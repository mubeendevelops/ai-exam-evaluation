#!/usr/bin/env python3
"""run_job_worker.py — claims and runs evaluation_jobs rows.

    # one pass, do nothing real, don't write:
    python scripts/run_job_worker.py --once --stub --dry-run

    # drain everything queued, stub execution:
    python scripts/run_job_worker.py --drain --stub

    # long-running worker (what you'd actually deploy):
    python scripts/run_job_worker.py --stub

THE WORKER NOW RUNS THE REAL PIPELINE. A `booklet_eval` job loads the
booklet's persisted regions and hands them to core/booklet_evaluator.py
through api/services/evaluation.py, which appends one evaluation_results row
per question. `--stub` and the job payload's own stub flags select the
plugins' stub paths so the whole loop can run with no models and no network.

INGESTION IS A SEPARATE JOB TYPE, not a phase of evaluation. A `booklet_ingest`
job runs §7C's pass — rasterize, segment, classify, assign, persist — through
api/services/ingestion.py -> core/booklet_pipeline.py, and then CHAINS into the
`booklet_eval` job for the same booklet. Two job types rather than one because
ingestion writes a student's answer_blocks and evaluation only reads them:
folding them together would rewrite those blocks on every re-score, and there
is no version history for them (PROJECT_CONTEXT.md §7 open decision 2). A
booklet that already has regions is NOT re-ingested — the ingest job checks and
skips, and POST /api/v1/evaluate skips queuing one at all.

`booklet_eval` still fails, loudly and by name, on a booklet with no persisted
regions. That used to be the normal path (someone had to run
scripts/ingest_booklet.py first); it is now a real failure, and it still must
never succeed with an empty report that reads like a student who wrote
nothing.

RLS: this is a SYSTEM-LEVEL job, not a tenant request, so every transaction
runs as platform admin — the same posture evaluate_pending.py,
evaluate_pending_diagrams.py and core/booklet_persist.py already take
(CLAUDE_CONTEXT.md §6). It has to be: a worker serves every college, and there
is no single current_college_id it could set. It reuses
api/deps/db.py::set_admin_context rather than re-typing the SET LOCAL, so the
worker and the API cannot drift on how the context is established or on the
read-back check that proves it stuck.

TRANSACTION SHAPE — the part that is easy to get wrong:

    txn 1:  claim (SELECT ... FOR UPDATE SKIP LOCKED, status -> running)
            COMMIT                      <- releases the row lock
    (no txn) do the work
    txn 2:  mark succeeded / failed
            COMMIT

The claim is committed BEFORE the work starts. Holding the claiming
transaction open across a minutes-long evaluation would block nothing (SKIP
LOCKED means other workers move on) but would keep a transaction open for
minutes, pinning the oldest xmin and preventing vacuum across the whole
database. Commit early; the 'running' status, not the lock, is what marks the
job as taken.

CRASH SEMANTICS: a worker killed between the two transactions leaves the job
'running' forever, with attempts already incremented. That is a known,
deliberate gap — reaping stalled jobs needs a heartbeat/lease column and a
reaper, which is real design work and not today's task. `attempts` and
`started_at` are already on the row so the reaper has what it needs. Until it
exists, `--requeue-stalled` below is the manual lever.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.services.evaluation as evaluation_service
import api.services.ingestion as ingestion_service
import core.db
import core.jobs
from api.deps.db import set_admin_context
from api.logging_config import bind_request_id, configure_logging

#: JSON, request-id-stamped, same shape the API's own log lines use — see
#: api/logging_config.py. The existing print()s to stderr stay: they are the
#: operator-facing progress feed this script has always had, and several
#: (the --requeue-stalled / --drain summaries) are JSON on STDOUT that other
#: tooling may already parse. These log lines are additive, for correlating
#: one job's run with the HTTP request that queued it via `request_id`.
log = logging.getLogger("worker")

#: Job types this worker handles. A job of any other type is left alone rather
#: than claimed and failed — a queue shared by several worker kinds must not
#: have one of them eat and reject everyone else's work.
HANDLED_JOB_TYPES = [
    ingestion_service.JOB_TYPE_BOOKLET_INGEST,
    evaluation_service.JOB_TYPE_BOOKLET_EVAL,
]

_shutdown = False


def _install_signal_handlers() -> None:
    """SIGINT/SIGTERM set a flag instead of raising.

    A worker killed mid-job should finish the job it holds and then stop, not
    abandon a 'running' row that nothing will reap (see the module docstring's
    crash semantics). Between jobs the flag is checked and the loop exits.
    """
    def handle(signum, _frame):
        global _shutdown
        _shutdown = True
        print(
            f"[worker] signal {signum} received — finishing the current job, "
            f"then stopping.",
            file=sys.stderr,
        )

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def _admin_transaction():
    """Opens a connection with the platform-admin RLS context set.

    Not a context manager over the whole worker: each transaction is short and
    separate on purpose (module docstring). Callers commit/rollback and close.
    """
    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            set_admin_context(cur)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    return conn


def claim_one(job_types=None):
    """Claims one job in its own committed transaction. Returns it or None."""
    conn = _admin_transaction()
    try:
        with conn.cursor() as cur:
            job = core.jobs.claim_next_job(cur, job_types=job_types)
        conn.commit()          # release the row lock before doing any work
        return job
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish(job_id, *, result=None, error=None, dry_run: bool = False) -> None:
    """Moves a claimed job to its terminal state in a fresh transaction."""
    conn = _admin_transaction()
    try:
        with conn.cursor() as cur:
            if error is not None:
                core.jobs.mark_failed(cur, job_id=job_id, error=error)
            else:
                core.jobs.mark_succeeded(cur, job_id=job_id, result=result)
        core.db.end_transaction(conn, dry_run=dry_run)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _progress_reporter(job_id):
    """Returns an on_progress(stage, percent, message, **counts) callback that
    writes evaluation_jobs.progress in its OWN short transaction.

    Its own transaction, and its own connection, for two reasons. The job's
    work runs outside any transaction (see the module docstring), so there is
    none to piggyback on. And a progress write must be visible to
    GET /api/v1/jobs/{id} IMMEDIATELY — progress committed only at the end
    would report nothing during the minutes it was supposed to cover, which is
    the entire problem it exists to solve.

    Every failure is swallowed. Progress is a convenience; a booklet whose
    progress reporting breaks must still be evaluated and still produce a
    correct result. The failure is printed so it is not silent to an operator,
    just not fatal to the student's marks.
    """
    def report(stage: str, percent=None, message: str = "", **counts) -> None:
        try:
            conn = _admin_transaction()
            try:
                with conn.cursor() as cur:
                    core.jobs.set_progress(cur, job_id=job_id, stage=stage,
                                           percent=percent, message=message,
                                           counts=counts)
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:                # noqa: BLE001 — never fatal
            print(f"[worker] progress write failed ({stage}): {exc}", file=sys.stderr)

    return report


def _run_job(job: dict, *, stub: bool, stub_seconds: float, dry_run: bool = False) -> dict:
    """Does the actual work for one job. Returns the run report."""
    if job["job_type"] == evaluation_service.JOB_TYPE_BOOKLET_EVAL:
        return _run_booklet_eval(job, stub=stub, dry_run=dry_run)

    if job["job_type"] == ingestion_service.JOB_TYPE_BOOKLET_INGEST:
        return _run_booklet_ingest(job, stub=stub, dry_run=dry_run)

    raise ValueError(
        f"No handler for job_type {job['job_type']!r}. This worker handles "
        f"{HANDLED_JOB_TYPES}; --job-type should have kept it from claiming this "
        f"row at all, so reaching here means the filter and the dispatch have "
        f"drifted apart."
    )


def _run_booklet_eval(job: dict, *, stub: bool, dry_run: bool) -> dict:
    """Loads the booklet's persisted regions, scores them, appends the ledger
    rows. All of the actual work happens in core/booklet_evaluator.py.

    The connection runs as PLATFORM ADMIN, like every other system-level job in
    this repo (§6). That is correct for a worker serving every college, and it
    is also why the payload's college_id is not used to scope the query: the
    booklet is addressed by (student_id, exam_id, source_scan_url), all three
    of which were resolved tenant-scoped by POST /api/v1/evaluate before the
    job was ever created.

    `stub` is the OR of the CLI flag and the job's own payload option, so a
    job queued in stub mode stays stubbed no matter which worker picks it up —
    the alternative is a job that scores for real because the worker that
    happened to claim it was not started with --stub.
    """
    payload = job["payload"]
    options = payload.get("options") or {}
    effective_stub = stub or bool(options.get("stub"))

    conn = _admin_transaction()
    try:
        report = evaluation_service.run_booklet_evaluation(
            conn,
            {**payload, "options": {**options, "stub": effective_stub}},
            on_progress=_progress_reporter(job["job_id"]),
            persist=True,
            dry_run=dry_run,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    # The report is large (every region, every signal, every failure). What
    # goes into evaluation_jobs.result is the SUMMARY — the per-question
    # detail is already in the evaluation_results ledger, which is the record
    # of account, and duplicating it into a JSONB column would create a second
    # copy that can disagree with it.
    return _summarize(report, stub=effective_stub, dry_run=dry_run)


def _run_booklet_ingest(job: dict, *, stub: bool, dry_run: bool) -> dict:
    """Segments the booklet into answer_blocks, then queues its evaluation.

    Platform admin, like every system-level job here (§6) and like
    scripts/ingest_booklet.py, which does the same thing for the same reason:
    a worker serves every college and has no single current_college_id.

    THE CHAIN IS A SEPARATE TRANSACTION from the ingestion, and it has to be:
    core/booklet_persist.py::persist_regions owns its own commit (its
    documented contract), so by the time this returns the blocks are already
    durable. Enqueuing the evaluation is therefore its own short transaction
    afterwards — and it is IDEMPOTENT (enqueue_chained_evaluation matches an
    existing job on the three ids that identify the booklet), because a worker
    killed between here and finish() leaves the ingest job 'running' and a
    --requeue-stalled would run it again. Re-running is then cheap and safe on
    both halves: the ingestion skips an already-ingested booklet, and the
    chain finds the evaluation job it already queued.

    A DRY RUN CHAINS NOTHING. persist_regions rolled its writes back, so there
    are no regions for an evaluation to read; queuing one would produce a job
    guaranteed to fail with NoRegionsError.
    """
    payload = job["payload"]
    options = payload.get("options") or {}
    effective_stub = stub or bool(options.get("stub"))

    conn = _admin_transaction()
    try:
        report = ingestion_service.run_booklet_ingest(
            conn,
            {**payload, "options": {**options, "stub": effective_stub}},
            on_progress=_progress_reporter(job["job_id"]),
            dry_run=dry_run,
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    evaluation_job = None
    if not dry_run:
        conn = _admin_transaction()
        try:
            with conn.cursor() as cur:
                evaluation_job = ingestion_service.enqueue_chained_evaluation(
                    cur, college_id=job["college_id"], payload=payload)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    return _summarize_ingestion(report, evaluation_job,
                                stub=effective_stub, dry_run=dry_run)


def _summarize_ingestion(report: dict, evaluation_job: dict | None, *,
                         stub: bool, dry_run: bool) -> dict:
    """The ingest job's result: what was written, what was not, and what next.

    Every region the ingestion refused to place is carried here with its
    reason. That list is the ONLY surface those regions have — an unassigned
    region is persisted nowhere, because answer_blocks.answer_id is NOT NULL
    and an orphan region has no answer to hang off (§7C). Dropping it from the
    summary would make "the segmenter found 12 regions and wrote 9" invisible,
    which is exactly the number a teacher reviewing a booklet needs.
    """
    persisted = report.get("persisted") or {}
    regions = report.get("regions") or []
    unplaced = [
        {
            "page_number": item["page_number"],
            "bbox": item["bbox"],
            "block_type": item["block_type"],
            "assigned_question": item["assigned_question"],
            "reason": item["skip_reason"],
        }
        for item in (persisted.get("results") or [])
        if item.get("skip_reason")
    ]

    return {
        "stub": stub,
        "dry_run": dry_run,
        # None until the chain runs; the id a client polls next.
        "evaluation_job_id": evaluation_job["job_id"] if evaluation_job else None,
        # Set when this booklet already had regions: nothing was re-segmented
        # and nothing was rewritten. NOT an error, and NOT "nothing to score".
        "skipped": report.get("skipped"),
        "source_scan_url": report.get("source_pdf_url"),
        "storage": report.get("storage"),
        "counts": {
            "pages": report.get("page_count"),
            "regions": len(regions),
            "markers": len(report.get("markers") or []),
            "blocks_written": persisted.get("persisted"),
            "regions_skipped": persisted.get("skipped"),
            "answers_touched": len(persisted.get("answers") or {}),
            "flagged_for_review": sum(1 for r in regions if r.get("needs_review")),
        },
        "unplaced_regions": unplaced,
        "detail": (
            "Region provenance (page, bbox, classification, confidence) is on "
            "the answer_blocks rows themselves — migration 013. Unplaced "
            "regions are persisted NOWHERE and appear only in this list."
        ),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _summarize(report: dict, *, stub: bool, dry_run: bool) -> dict:
    """The job result: counts, totals, and where to find the detail."""
    questions = report.get("questions", [])
    return {
        "stub": stub,
        "dry_run": dry_run,
        "booklet": report.get("booklet", {}),
        "totals": report.get("totals", {}),
        "confidence": report.get("confidence", {}),
        "persistence": report.get("persistence", {}),
        "counts": {
            "questions": len(questions),
            "scored": sum(1 for q in questions if q.get("scored")),
            # Unscored is NOT zero-scored — §7D keeps them apart on purpose,
            # so the summary does too.
            "unscored": sum(1 for q in questions if not q.get("scored")),
            # regions_* live under totals, not at the report root — read from
            # where core/booklet_evaluator.aggregate actually writes them.
            "regions": report.get("totals", {}).get("regions_total"),
            "regions_evaluated": report.get("totals", {}).get("regions_evaluated"),
            "regions_failed": report.get("totals", {}).get("regions_failed"),
            "failures": len(report.get("failures", [])),
        },
        "review_queue": report.get("review_queue", {}),
        "failures": report.get("failures", []),
        "detail": (
            "Per-question scores are in the evaluation_results ledger — fetch "
            "them with GET /api/v1/results/{answer_id}. They are not duplicated "
            "here: a second copy in JSONB can disagree with the record of "
            "account."
        ),
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def process_one(*, stub: bool, stub_seconds: float, dry_run: bool, job_types) -> dict | None:
    """Claims and runs at most one job. Returns the job dict, or None if the
    queue was empty."""
    job = claim_one(job_types=job_types)
    if job is None:
        return None

    # Read back from the payload the API wrote it into (POST /evaluate, via
    # api/services/ingestion.py / evaluation.py) — see api/logging_config.py.
    # A job with no request_id (queued by a CLI script, or predating this
    # pass) binds None, which the logging filter renders as "-" rather than
    # raising on a job with nothing to correlate against.
    request_id = (job.get("payload") or {}).get("request_id")
    with bind_request_id(request_id):
        print(
            f"[worker] claimed {job['job_id']} type={job['job_type']} "
            f"college={job['college_id']} attempt={job['attempts']}",
            file=sys.stderr,
        )
        log.info(
            "claimed job", extra={
                "job_id": str(job["job_id"]), "job_type": job["job_type"],
                "college_id": str(job["college_id"]), "attempt": job["attempts"],
            },
        )

        try:
            result = _run_job(job, stub=stub, stub_seconds=stub_seconds, dry_run=dry_run)
        except Exception as exc:
            # Every failure is recorded ON THE JOB, with its traceback, rather
            # than only logged. The client polling GET /jobs/{id} has no
            # access to this process's stderr, and "the job just stayed
            # running" is the least debuggable outcome available.
            detail = f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
            finish(job["job_id"], error=detail, dry_run=dry_run)
            print(f"[worker] FAILED {job['job_id']}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            log.error(
                "job failed", extra={"job_id": str(job["job_id"]), "error": str(exc)},
            )
            return job

        finish(job["job_id"], result=result, dry_run=dry_run)
        print(f"[worker] succeeded {job['job_id']}", file=sys.stderr)
        log.info("job succeeded", extra={"job_id": str(job["job_id"])})
        return job


def requeue_stalled(older_than_minutes: int, dry_run: bool) -> int:
    """Manual reaper: returns jobs stuck in 'running' to the queue.

    The stopgap for the crash semantics in the module docstring, kept explicit
    and opt-in rather than automatic — an automatic reaper without a heartbeat
    would race a slow-but-healthy job and run it twice.
    """
    conn = _admin_transaction()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id FROM evaluation_jobs
                WHERE status = 'running'
                  AND started_at < now() - make_interval(mins => %s)
                """,
                (older_than_minutes,),
            )
            stalled = [row[0] for row in cur.fetchall()]
            for job_id in stalled:
                core.jobs.requeue(
                    cur,
                    job_id=job_id,
                    error=(
                        f"Requeued by --requeue-stalled: still 'running' more "
                        f"than {older_than_minutes} min after being claimed, "
                        f"so the worker holding it is presumed dead."
                    ),
                )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return len(stalled)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--once", action="store_true",
                        help="Claim and run at most one job, then exit.")
    parser.add_argument("--drain", action="store_true",
                        help="Run until the queue is empty, then exit.")
    parser.add_argument("--stub", action="store_true",
                        help="Run the plugins' stub paths — no models, no Groq. "
                             "OR-ed with the job payload's own stub option, so a "
                             "job queued in stub mode stays stubbed. NOTE: a stub "
                             "score is FABRICATED and is still appended to the "
                             "append-only ledger, so never point a --stub worker "
                             "at a production queue.")
    parser.add_argument("--stub-seconds", type=float, default=0.5,
                        help="Unused by the real pipeline; kept for the "
                             "no-op job types that still sleep.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Roll back the terminal-state write instead of committing. "
                             "The CLAIM still commits — otherwise there is nothing to "
                             "run and the flag would test nothing.")
    parser.add_argument("--poll-interval", type=float, default=2.0,
                        help="Seconds to sleep when the queue is empty (default 2.0).")
    parser.add_argument("--max-jobs", type=int, default=0,
                        help="Stop after this many jobs. 0 = unlimited.")
    parser.add_argument("--job-type", action="append", dest="job_types",
                        help="Only claim these job types. Repeatable. "
                             f"Default: {HANDLED_JOB_TYPES}.")
    parser.add_argument("--requeue-stalled", type=int, metavar="MINUTES",
                        help="Return jobs 'running' for longer than MINUTES to the "
                             "queue, then exit. See the module docstring.")
    args = parser.parse_args()

    if args.requeue_stalled is not None:
        count = requeue_stalled(args.requeue_stalled, args.dry_run)
        print(json.dumps({"requeued": count, "dry_run": args.dry_run}))
        return 0

    job_types = args.job_types or HANDLED_JOB_TYPES
    _install_signal_handlers()

    processed = 0
    while not _shutdown:
        job = process_one(
            stub=args.stub,
            stub_seconds=args.stub_seconds,
            dry_run=args.dry_run,
            job_types=job_types,
        )

        if job is not None:
            processed += 1
            if args.max_jobs and processed >= args.max_jobs:
                break
            if args.once:
                break
            continue

        # Queue empty.
        if args.once or args.drain:
            break
        time.sleep(args.poll_interval)

    print(json.dumps({"processed": processed}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
