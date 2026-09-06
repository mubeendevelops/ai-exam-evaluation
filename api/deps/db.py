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
from api.deps.identity import CurrentUser, get_current_user, unknown_tenant_error


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


def _require_known_college(cur, college_id: str) -> None:
    """Refuses a well-formed college_id that names no usable tenant, with 401.

    ONE SELECT, and it runs BEFORE `SET LOCAL app.current_college_id`.
    `colleges` is the tenant table itself: it carries no college_id column and
    has no RLS policy (migration 003 §0), so this read is correct with or
    without a tenant context — and doing it first means a rejected request
    never establishes a context at all.

    WHY THIS IS NOT A TIDY-UP. Before this check, identity.py only PARSED the
    uuid, so any well-formed id established a tenant context that owns
    nothing. Reads then 404ed (correct, and indistinguishable from any other
    miss) but POST /api/v1/upload 500ed on
    `booklet_uploads_college_id_fkey` — the database catching, at the last
    possible moment, something the API never checked. A 500 is a loud failure
    that writes nothing, so the safety property held; it was still the wrong
    answer to "who are you?".

    `status <> 'active'` is refused for the same reason as a missing row.
    Migration 003 defines college_status as ('active', 'suspended'), and a
    suspended college is one the platform has deliberately switched off; it
    still owns rows, so admitting it would hand out a working tenant context
    for a tenant that is supposed to be dark. This is the ONE place that
    decision can be made once for every endpoint.

    Costs one indexed primary-key lookup per request, on a table with one row
    per college.
    """
    cur.execute("SELECT status FROM colleges WHERE college_id = %s", (college_id,))
    row = cur.fetchone()
    if row is None:
        raise unknown_tenant_error(
            college_id, reason="no college with that id exists")
    if row[0] != "active":
        raise unknown_tenant_error(
            college_id, reason=f"that college is {row[0]}, not active")


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
    """FastAPI dependency: a connection whose RLS context matches the CALLER'S
    ROLE. This is the default for every business endpoint.

    ROLE → SESSION VARIABLE. This mapping is the whole of the permission model
    that touches the database, and it lives here, once:

        teacher, admin  ->  SET LOCAL app.current_college_id = <user.college_id>
        platform_admin  ->  SET LOCAL app.is_platform_admin  = 'true'

    Both branches read the GUC back and raise TenantContextError if it did not
    take (`_verify_guc`), because a context that did not stick is invisible:
    every query returns zero rows and reads as "no data".

    The tenant comes from the authenticated user's signature-verified `cid`
    claim and NEVER from a request body, query parameter, or path parameter —
    otherwise any caller could read any college's answers by typing a
    different UUID, and RLS would happily comply.

    THE platform_admin BRANCH IS A CROSS-TENANT CONTEXT, and endpoints that
    pass `user.college_id` into an explicit predicate must not be reachable
    with it — a NULL college_id there matches nothing and answers 404 for data
    that exists. Those endpoints declare
    `api/deps/identity.py::require_college_user` alongside this dependency;
    that function's docstring is where the reasoning lives.

    Raises TenantContextError (→ 500) rather than yielding a context-less
    connection if a college-scoped user has no college_id, and HTTP 401 if the
    college_id is well-formed but names no active college
    (`_require_known_college`). Both happen before the connection is yielded,
    so an endpoint body never runs under a tenant that does not exist.
    """
    def configure(cur):
        if user.is_platform_admin:
            # No `_require_known_college` here: there is no college to check.
            # A platform_admin's account is verified at login, and its token
            # cannot carry a college at all (api/deps/identity.py refuses
            # one), so there is no id in play that could name a dead tenant.
            set_admin_context(cur)
            return

        college_id = _require_college_id(user.college_id)
        # Order matters: verify the tenant, THEN establish its context. A
        # rejected caller must never have had `app.current_college_id` set.
        _require_known_college(cur, college_id)
        set_tenant_context(cur, college_id)

    yield from _open(configure)


def get_auth_conn() -> Iterator[psycopg2.extensions.connection]:
    """FastAPI dependency: a connection with NO RLS context, for /auth only.

    ════════════════════════════════════════════════════════════════════════
    READ THIS BEFORE USING IT ANYWHERE ELSE. THE ANSWER IS: DO NOT.
    ════════════════════════════════════════════════════════════════════════

    Authentication is the one operation that CANNOT have a tenant context,
    because resolving the tenant is its OUTPUT, not its input. At the moment
    `POST /auth/login` reads the `users` row, nobody has been identified yet.

    The two obvious ways to give it a context are both wrong, and migration
    017 §3 says so at length:

      * `set_admin_context()` would flip the permissive platform_admin_bypass
        policy on all ELEVEN RLS-protected tables for the whole transaction —
        an UNAUTHENTICATED request briefly holding cross-tenant read on every
        answer in the platform. Absolutely not.
      * a tenant context cannot be set, because there is no tenant yet.

    So this connection carries neither, and it is safe ONLY because the
    database refuses to let it read anything: with no GUC set, every
    `tenant_isolation` policy matches nothing, and the login path reaches the
    two rows it needs exclusively through migration 017's SECURITY DEFINER
    `auth_*` functions, each of which opens a ONE-ROW window (by email, by
    token hash, or by user id) and closes it again. `core/users.py` wraps
    those functions and is the only module that calls them.

    Concretely, on this connection:

        SELECT * FROM answers;              -->  0 rows (no context)
        SELECT * FROM users;                -->  0 rows, and it cannot even
                                                 name password_hash (017 §4
                                                 revokes column SELECT)
        SELECT * FROM refresh_tokens;       -->  permission denied (017 §4)
        SELECT * FROM auth_lookup_user(%s); -->  the one row for that email

    An endpoint that took this dependency for anything but authentication
    would therefore not leak data — it would silently see none, which is the
    §6 failure this whole package exists to prevent. Hence: `/auth` only, and
    `tests/test_api/test_auth.py::test_the_auth_connection_can_see_nothing_else`
    holds that line by asserting the emptiness above rather than trusting it.
    """
    yield from _open(lambda cur: None)


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
