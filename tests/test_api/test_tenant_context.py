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

THE CREDENTIAL IS A REAL SIGNED TOKEN. These tests were written against the
`X-Debug-College-Id` stub and read a header name in a dozen places; that
header is gone (2026-09-06), and the tenant now comes out of a JWT's verified
`cid` claim. What they assert did not change — a request with no usable
tenant must be REFUSED, loudly, rather than served an empty result — which is
why the diff was a credential swap and not a rewrite. `bearer(...)` from
conftest signs one; a malformed credential is now a malformed TOKEN rather
than a malformed uuid.

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

from .conftest import COLLEGE_A, COLLEGE_ABSENT, COLLEGE_B, COLLEGE_SUSPENDED, WHOAMI_PATH

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

# COLLEGE_A / COLLEGE_B are seed_minimal.sql's two REAL colleges (imported
# rather than re-declared, so there is one definition of "the other tenant" in
# this package). COLLEGE_B used to be declared here as "a syntactically valid
# college that need not exist" — whoami only reads session settings, so an
# absent id was enough to show two different values of app.current_college_id.
# It is not enough any more, and not because of whoami: get_tenant_conn now
# verifies the college before SET LOCAL, so a request from an absent college
# never reaches the probe at all. Every test below that expects a 200 needs a
# real tenant.

WHOAMI = WHOAMI_PATH


@pytest.fixture
def client(whoami_app):
    """AsyncClient bound to a freshly-built app carrying the test-only whoami
    probe. No dependence on the developer's API_ENV — the probe is mounted by
    the fixture, not by a setting."""
    transport = httpx.ASGITransport(app=whoami_app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def test_a_token_sets_current_college_id(client, bearer):
    """WITH a valid access token, the request's transaction has
    app.current_college_id set to exactly the college in its `cid` claim."""
    async with client as ac:
        response = await ac.get(WHOAMI, headers=bearer(COLLEGE_A))

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


async def test_missing_token_is_an_explicit_error_not_empty_data(client):
    """WITHOUT a token the request is REJECTED.

    The bug this guards against is the tempting alternative: resolve no
    tenant, open a connection anyway, and let RLS return nothing. That path
    produces a 200 with empty results — indistinguishable from a college that
    genuinely has no data. So assert both that it fails, and that the failure
    says why.
    """
    async with client as ac:
        response = await ac.get(WHOAMI)

    assert response.status_code == 401, response.text
    # The 401 must tell a client what to send, and the standard header that
    # says "this endpoint takes a bearer token" must be on the response.
    assert response.headers["WWW-Authenticate"] == "Bearer"
    detail = response.json()["detail"]
    assert "bearer" in detail.lower()
    assert "auth/login" in detail.lower()

    # And nothing that looks like a successful, empty answer came back.
    assert "db" not in response.json()


async def test_blank_and_malformed_tokens_are_rejected(client, bearer):
    """A blank or unsignable credential must not reach `SET LOCAL`.

    Neither can produce a tenant, and a request that reached the connection
    with no tenant would return zero rows and read as "no data".

    The forged token is the interesting one: it is a syntactically perfect
    JWT carrying a real college id, signed with a key we do not use. If the
    signature were not checked it would be indistinguishable from a genuine
    credential — which is the whole reason `decode_access_token` pins the
    algorithm list rather than trusting the token's own `alg` header.
    """
    from jose import jwt

    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()), "email": "x@y.z",
         "role": "teacher", "cid": COLLEGE_A, "typ": "access",
         "exp": 4102444800},
        "not the key this app signs with", algorithm="HS256")

    async with client as ac:
        blank = await ac.get(WHOAMI, headers={"Authorization": "Bearer    "})
        malformed = await ac.get(WHOAMI, headers={"Authorization": "Bearer not-a-token"})
        signed_by_someone_else = await ac.get(
            WHOAMI, headers={"Authorization": f"Bearer {forged}"})

    assert blank.status_code == 401, blank.text
    assert malformed.status_code == 401, malformed.text
    assert signed_by_someone_else.status_code == 401, signed_by_someone_else.text
    for response in (blank, malformed, signed_by_someone_else):
        assert "db" not in response.json()


async def test_a_well_formed_id_for_an_unknown_or_inactive_college_is_401(
    client, bearer, suspended_college
):
    """A uuid that parses is not a tenant.

    Both of these used to succeed as far as this probe is concerned: identity
    only PARSED the uuid, so any well-formed id established
    `app.current_college_id` for a tenant that owns nothing. Reads then 404ed
    (indistinguishable from any other miss, so arguably fine) but
    `POST /api/v1/upload` 500ed on the college_id foreign key — the database
    catching, at the last possible moment, something the API never checked.

    `api/deps/db.py::get_tenant_conn` now SELECTs the college before
    `SET LOCAL`, so the refusal happens at the edge with the right status, and
    no context is established for a tenant that cannot act. The suspended case
    is the half an absent id cannot exercise: a real row, deliberately
    switched off by the platform, must be refused exactly the same way.
    """
    async with client as ac:
        unknown = await ac.get(WHOAMI, headers=bearer(COLLEGE_ABSENT))
        suspended = await ac.get(WHOAMI,
                                 headers=bearer(suspended_college))

    assert unknown.status_code == 401, unknown.text
    assert suspended.status_code == 401, suspended.text

    for response in (unknown, suspended):
        detail = response.json()["detail"]
        # Names the tenant and what was wrong with it — an operator reading
        # this must not have to go looking in the database.
        assert "college" in detail.lower()
        # And nothing that reads as a successful, empty answer.
        assert "db" not in response.json()

    assert "no college with that id exists" in unknown.json()["detail"]
    assert "suspended" in suspended.json()["detail"]


async def test_two_tenants_get_two_different_session_settings(client, bearer, college_b):
    """Isolation, not merely "a setting was set".

    Two requests with different credentials must produce two different
    values of app.current_college_id. A single hardcoded college, a leaked
    module-level connection, or a `SET` that outlived its transaction would
    all pass the first test in this file and fail this one.
    """
    async with client as ac:
        first = await ac.get(WHOAMI, headers=bearer(COLLEGE_A))
        second = await ac.get(WHOAMI, headers=bearer(COLLEGE_B))
        # Back to A, to catch a context that sticks to whatever was set last.
        third = await ac.get(WHOAMI, headers=bearer(COLLEGE_A))

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)

    a1 = first.json()["db"]["current_college_id"]
    b = second.json()["db"]["current_college_id"]
    a2 = third.json()["db"]["current_college_id"]

    assert a1 == COLLEGE_A
    assert b == COLLEGE_B
    assert a1 != b
    assert a2 == COLLEGE_A


async def test_context_does_not_leak_between_requests(client, bearer):
    """SET LOCAL is transaction-scoped, so a fresh request that fails
    identity resolution must not inherit a previous request's tenant.

    Belt-and-braces against the `SET` (session-scoped) variant of this code,
    which would leak the moment connections are pooled.
    """
    async with client as ac:
        primed = await ac.get(WHOAMI, headers=bearer(COLLEGE_A))
        anonymous = await ac.get(WHOAMI)

    assert primed.json()["db"]["current_college_id"] == COLLEGE_A
    assert anonymous.status_code == 401


async def test_tenant_connection_is_actually_rls_scoped(client, tenant_conn, db_conn,
                                                        college_b):
    """End-to-end proof that the context the API sets is the one RLS honours:
    the same query, run under two different tenants, sees different rows.

    whoami only reports session settings; this reaches past it to the
    behaviour those settings control, using the suite's existing tenant_conn
    fixture (the same SET LOCAL the dependency performs).

    IT USED TO SKIP, and that is the point of this change. Postgres exempts
    SUPERUSER/BYPASSRLS roles from row-level security entirely — `FORCE ROW
    LEVEL SECURITY` closes the table-OWNER loophole, not that one — so with
    `PGUSER=postgres` (the old .env.example default) every policy in migration
    003 was inert and this test could not assert anything the database would
    honour. Migration 016 creates the NOSUPERUSER/NOBYPASSRLS application role
    and .env.example points PGUSER at it, so a bypassing role is now a
    misconfiguration: this FAILS on one rather than skipping, because a skip
    would report green while the property is untested.

    Both tenants own real students (college B is seed_minimal.sql's second
    real college), so this compares two non-empty row sets. The old version
    compared A's students against a college that owned nothing, where "0 rows"
    is equally what a broken query or an unset GUC returns.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT current_user, rolsuper OR rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user"
        )
        role, bypasses_rls = cur.fetchone()

    assert not bypasses_rls, (
        f"PGUSER={role!r} bypasses RLS (superuser or BYPASSRLS), so migration "
        f"003's tenant policies are inert and both tenants would see all rows. "
        f"Run migrations/016_application_role.sql and point PGUSER at the "
        f"application role it creates (see .env.example)."
    )

    def students_visible_to(college_id) -> set[str]:
        conn = tenant_conn(college_id)
        with conn.cursor() as cur:
            cur.execute("SELECT student_id::text FROM students")
            return {row[0] for row in cur.fetchall()}

    seen_by_a = students_visible_to(COLLEGE_A)
    seen_by_b = students_visible_to(uuid.UUID(COLLEGE_B))

    assert seen_by_a, (
        "expected seed_minimal.sql's demo college to have students; "
        "run scripts/reset_and_seed_db.sh"
    )
    assert college_b["student_id"] in seen_by_b, (
        "expected college B to have a student of its own; "
        "run scripts/reset_and_seed_db.sh"
    )
    # The actual property: two contexts, two different row sets, neither
    # containing the other's rows.
    assert seen_by_a.isdisjoint(seen_by_b)
    assert college_b["student_id"] not in seen_by_a

    # And a college that exists but owns nothing still fails CLOSED — the
    # silent behaviour api/deps/db.py exists to keep from being the default.
    assert students_visible_to(uuid.UUID(COLLEGE_SUSPENDED)) == set()
