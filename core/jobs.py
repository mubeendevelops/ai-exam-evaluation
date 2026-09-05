"""core/jobs.py — the evaluation_jobs queue, as a library.

Sits with the rest of core/ rather than inside api/ for the reason every other
module here does: the API enqueues jobs, a standalone worker process claims
them, and eventually a CLI will want to enqueue one too. All three call these
functions; none of them re-derives the SQL. (CLAUDE_CONTEXT.md §3 — "shared
library, imported by every script".)

WHY POSTGRES AND NOT REDIS — see migrations/014_evaluation_jobs.sql's header
for the full argument. Short version: Redis is named in the design doc and
implemented nowhere (§2, §10), it has no RLS so tenant isolation would have to
be hand-rolled a second time, and a job's state change cannot commit
atomically with the evaluation_results row it produces if the two live in
different stores.

CONNECTION / RLS CONTRACT (same as core/plugins/persistence.py): the CALLER
owns the connection and MUST have already set the RLS context on the
transaction — `SET LOCAL app.current_college_id` for a tenant request (the API
path, via api/deps/db.py::get_tenant_conn) or `SET LOCAL app.is_platform_admin`
for a system job (the worker, which claims across all tenants). Nothing here
sets it. evaluation_jobs is RLS-protected and fails closed SILENTLY
(CLAUDE_CONTEXT.md §6), so every read below that must have matched treats zero
rows as loud rather than as "no data".

THE EXPLICIT college_id PREDICATE, and why it is not redundant: get_job() takes
a college_id and puts it in the WHERE clause even though the RLS policy already
filters on exactly that column. Postgres exempts SUPERUSER and BYPASSRLS roles
from row-level security entirely — FORCE ROW LEVEL SECURITY closes the
table-owner loophole, not that one — so with PGUSER=postgres (the
.env.example default, and most local setups) migration 003's and 014's policies
are INERT. Under such a role the predicate here is the only thing isolating
tenants. Belt and braces; do not remove the belt because the braces exist,
particularly when the braces are currently not fastened. See api/README.md.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterable

from psycopg2.extras import Json

#: Columns every read below returns, in one place so the API's response model
#: and the worker see the same shape.
JOB_COLUMNS = (
    "job_id", "college_id", "job_type", "status", "payload", "result",
    "error", "attempts", "created_at", "started_at", "finished_at", "progress",
)

_SELECT_COLUMNS = ", ".join(JOB_COLUMNS)

TERMINAL_STATUSES = frozenset({"succeeded", "failed"})


class JobNotFoundError(LookupError):
    """A job lookup that must have matched returned zero rows.

    Its own type because, on an RLS-protected table, "zero rows" has three
    very different causes — the id does not exist, the id belongs to another
    tenant, or the transaction has no RLS context at all (CLAUDE_CONTEXT.md
    §6). The API deliberately collapses the first two into one 404 so it
    cannot be used to probe for other colleges' job ids; the third is a bug
    and must never be reported as 404. api/routers/jobs.py does that mapping.
    """


def _row_to_dict(row) -> dict[str, Any]:
    job = dict(zip(JOB_COLUMNS, row))
    job["job_id"] = str(job["job_id"])
    job["college_id"] = str(job["college_id"])
    return job


def enqueue_job(
    cur,
    *,
    college_id,
    job_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inserts one queued job and returns it as a dict.

    Does NOT commit — the caller's transaction owns that. This matters more
    than it looks: the API enqueues a job in the same transaction that
    validates the upload, so a failure anywhere in the request leaves no
    orphan job pointing at a file nobody will ever evaluate.

    college_id is passed explicitly rather than read from the RLS GUC, even
    though the policy's WITH CHECK will reject a mismatch. An INSERT that
    silently took its tenant from ambient session state is exactly the kind of
    thing that writes to the wrong college when a worker later reuses this
    function under a platform-admin context, where WITH CHECK permits
    anything.
    """
    if not job_type or not job_type.strip():
        raise ValueError("job_type is required and must be non-empty.")

    cur.execute(
        f"""
        INSERT INTO evaluation_jobs (college_id, job_type, payload)
        VALUES (%s, %s, %s)
        RETURNING {_SELECT_COLUMNS}
        """,
        (str(college_id), job_type.strip(), Json(payload or {})),
    )
    return _row_to_dict(cur.fetchone())


def get_job(cur, *, job_id, college_id) -> dict[str, Any] | None:
    """Reads one job scoped to one college. Returns None if it isn't there.

    Returns None rather than raising because the caller (the API) has to
    answer 404 for BOTH "no such job" and "another college's job", and telling
    those apart at this layer would only tempt someone to report the
    difference — which is an existence oracle over other tenants' job ids.

    See the module docstring for why college_id is in the WHERE clause and not
    left to RLS.
    """
    cur.execute(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM evaluation_jobs
        WHERE job_id = %s AND college_id = %s
        """,
        (str(job_id), str(college_id)),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def claim_next_job(
    cur,
    *,
    job_types: Iterable[str] | None = None,
    college_id=None,
) -> dict[str, Any] | None:
    """Atomically claims the oldest queued job, or returns None if there is
    none available. THE core of the queue.

    `FOR UPDATE SKIP LOCKED` is what makes this safe to run from N workers at
    once: the row-level lock taken by one worker's uncommitted transaction
    makes every other worker's subquery SKIP that row rather than block on it.
    Without SKIP LOCKED, ten workers would serialize behind the same row and
    then nine of them would find it already 'running'; without FOR UPDATE they
    would all claim it.

    The whole claim is ONE statement on purpose. A read-then-update pair —
    even inside a transaction — has a window in which two workers both saw the
    row as queued, and closing that window is what the subquery's lock does.

    Returns the job as it now stands: status='running', started_at set,
    attempts incremented.

    TRANSACTION LIFETIME MATTERS. The row stays locked until the caller
    commits, so the caller must COMMIT THE CLAIM before doing the actual work,
    not after. scripts/run_job_worker.py does exactly that: claim → commit →
    work → new transaction → mark terminal. Holding the lock across a
    minutes-long evaluation would idle every other worker against this row and
    keep a transaction open for the duration, which is how a queue table turns
    into a bloat problem.

    `attempts` is incremented HERE, on the claim, so a job that is claimed and
    then dies without finishing still records that it was tried — the one
    signal that separates "poisonous job" from "no worker ever ran".
    """
    types = list(job_types) if job_types is not None else None
    if types is not None and not types:
        raise ValueError(
            "job_types was given as an empty collection, which would match "
            "nothing and silently claim no work forever. Pass None to accept "
            "every job type."
        )

    cur.execute(
        f"""
        UPDATE evaluation_jobs
        SET status      = 'running',
            started_at  = now(),
            attempts    = attempts + 1
        WHERE job_id = (
            SELECT job_id
            FROM evaluation_jobs
            WHERE status = 'queued'
              AND (%(types)s::text[] IS NULL OR job_type = ANY(%(types)s::text[]))
              AND (%(college)s::uuid IS NULL OR college_id = %(college)s::uuid)
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        RETURNING {_SELECT_COLUMNS}
        """,
        {
            "types": types,
            "college": str(college_id) if college_id is not None else None,
        },
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def mark_succeeded(cur, *, job_id, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Moves a running job to 'succeeded' with its report.

    `result` is the run REPORT (counts, per-question summary, failures) — not
    the scores. Scores go to the append-only evaluation_results ledger
    (PROJECT_CONTEXT.md rule 2); duplicating them here would create a second,
    diverging record of what a student was given.
    """
    cur.execute(
        f"""
        UPDATE evaluation_jobs
        SET status = 'succeeded', result = %s, error = NULL, finished_at = now()
        WHERE job_id = %s AND status = 'running'
        RETURNING {_SELECT_COLUMNS}
        """,
        (Json(result) if result is not None else None, str(job_id)),
    )
    return _require_row(cur, job_id, "mark_succeeded")


def mark_failed(cur, *, job_id, error: str) -> dict[str, Any]:
    """Moves a running job to 'failed' with a non-empty reason.

    The DB refuses a failed row with a NULL error
    (evaluation_jobs_failed_requires_error), and this refuses a blank one, for
    the same reason: a failure nobody can read is only marginally better than
    a job that vanished.
    """
    if not error or not error.strip():
        raise ValueError(
            "mark_failed() requires a non-empty error. A failed job with no "
            "reason is unactionable, and the DB CHECK constraint "
            "evaluation_jobs_failed_requires_error rejects it anyway."
        )

    cur.execute(
        f"""
        UPDATE evaluation_jobs
        SET status = 'failed', error = %s, finished_at = now()
        WHERE job_id = %s AND status = 'running'
        RETURNING {_SELECT_COLUMNS}
        """,
        (error.strip(), str(job_id)),
    )
    return _require_row(cur, job_id, "mark_failed")


def requeue(cur, *, job_id, error: str | None = None) -> dict[str, Any]:
    """Returns a job to 'queued' for another attempt, keeping `attempts` and
    the last error so the history isn't erased by the retry.

    Clears started_at/finished_at because the state-machine CHECK constraints
    require a queued row to have neither; `attempts` is NOT reset — that is the
    whole record of how many times this has gone wrong.
    """
    cur.execute(
        f"""
        UPDATE evaluation_jobs
        SET status = 'queued', started_at = NULL, finished_at = NULL,
            result = NULL, error = COALESCE(%s, error)
        WHERE job_id = %s
        RETURNING {_SELECT_COLUMNS}
        """,
        (error, str(job_id)),
    )
    return _require_row(cur, job_id, "requeue")


def _require_row(cur, job_id, operation: str) -> dict[str, Any]:
    """Turns a zero-row UPDATE into a loud, specific failure.

    Under RLS a zero-row result means one of: the job does not exist, it
    belongs to another tenant, this transaction has no RLS context at all, or
    the job was not in the state the UPDATE required (someone else already
    finished it). All four are bugs at this layer — the worker just claimed
    this row — and all four are invisible if the UPDATE's rowcount is ignored,
    which is the normal way this kind of code fails.
    """
    row = cur.fetchone()
    if row is None:
        raise JobNotFoundError(
            f"{operation}({job_id!r}) updated zero rows. evaluation_jobs is "
            f"RLS-protected and fails closed SILENTLY (CLAUDE_CONTEXT.md §6), "
            f"so this is one of: no such job; the job belongs to another "
            f"college; this transaction never ran SET LOCAL "
            f"app.current_college_id / app.is_platform_admin; or the job was "
            f"no longer in the status this transition requires (another worker "
            f"got there first). Check the RLS context first — it is the cause "
            f"that produces no other symptom."
        )
    return _row_to_dict(row)


def set_progress(cur, *, job_id, stage: str, percent: float | None = None,
                 message: str = "", counts: dict[str, Any] | None = None) -> None:
    """Records what a running job is currently doing (migration 015).

    Writes to the `progress` column, NEVER to `payload`: payload is the job's
    immutable input, and a worker that overwrites its own input destroys the
    record of what it was asked to do — and with it any chance of a faithful
    retry.

    Fire-and-forget by design. It is called from inside a worker's run and a
    failure to record progress must never fail the job itself, so it returns
    nothing and the caller commits it on its own short transaction. A job
    whose progress reporting is broken should still produce a correct result.

    Not guarded on status: a progress write races the terminal update only in
    the worker's own process, and api/schemas/jobs.py ignores stored progress
    for any non-running job precisely so a stale mid-run snapshot cannot be
    reported as though the job were still there.
    """
    cur.execute(
        "UPDATE evaluation_jobs SET progress = %s WHERE job_id = %s",
        (
            Json({
                "stage": stage,
                "percent": percent,
                "message": message,
                "counts": counts or {},
            }),
            str(job_id),
        ),
    )
