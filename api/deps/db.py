"""api/deps/db.py — the ONLY way an endpoint gets a database connection.

════════════════════════════════════════════════════════════════════════════
WHY NO ENDPOINT MAY EVER CALL core.db.get_connection() DIRECTLY
════════════════════════════════════════════════════════════════════════════

The answer-schema tables (students, exams, answers, answer_blocks,
evaluation_results, answer_reviews, answer_status_history) are RLS-protected
with FORCE ROW LEVEL SECURITY (migration 003). Their tenant policy is:

    USING (college_id = current_setting('app.current_college_id', true)::uuid)

`current_setting(..., true)` is the missing_ok form: if the GUC was never
set it returns NULL, the comparison is NULL, and the policy matches nothing.

So a connection with no tenant context DOES NOT ERROR. It returns zero rows.

    SELECT * FROM answers WHERE answer_id = '<a real answer>';   -->  []

which reads, at every call site, as "that answer doesn't exist." An endpoint
that opens its own connection therefore doesn't crash in review or in tests —
it 404s, or returns an empty list, or writes an orphan row, and looks
completely fine while being silently blind to all of the tenant's data. That
is the single worst failure mode available in this codebase, because nothing
reports it. There is no log line, no exception, no metric.

Worse, the write side has the same shape: the policy's WITH CHECK clause
means an INSERT under no context is rejected, but an INSERT under the WRONG
context succeeds and writes another college's row.

Hence: every DB-touching endpoint declares `Depends(get_tenant_conn)` or
`Depends(get_admin_conn)`. These two functions are the only places in api/
that call core.db.get_connection(). If, under deadline pressure, you are
about to "just quickly" open a connection inside a router because the
dependency is awkward to thread through — that is precisely the change this
comment exists to stop. Fix the dependency instead; the failure you would be
introducing is invisible.

(Auditable: `grep -rn --include=*.py "get_connection" api/` must only ever match
this file.)

════════════════════════════════════════════════════════════════════════════
TRANSACTION SHAPE
════════════════════════════════════════════════════════════════════════════

`SET LOCAL` is scoped to the enclosing transaction and is reverted at COMMIT
or ROLLBACK. psycopg2 opens a transaction implicitly on the first statement
(autocommit is off), so the SET LOCAL below and everything the endpoint does
afterwards are the same transaction, and the tenant context cannot leak into
a later request. `SET` (without LOCAL) would be session-scoped and would leak
if connections were ever pooled — do not "simplify" it to that.

One connection per request, committed on a clean response and rolled back on
any exception. There is no pooling yet (core/db.py::get_connection is a
stateless factory); if per-request connection cost is ever measured to
matter, a pool goes behind THIS interface, not around it.
"""
from __future__ import annotations

import uuid
from typing import Iterator

import psycopg2.extensions
from fastapi import Depends

import core.db
from api.deps.identity import CurrentUser, get_current_user


class TenantContextError(RuntimeError):
    """Raised when a tenant-scoped connection was requested without a usable
    college_id, or when the RLS GUC did not take effect.

    Its own exception type, and a hard failure rather than a degraded
    connection, precisely because the degraded connection is indistinguishable
    from "no data" (see the module docstring). Never catch this and continue
    with the connection; there is nothing to continue with.

    Deliberately NOT an HTTPException: a missing credential is caught upstream
    in api/deps/identity.py and answered with 401. Reaching this class means a
    programming error inside the API — the generic handler in api/main.py
    turns it into a 500, which is the correct answer for "this server is
    misconfigured", not something to paper over per-endpoint.
    """


def _require_college_id(college_id) -> str:
    """Validates that a college_id is actually present and UUID-shaped.

    An empty string would be accepted by `SET LOCAL` without complaint and
    then cast-fail or match nothing inside the RLS policy — i.e. it would
    look exactly like "no data". Fail here, loudly, instead.
    """
    if college_id is None:
        raise TenantContextError(
            "get_tenant_conn() was asked for a tenant-scoped connection but "
            "college_id is None. Refusing to hand back a connection with no "
            "RLS context: answer-schema tables fail closed SILENTLY "
            "(CLAUDE_CONTEXT.md §6), so such a connection would return zero "
            "rows from every query and read as 'no data' rather than as this "
            "bug. Resolve the caller's tenant (api/deps/identity.py) first, "
            "or use get_admin_conn() if this really is a cross-tenant "
            "system operation."
        )

    text = str(college_id).strip()
    if not text:
        raise TenantContextError(
            f"get_tenant_conn() got a blank college_id ({college_id!r}). See "
            f"the None case above — a blank GUC fails closed just as silently "
            f"as an unset one."
        )

    try:
        uuid.UUID(text)
    except ValueError:
        raise TenantContextError(
            f"college_id {text!r} is not a valid UUID. The RLS tenant policy "
            f"casts app.current_college_id to uuid, so this would raise "
            f"inside the policy on the first query against an answer-schema "
            f"table — a confusing place to discover a bad id."
        )
    return text


def _verify_guc(cur, name: str, expected: str) -> None:
    """Reads the GUC back after setting it.

    Cheap, and it converts the one silent failure mode this module exists to
    prevent into a loud one. If `SET LOCAL` were ever run outside a
    transaction, or against a connection someone left in autocommit, the
    setting would not stick — and the resulting connection would once again be
    a blind one that reports "no data".
    """
    cur.execute("SELECT current_setting(%s, true)", (name,))
    (actual,) = cur.fetchone()
    if actual != expected:
        raise TenantContextError(
            f"Set {name} = {expected!r} but reading it back gave {actual!r}. "
            f"The RLS context did not take effect, so this connection would "
            f"silently return zero rows from every RLS-protected table. "
            f"Check that the connection is not in autocommit mode — SET LOCAL "
            f"only persists inside a transaction."
        )


def set_tenant_context(cur, college_id) -> str:
    """Applies `SET LOCAL app.current_college_id` on an open cursor and
    verifies it stuck. Returns the college_id actually set.

    Exposed separately from the dependency so background/worker code (which
    has no FastAPI request to hang a dependency off) can reuse the exact same
    validation instead of hand-rolling a second, subtly-different version of
    it.
    """
    text = _require_college_id(college_id)
    # psycopg2 interpolates client-side (PostgreSQL's SET takes no bind
    # parameters), so the %s is still doing the quoting/escaping here — and
    # _require_college_id has already proven the value is a bare UUID.
    cur.execute("SET LOCAL app.current_college_id = %s", (text,))
    _verify_guc(cur, "app.current_college_id", text)
    return text


def set_admin_context(cur) -> None:
    """Applies `SET LOCAL app.is_platform_admin = 'true'` and verifies it.

    Same bypass the CLI system jobs use (scripts/evaluate_pending.py,
    core/booklet_persist.py, the reset/seed scripts) — see CLAUDE_CONTEXT.md
    §6.
    """
    cur.execute("SET LOCAL app.is_platform_admin = 'true'")
    _verify_guc(cur, "app.is_platform_admin", "true")


def _open(configure) -> Iterator[psycopg2.extensions.connection]:
    """Shared connection lifecycle: open → configure RLS → yield → commit,
    rolling back and closing on any failure.

    If `configure` raises (a missing/invalid tenant context), the connection
    is closed and NOTHING is yielded — the endpoint never runs. That is the
    point: there is no code path in this module that produces a connection
    without an RLS context.
    """
    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            configure(cur)
    except Exception:
        conn.rollback()
        conn.close()
        raise

    try:
        yield conn
    except Exception:
        conn.rollback()
        conn.close()
        raise
    else:
        conn.commit()
        conn.close()


def get_tenant_conn(
    user: CurrentUser = Depends(get_current_user),
) -> Iterator[psycopg2.extensions.connection]:
    """FastAPI dependency: a connection scoped to the caller's college by RLS.

    This is the default for every business endpoint. The tenant comes from the
    authenticated user and NEVER from a request body, query parameter, or path
    parameter — otherwise any caller could read any college's answers by
    typing a different UUID, and RLS would happily comply.

    Raises TenantContextError (→ 500) rather than yielding a context-less
    connection if the user has no college_id.
    """
    yield from _open(lambda cur: set_tenant_context(cur, user.college_id))


def get_admin_conn() -> Iterator[psycopg2.extensions.connection]:
    """FastAPI dependency: a cross-tenant connection for system/admin work.

    Sets `app.is_platform_admin` instead of `app.current_college_id`, which
    activates migration 003's permissive `platform_admin_bypass` policy and
    makes every college's rows visible on one connection.

    USE SPARINGLY AND NEVER FOR A TENANT REQUEST. This is the correct
    dependency for the system-level operations the CLI already runs this way
    (batch evaluation over all pending answers, booklet ingestion, platform
    reporting). It is the WRONG dependency for anything reached from a
    college's own session: under it, a mistaken or attacker-supplied id
    returns another college's data with no isolation left to stop it.

    It takes no `user` parameter on purpose — admin authorization does not
    exist yet (api/deps/identity.py always reports is_platform_admin=False),
    so no endpoint that a tenant can reach should be wired to this until it
    does.
    """
    yield from _open(set_admin_context)
