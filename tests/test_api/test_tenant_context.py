"""tests/test_api/test_tenant_context.py — proves the RLS tenant context is
actually established per request.

These are the tests that matter most in this codebase, because the failure
they guard against is silent: an answer-schema query with no
`app.current_college_id` returns zero rows instead of erroring
(CLAUDE_CONTEXT.md §6). A broken tenant context therefore looks like an empty
database, not like a bug, so it has to be asserted directly rather than
inferred from some endpoint returning data.

They hit a LIVE Postgres (marked `db`, same convention as the rest of the
suite) via the real dependency chain — httpx.AsyncClient over an ASGI
transport, no mocked connection. A mock would happily confirm that we called
`SET LOCAL` while proving nothing about whether the setting took effect
inside the request's transaction, which is the only thing under test.

THE PROBE THESE TESTS DRIVE IS TEST-ONLY. It used to be a shipped, env-gated
`GET /api/v1/_debug/whoami`; that endpoint was deleted in the Day 5 hardening
pass and the probe now mounts onto the test app from conftest.py
(`mount_whoami_probe`). Same request path, same dependencies, same assertions
— but nothing an environment variable can turn on in production.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from api.deps.identity import DEBUG_COLLEGE_HEADER

from .conftest import WHOAMI_PATH

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

COLLEGE_A = "11111111-1111-1111-1111-111111111111"  # seed_minimal.sql's demo college
# A syntactically valid college that need not exist: whoami reads session
# settings only, so this proves isolation without depending on second fixture
# data existing in whatever DB the suite runs against.
COLLEGE_B = "22222222-2222-2222-2222-222222222222"

WHOAMI = WHOAMI_PATH


@pytest.fixture
def client(whoami_app):
    """AsyncClient bound to a freshly-built app carrying the test-only whoami
    probe. No dependence on the developer's API_ENV — the probe is mounted by
    the fixture, not by a setting."""
    transport = httpx.ASGITransport(app=whoami_app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_header_sets_current_college_id(client):
    """WITH the debug header, the request's transaction has
    app.current_college_id set to exactly that college."""
    async with client as ac:
        response = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: COLLEGE_A})

    assert response.status_code == 200, response.text
    body = response.json()

    # The identity layer resolved the caller...
    assert body["identity"]["college_id"] == COLLEGE_A
    # ...and, the part that actually matters, Postgres agrees inside the
    # request's own transaction.
    assert body["db"]["current_college_id"] == COLLEGE_A
    assert body["rls_context_ok"] is True
    # A tenant request must NOT have acquired the cross-tenant bypass.
    assert body["db"]["is_platform_admin"] in (None, "", "false")


async def test_missing_header_is_an_explicit_error_not_empty_data(client):
    """WITHOUT the header the request is REJECTED.

    The bug this guards against is the tempting alternative: resolve no
    tenant, open a connection anyway, and let RLS return nothing. That path
    produces a 200 with empty results — indistinguishable from a college that
    genuinely has no data. So assert both that it fails, and that the failure
    says why.
    """
    async with client as ac:
        response = await ac.get(WHOAMI)

    assert response.status_code == 401, response.text
    detail = response.json()["detail"]
    assert DEBUG_COLLEGE_HEADER in detail
    # Not a bare "unauthorized" — the message must name the actual hazard.
    assert "silently" in detail.lower()

    # And nothing that looks like a successful, empty answer came back.
    assert "db" not in response.json()


async def test_blank_and_malformed_headers_are_rejected(client):
    """A blank or non-UUID header must not reach `SET LOCAL`.

    Either would be accepted by the SET statement itself and only misbehave
    later — a blank matching nothing, a malformed one raising from inside the
    RLS policy's ::uuid cast — both far from the actual mistake.
    """
    async with client as ac:
        blank = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: "   "})
        malformed = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: "not-a-uuid"})

    assert blank.status_code == 401, blank.text
    assert malformed.status_code == 401, malformed.text
    assert "uuid" in malformed.json()["detail"].lower()


async def test_two_tenants_get_two_different_session_settings(client):
    """Isolation, not merely "a setting was set".

    Two requests with different credentials must produce two different
    values of app.current_college_id. A single hardcoded college, a leaked
    module-level connection, or a `SET` that outlived its transaction would
    all pass the first test in this file and fail this one.
    """
    async with client as ac:
        first = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: COLLEGE_A})
        second = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: COLLEGE_B})
        # Back to A, to catch a context that sticks to whatever was set last.
        third = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: COLLEGE_A})

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)

    a1 = first.json()["db"]["current_college_id"]
    b = second.json()["db"]["current_college_id"]
    a2 = third.json()["db"]["current_college_id"]

    assert a1 == COLLEGE_A
    assert b == COLLEGE_B
    assert a1 != b
    assert a2 == COLLEGE_A


async def test_context_does_not_leak_between_requests(client):
    """SET LOCAL is transaction-scoped, so a fresh request that fails
    identity resolution must not inherit a previous request's tenant.

    Belt-and-braces against the `SET` (session-scoped) variant of this code,
    which would leak the moment connections are pooled.
    """
    async with client as ac:
        primed = await ac.get(WHOAMI, headers={DEBUG_COLLEGE_HEADER: COLLEGE_A})
        anonymous = await ac.get(WHOAMI)

    assert primed.json()["db"]["current_college_id"] == COLLEGE_A
    assert anonymous.status_code == 401


async def test_tenant_connection_is_actually_rls_scoped(client, tenant_conn, db_conn):
    """End-to-end proof that the context the API sets is the one RLS honours:
    the same query, run under two different tenants, sees different rows.

    whoami only reports session settings; this reaches past it to the
    behaviour those settings control, using the suite's existing tenant_conn
    fixture (the same SET LOCAL the dependency performs).

    IMPORTANT — this test SKIPS when the configured PGUSER is a superuser (or
    has BYPASSRLS). Postgres exempts those roles from row-level security
    entirely; `FORCE ROW LEVEL SECURITY` in migration 003 closes the
    table-OWNER loophole but NOT the superuser one. With PGUSER=postgres —
    the value in .env.example, and so most local setups — every policy in
    migration 003 is inert and both tenants see all rows.

    That is a deployment gap, not an API bug: api/deps/db.py sets the context
    correctly either way (the tests above assert that against the live DB),
    and this test starts enforcing the isolation the moment the API connects
    as a non-superuser role. Skipping is deliberately loud rather than
    asserting something the current DB cannot deliver.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        (bypasses_rls,) = cur.fetchone()

    if bypasses_rls:
        pytest.skip(
            "PGUSER bypasses RLS (superuser or BYPASSRLS), so migration 003's "
            "policies are not enforced and tenant isolation cannot be "
            "verified. Create a non-superuser application role and point "
            "PGUSER at it; see api/README.md."
        )

    seeded = tenant_conn(COLLEGE_A)
    with seeded.cursor() as cur:
        cur.execute("SELECT count(*) FROM students")
        (seeded_students,) = cur.fetchone()

    other = tenant_conn(uuid.UUID(COLLEGE_B))
    with other.cursor() as cur:
        cur.execute("SELECT count(*) FROM students")
        (other_students,) = cur.fetchone()

    assert seeded_students > 0, (
        "expected seed_minimal.sql's demo college to have students; "
        "run scripts/reset_and_seed_db.sh"
    )
    # Fails closed for a college with no rows of its own — the exact silent
    # behaviour api/deps/db.py exists to keep from ever being the default.
    assert other_students == 0
