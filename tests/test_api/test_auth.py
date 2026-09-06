"""tests/test_api/test_auth.py — the authentication layer, end to end.

════════════════════════════════════════════════════════════════════════════
WHAT THIS MODULE IS RESPONSIBLE FOR
════════════════════════════════════════════════════════════════════════════

Every other module in this package authenticates by MINTING a token
(conftest's `make_client`), which is right for them — they are testing what an
authenticated request does. This module is the one that tests how a caller
BECOMES authenticated, and it does it through the real endpoints against a
live Postgres: a real password verified against a real bcrypt hash, a real
refresh-token row, a real revocation.

The properties asserted here, in order of how badly each would hurt:

  1. NO ENDPOINT IS REACHABLE WITHOUT A TOKEN except /health and the /auth
     entry points — derived from the app's own OpenAPI schema, so a router
     added next month is included whether or not anyone remembers this file.
  2. A REVOKED REFRESH TOKEN CANNOT MINT AN ACCESS TOKEN. If it could,
     "logout" would be a client-side gesture and nothing more.
  3. AN EXPIRED ACCESS TOKEN IS REFUSED. The short access TTL is the ONLY
     bound on a stolen access token (there is no revocation list), so an
     expiry that were not enforced would make that bound imaginary.
  4. A TOKEN WITH NO COLLEGE, FROM A ROLE THAT NEEDS ONE, FAILS LOUDLY.
     Silently, it would set no RLS context and every query would return zero
     rows — a 200 with an empty list, reported to a real user as "you have no
     data" (CLAUDE_CONTEXT.md §6).
  5. WRONG PASSWORD AND UNKNOWN EMAIL ARE INDISTINGUISHABLE, in the response
     and in the time taken. A login endpoint that answers faster for an
     address nobody registered is a user-enumeration oracle at any scale.
  6. TENANT ISOLATION SURVIVES REAL AUTH: a teacher signed in at college A
     sees 404 / empty for college B's rows.

Everything here is marked `db` and runs against a live database, like the rest
of tests/test_api. Nothing is mocked: the thing under test is the chain from
an HTTP request through a signed claim to `SET LOCAL app.current_college_id`,
and a mock anywhere in it would confirm the code we wrote rather than the
behaviour it produces.
"""
from __future__ import annotations

import datetime as dt
import time
import uuid

import httpx
import pytest
from jose import jwt

import core.db
import core.users
from api.deps.db import set_admin_context
from api.deps.identity import create_access_token
from api.main import create_app
from api.settings import Settings

from .conftest import COLLEGE_A, TEST_PASSWORD

# `asyncio` is applied per-test rather than module-wide: the last two tests in
# this file are SYNCHRONOUS on purpose (they drive a dependency directly rather
# than through a request), and pytest warns about an asyncio mark on a sync
# function — a warning that would be noise here and camouflage elsewhere.
pytestmark = [pytest.mark.db]

asyncio = pytest.mark.asyncio

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


@pytest.fixture
def auth_app(_dotenv, api_settings):
    """ONE app instance per test, shared by every client the test builds.

    Per-test rather than per-client because the login rate limiter lives on
    `app.state` (api/deps/ratelimit.py): two clients over two apps would have
    two independent counters, and the rate-limit test would silently pass
    without ever limiting anything.
    """
    return create_app(api_settings)


@pytest.fixture
def anon(auth_app):
    """Factory for clients that send NO credential of their own.

    A bare client, not `make_client` with the header cleared: httpx merges a
    client's default headers into every request, so a client that HAS an
    Authorization header cannot fully un-send it per request. Every test here
    supplies its credential explicitly, which is the point.
    """
    def make(**headers: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=auth_app),
            base_url="http://testserver",
            headers=headers,
        )

    return make


async def _login(client, account, password=TEST_PASSWORD):
    return await client.post(
        LOGIN, json={"email": account["email"], "password": password})


# ════════════════════════ 1. the unauthenticated surface ════════════════════

@asyncio
async def test_no_endpoint_is_reachable_without_a_token(auth_app, anon):
    """EVERY route the app exposes, called with no credential at all.

    Derived from the app's OWN OpenAPI schema rather than from a list kept
    here, so an endpoint added next month is covered on the day it is added.
    The exemptions are named individually, and each one has to be justified in
    this docstring rather than merely appearing in a set:

      GET  /health         reports its own aliveness and nothing else, and
                           opens no connection.
      POST /auth/login     IS the credential-granting endpoint.
      POST /auth/refresh   is authenticated by the refresh token in its body;
                           the access token is expired by definition when a
                           client calls it.
      POST /auth/logout    acts on the refresh token in its body and answers
                           204 either way, so it reveals nothing.

    A 422 counts as "reachable" here and is asserted against deliberately: it
    would mean the request got past authentication and was rejected by schema
    validation, i.e. the endpoint was reachable without a token.
    """
    exempt = {
        ("get", "/health"),
        ("post", LOGIN),
        ("post", REFRESH),
        ("post", LOGOUT),
    }

    routes = [
        (method, path)
        for path, operations in auth_app.openapi()["paths"].items()
        for method in operations
    ]
    assert len(routes) > 15, "the schema looks empty; this test would prove nothing"

    async with anon() as client:
        for method, path in routes:
            if (method, path) in exempt:
                continue

            # A concrete id for every path template. Whether the row exists is
            # irrelevant: authentication happens before the id is looked at,
            # which is exactly what is being asserted.
            concrete = path
            while "{" in concrete:
                head, rest = concrete.split("{", 1)
                _, tail = rest.split("}", 1)
                concrete = f"{head}{uuid.uuid4()}{tail}"

            response = await client.request(method.upper(), concrete, json={})

            assert response.status_code == 401, (
                f"{method.upper()} {path} answered {response.status_code} with "
                f"NO credential: {response.text[:300]}\n"
                f"Every endpoint but the four exempt ones must require a "
                f"bearer token. If this one is meant to be public, add it to "
                f"`exempt` above WITH a reason in the docstring — the point of "
                f"this test is that the decision is deliberate."
            )
            assert response.headers.get("WWW-Authenticate") == "Bearer"


@asyncio
async def test_health_answers_without_a_credential_and_says_nothing_else(anon):
    """The liveness probe works unauthenticated, and leaks nothing.

    The second half is the interesting one: an unauthenticated endpoint is
    readable by everyone, and "which version are you running" is the first
    question an attacker asks. So the response must be exactly one field.
    """
    async with anon() as client:
        response = await client.get("/health")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok"}


# ═══════════════════════════ 2. login, the happy path ═══════════════════════

@asyncio
async def test_login_returns_a_working_access_token_and_a_refresh_token(
    anon, account
):
    """The whole point, proven by USING the token rather than by inspecting it.

    A test that decoded the JWT and checked its claims would pass against an
    API that never verified them. So this signs in, then calls an endpoint
    that requires authentication AND a tenant context, and asserts it works.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        response = await _login(client, teacher)

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["refresh_token"] and body["refresh_token"] != body["access_token"]
    assert body["user"]["email"] == teacher["email"]
    assert body["user"]["college_id"] == COLLEGE_A
    assert body["user"]["role"] == "teacher"
    # The password, and anything derived from it, must not come back.
    assert "password" not in response.text and "hash" not in response.text
    # A token response must never be cached by an intermediary.
    assert response.headers["Cache-Control"] == "no-store"

    async with anon(Authorization=f"Bearer {body['access_token']}") as client:
        exams = await client.get("/api/v1/exams")
        me = await client.get(ME)

    assert exams.status_code == 200, exams.text
    assert me.json()["user_id"] == str(teacher["user_id"])
    assert me.json()["reviewer_id"] == str(teacher["reviewer_id"])


@asyncio
async def test_the_email_is_case_insensitive(anon, account):
    """`users.email` is citext, so the address is one account in any case.

    Asserted because the alternative — case-sensitive matching in application
    code — produces a SECOND account for the same human the first time
    somebody's client capitalises their address, which is a data problem long
    before it is a login problem.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        response = await _login(
            client, {"email": teacher["email"].upper()}, TEST_PASSWORD)

    assert response.status_code == 200, response.text


# ══════════════════ 3. failure is uniform: response AND time ════════════════

@asyncio
async def test_wrong_password_and_unknown_email_are_indistinguishable(anon, account):
    """Same status, same body, same headers. No field says which half failed.

    An endpoint that answered 404 for an unknown address and 401 for a bad
    password would let anyone enumerate the platform's users with a wordlist
    and no valid credentials at all.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        wrong_password = await _login(client, teacher, "definitely not it")
        unknown_email = await client.post(LOGIN, json={
            "email": f"nobody-{uuid.uuid4()}@nowhere.example",
            "password": "definitely not it"})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json()["detail"] == "Incorrect email or password."
    # And nothing token-shaped came back from either.
    for response in (wrong_password, unknown_email):
        assert "access_token" not in response.text


@asyncio
async def test_an_unknown_email_costs_the_same_time_as_a_wrong_password(anon, account):
    """THE TIMING HALF, which is the one that gets forgotten.

    If a nonexistent address returned before any hashing happened, the two
    branches would differ by the cost of a bcrypt verification — tens of
    milliseconds, measurable remotely, at any scale, with no credentials. An
    attacker would learn which addresses are real and then have a list worth
    attacking. `api/routers/auth.py` verifies the presented password against a
    DECOY HASH when no user row exists, so both branches pay for exactly one
    verification.

    The bound is deliberately loose (a factor of three, on medians of three
    samples). This runs on a developer's laptop alongside other tests; the
    failure it must catch is a MISSING bcrypt call, which is a difference of
    orders of magnitude, not of percent. Tightening it would buy nothing and
    would make the suite flaky.
    """
    teacher = account(COLLEGE_A, "teacher")

    async def sample(payload) -> float:
        start = time.perf_counter()
        async with anon() as client:
            response = await client.post(LOGIN, json=payload)
        assert response.status_code == 401
        return time.perf_counter() - start

    known, unknown = [], []
    for _ in range(3):
        known.append(await sample(
            {"email": teacher["email"], "password": "wrong password"}))
        unknown.append(await sample(
            {"email": f"nobody-{uuid.uuid4()}@nowhere.example",
             "password": "wrong password"}))

    known.sort()
    unknown.sort()
    ratio = unknown[1] / known[1]
    assert 1 / 3 < ratio < 3, (
        f"an unknown address took {unknown[1]:.3f}s and a wrong password "
        f"{known[1]:.3f}s (ratio {ratio:.2f}). The two branches must cost the "
        f"same: a faster answer for an address nobody registered is a "
        f"user-enumeration oracle. Check the decoy-hash verification in "
        f"api/routers/auth.py::login."
    )


@asyncio
async def test_a_disabled_account_is_refused_with_the_same_answer(anon, account,
                                                                  admin_conn):
    """is_active=false is a 401 that looks exactly like a wrong password.

    Deliberately NOT "this account is disabled": that message confirms the
    address exists and is worth attacking, which is the same leak the whole
    endpoint is built to avoid. The account holder finds out from whoever
    disabled them.
    """
    teacher = account(COLLEGE_A, "teacher")
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("UPDATE users SET is_active = false WHERE user_id = %s",
                    (str(teacher["user_id"]),))
    admin_conn.commit()

    try:
        async with anon() as client:
            response = await _login(client, teacher)
    finally:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("UPDATE users SET is_active = true WHERE user_id = %s",
                        (str(teacher["user_id"]),))
        admin_conn.commit()

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Incorrect email or password."


@asyncio
async def test_a_user_of_a_suspended_college_cannot_sign_in(
    anon, account, suspended_college
):
    """Refused at LOGIN, not merely at every request afterwards.

    A suspended college is one the platform has deliberately switched off.
    `get_tenant_conn` already refuses its requests (§6), so admitting the
    login would issue a session whose every subsequent call 401s — an account
    that appears to work and then does not. The check runs AFTER the password
    verification, so it cannot be used to probe which colleges are suspended
    without already holding valid credentials for one, and it answers with the
    same string as a wrong password.
    """
    user = account(suspended_college, "teacher")

    async with anon() as client:
        response = await _login(client, user)

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Incorrect email or password."


@asyncio
async def test_an_absurdly_long_password_is_refused_not_a_500(anon, account):
    """bcrypt hashes at most 72 BYTES, and a caller may send more than that.

    Two things must not happen: a 500 (passlib raises `ValueError` on an
    over-long secret with bcrypt 4.x, and an unhandled exception on the login
    path would be a denial of service anyone can trigger), and a SUCCESS by
    truncation (where a 200-character password would be accepted on the
    strength of its first 72 bytes). Both are the same 401 here.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        response = await client.post(
            LOGIN, json={"email": teacher["email"], "password": "x" * 500})

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Incorrect email or password."


# ══════════════════════════ 4. the rate limiter ═════════════════════════════

@asyncio
async def test_repeated_failures_are_rate_limited_then_recover(_dotenv, account):
    """A wall of failed attempts is refused with 429 and a Retry-After.

    Uses its own app with a tiny limit so the test does not have to make ten
    bcrypt verifications to reach the default. The behaviour under test is the
    limiter, not the number.

    ALSO asserts that the refusal comes BEFORE the password check: the limited
    request is sent with the CORRECT password and must still be refused. That
    is what makes the limiter a defence against CPU exhaustion and not merely
    against guessing — a limiter that verified first would still burn a bcrypt
    per attempt.
    """
    teacher = account(COLLEGE_A, "teacher")
    app = create_app(Settings(enable_debug_endpoints=True, storage_mode="dummy",
                              login_rate_limit_attempts=3,
                              login_rate_limit_window_seconds=300))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    ) as client:
        for _ in range(3):
            failed = await client.post(
                LOGIN, json={"email": teacher["email"], "password": "wrong"})
            assert failed.status_code == 401, failed.text

        limited = await client.post(
            LOGIN, json={"email": teacher["email"], "password": TEST_PASSWORD})

    assert limited.status_code == 429, limited.text
    retry_after = int(limited.headers["Retry-After"])
    assert 0 < retry_after <= 300
    assert "access_token" not in limited.text


@asyncio
async def test_the_limiter_does_not_reveal_whether_an_address_exists(_dotenv):
    """An address with no account is limited exactly like one with an account.

    Otherwise the limiter would answer the question the login endpoint is so
    careful not to: "429 after five tries" for a real address and "401
    forever" for an invented one is an enumeration oracle wearing a different
    status code.
    """
    app = create_app(Settings(enable_debug_endpoints=True, storage_mode="dummy",
                              login_rate_limit_attempts=2,
                              login_rate_limit_window_seconds=300))
    ghost = f"nobody-{uuid.uuid4()}@nowhere.example"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    ) as client:
        for _ in range(2):
            assert (await client.post(
                LOGIN, json={"email": ghost, "password": "x"})).status_code == 401
        limited = await client.post(LOGIN, json={"email": ghost, "password": "x"})

    assert limited.status_code == 429, limited.text


@asyncio
async def test_a_successful_login_clears_that_addresss_counter(_dotenv, account):
    """Fumbling a password twice must not lock you out of your next session.

    The counter is cleared for the EMAIL on success — see api/deps/ratelimit.py
    on why the IP counter deliberately is not.
    """
    teacher = account(COLLEGE_A, "teacher")
    app = create_app(Settings(enable_debug_endpoints=True, storage_mode="dummy",
                              login_rate_limit_attempts=3,
                              login_rate_limit_window_seconds=300))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    ) as client:
        for _ in range(2):
            await client.post(LOGIN, json={"email": teacher["email"],
                                           "password": "wrong"})
        good = await client.post(LOGIN, json={"email": teacher["email"],
                                              "password": TEST_PASSWORD})
        # Without the clear, this third failure would trip the limit.
        after = await client.post(LOGIN, json={"email": teacher["email"],
                                               "password": "wrong"})

    assert good.status_code == 200, good.text
    assert after.status_code == 401, after.text


# ═════════════════════════ 5. tokens: expiry and shape ══════════════════════

@asyncio
async def test_an_expired_access_token_is_refused(anon, account, api_settings):
    """The access token's TTL is its ONLY bound, so it has to be enforced.

    There is no revocation list for access tokens — `get_current_user` does no
    database lookup, deliberately — which means a stolen one is usable until
    it expires and not one second longer. If `exp` were not checked, that
    "not one second longer" would be fiction and a leaked token would be a
    permanent credential.

    Signed here with `now` two days in the past rather than by sleeping
    through a TTL, which is what that parameter on `create_access_token`
    exists for.
    """
    teacher = account(COLLEGE_A, "teacher")
    expired, _ = create_access_token(
        user_id=teacher["user_id"], reviewer_id=teacher["reviewer_id"],
        email=teacher["email"], role="teacher", college_id=COLLEGE_A,
        settings=api_settings,
        now=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2))

    async with anon(Authorization=f"Bearer {expired}") as client:
        me = await client.get(ME)
        exams = await client.get("/api/v1/exams")

    assert me.status_code == 401, me.text
    assert exams.status_code == 401, exams.text
    assert "expired" in me.json()["detail"].lower()


@asyncio
async def test_a_teacher_token_with_no_college_fails_loudly_not_silently(
    anon, api_settings
):
    """THE §6 FAILURE, ARRIVING WITH A VALID SIGNATURE.

    This token is signed with the app's own key, is unexpired, and claims
    role=teacher with cid=null. Every cryptographic check passes. If
    `user_from_claims` did not refuse it, `get_tenant_conn` would be handed
    college_id=None, `SET LOCAL app.current_college_id` would never run, and
    every RLS policy would match nothing — so `GET /exams` would answer
    **200 with an empty list**, and the user would be told they have no exams.

    So the assertion is not merely "it fails". It is that it fails with a 401
    naming the problem, and specifically that it does NOT return an
    empty-but-successful body.
    """
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
         "email": "nocollege@example.edu", "role": "teacher", "cid": None,
         "typ": "access", "exp": 4102444800},
        api_settings.jwt_signing_key, algorithm=api_settings.jwt_algorithm)

    async with anon(Authorization=f"Bearer {token}") as client:
        exams = await client.get("/api/v1/exams")
        results = await client.get("/api/v1/results")

    for response in (exams, results):
        assert response.status_code == 401, (
            f"a college-less teacher token returned {response.status_code} "
            f"({response.text[:200]}). Silently, this is a 200 with an empty "
            f"list — 'you have no data' — which is the worst answer available."
        )
        body = response.json()
        assert body.get("items") is None and body.get("total") is None
        assert "college" in body["detail"].lower()


@asyncio
async def test_a_refresh_token_cannot_be_used_as_an_access_token(anon, account):
    """The two are not interchangeable, and the failure is a plain 401.

    A refresh token is opaque random text, so it cannot decode as a JWT at
    all — but the `typ` claim check exists for the day this key signs
    something else (an invite link, a download token), and this test is what
    will still be here to catch that thing being presented as a login.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        tokens = (await _login(client, teacher)).json()

    async with anon(Authorization=f"Bearer {tokens['refresh_token']}") as client:
        response = await client.get(ME)

    assert response.status_code == 401, response.text


# ══════════════════════ 6. refresh, rotation and revocation ═════════════════

@asyncio
async def test_refresh_returns_a_new_pair_and_retires_the_old_one(anon, account):
    """Rotation: the refresh token used is revoked as its replacement is issued.

    That is what bounds a stolen refresh token. Without rotation, a thief and
    the legitimate client can both keep refreshing forever and neither ever
    notices; with it, the theft surfaces as somebody being logged out.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        first = (await _login(client, teacher)).json()
        second = await client.post(REFRESH,
                                   json={"refresh_token": first["refresh_token"]})
        replayed = await client.post(REFRESH,
                                     json={"refresh_token": first["refresh_token"]})

    assert second.status_code == 200, second.text
    body = second.json()
    assert body["refresh_token"] != first["refresh_token"]
    assert body["user"]["user_id"] == str(teacher["user_id"])

    assert replayed.status_code == 401, (
        "the OLD refresh token still worked after being exchanged. Without "
        "rotation a stolen refresh token is a permanent second session.")

    # And the NEW one works.
    async with anon(Authorization=f"Bearer {body['access_token']}") as client:
        assert (await client.get(ME)).status_code == 200


@asyncio
async def test_a_revoked_refresh_token_cannot_mint_a_new_access_token(anon, account):
    """LOGOUT MUST ACTUALLY INVALIDATE. The headline test of this module.

    A stateless JWT cannot be revoked, so if the refresh token were not a row
    that logout marks, "sign out" would mean nothing more than the client
    dropping its own copy — and a token captured before logout would keep
    minting fresh access tokens for its full lifetime. That is why
    migration 017 §2 exists at all, and this is the test that says so.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        tokens = (await _login(client, teacher)).json()

        logout = await client.post(LOGOUT,
                                   json={"refresh_token": tokens["refresh_token"]})
        after = await client.post(REFRESH,
                                  json={"refresh_token": tokens["refresh_token"]})

    assert logout.status_code == 204, logout.text
    assert after.status_code == 401, (
        "a REVOKED refresh token minted a new access token. Logout is "
        "cosmetic and every 'sign out' on the platform is a lie.")
    assert "access_token" not in after.text


@asyncio
async def test_logout_answers_204_for_a_token_it_never_knew(anon, account):
    """Unknown, already-revoked and live tokens all get 204.

    Reporting the difference would make logout an oracle over tokens the
    caller does not hold, and there is nothing a client could do with the
    distinction: either way, that token is now dead.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        tokens = (await _login(client, teacher)).json()
        first = await client.post(LOGOUT,
                                  json={"refresh_token": tokens["refresh_token"]})
        again = await client.post(LOGOUT,
                                  json={"refresh_token": tokens["refresh_token"]})
        never = await client.post(LOGOUT,
                                  json={"refresh_token": "no such token, ever"})

    assert first.status_code == again.status_code == never.status_code == 204


@asyncio
async def test_logout_everywhere_kills_every_session(anon, account):
    """`all_sessions` revokes the account's other live refresh tokens too.

    The password-change / stolen-laptop lever. Two independent sessions are
    opened, one signs out with all_sessions, and the OTHER one must be dead —
    which a per-token logout would leave alive.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        laptop = (await _login(client, teacher)).json()
        phone = (await _login(client, teacher)).json()

        assert laptop["refresh_token"] != phone["refresh_token"]

        out = await client.post(LOGOUT, json={"refresh_token": laptop["refresh_token"],
                                              "all_sessions": True})
        phone_refresh = await client.post(
            REFRESH, json={"refresh_token": phone["refresh_token"]})

    assert out.status_code == 204, out.text
    assert phone_refresh.status_code == 401, (
        "'sign out everywhere' left another session alive")


@asyncio
async def test_refresh_re_reads_the_account_rather_than_copying_its_claims(
    anon, account, admin_conn
):
    """A deactivated account stops refreshing IMMEDIATELY, not at expiry.

    The tempting implementation copies role/college/id forward out of the old
    token — it is right there, already verified. But then an account disabled
    at 10:00 keeps minting working access tokens until its refresh token
    expires, which could be a fortnight. `auth_user_by_id()` (migration 017
    §5) exists so the refresh path re-reads the row.

    The kill is also total: a refused refresh revokes EVERY session the
    account has, because whatever disabled it meant to end its access, and a
    second refresh token in another browser would otherwise keep working.
    """
    teacher = account(COLLEGE_A, "teacher")

    async with anon() as client:
        laptop = (await _login(client, teacher)).json()
        phone = (await _login(client, teacher)).json()

        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("UPDATE users SET is_active = false WHERE user_id = %s",
                        (str(teacher["user_id"]),))
        admin_conn.commit()

        try:
            refused = await client.post(
                REFRESH, json={"refresh_token": laptop["refresh_token"]})
            other = await client.post(
                REFRESH, json={"refresh_token": phone["refresh_token"]})
        finally:
            with admin_conn.cursor() as cur:
                set_admin_context(cur)
                cur.execute("UPDATE users SET is_active = true WHERE user_id = %s",
                            (str(teacher["user_id"]),))
            admin_conn.commit()

    assert refused.status_code == 401, refused.text
    assert other.status_code == 401, (
        "the account's OTHER session survived its deactivation")


# ═══════════════════ 7. roles, tenancy and what a token buys ════════════════

@asyncio
async def test_a_teacher_signed_in_at_college_a_cannot_see_college_b(
    anon, account, make_booklet, college_b
):
    """Tenant isolation, driven by a REAL login rather than a minted token.

    The rest of the suite proves this against tokens conftest signs; this one
    proves the same thing about a token the login endpoint issued, which is
    the only kind a real client will ever hold. B's answer is addressed by its
    exact id and comes back 404 — indistinguishable from a nonexistent one,
    never 403, which would confirm the row exists.
    """
    teacher = account(COLLEGE_A, "teacher")
    b = make_booklet(college_id=college_b["college_id"],
                     student_id=college_b["student_id"],
                     exam_id=college_b["exam_id"])

    async with anon() as client:
        tokens = (await _login(client, teacher)).json()

    async with anon(Authorization=f"Bearer {tokens['access_token']}") as client:
        listed = await client.get("/api/v1/results",
                                  params={"student_id": college_b["student_id"]})
        direct = await client.get(f"/api/v1/results/{b['answer_id']}")
        ghost = await client.get(f"/api/v1/results/{uuid.uuid4()}")

    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [], (
        "college A's teacher listed college B's answers")
    assert listed.json()["total"] == 0

    assert direct.status_code == 404, direct.text
    # Indistinguishable from an id that does not exist anywhere: same status,
    # and nothing of the row's content in the body.
    assert direct.status_code == ghost.status_code
    assert str(b["answer_id"]) not in direct.text or "No answer" in direct.text


@asyncio
async def test_a_platform_admin_gets_the_bypass_context_not_a_college(
    anon, admin_conn, account
):
    """Role -> session variable, the whole permission model, end to end.

    A platform_admin's token carries no college (migration 017's biconditional
    CHECK, mirrored in `create_access_token`), so `get_tenant_conn` sets
    `app.is_platform_admin` instead of `app.current_college_id`. The endpoints
    that pass `user.college_id` into an explicit tenant predicate are
    therefore CLOSED to them with a 403 — not because platform admins are
    less trusted, but because a NULL college in those predicates would match
    nothing and answer 404 for data that plainly exists (see
    api/deps/identity.py::require_college_user).

    The 403 here does not contradict "404, never 403": that rule is about
    another tenant's RESOURCE, where a 403 would confirm a row exists. This
    403 is about the CALLER, who is authenticated and known.
    """
    admin = account(None, "platform_admin")
    assert admin["college_id"] is None

    async with anon() as client:
        tokens = (await _login(client, admin)).json()

    assert tokens["user"]["college_id"] is None
    assert tokens["user"]["role"] == "platform_admin"

    async with anon(Authorization=f"Bearer {tokens['access_token']}") as client:
        me = await client.get(ME)
        exams = await client.get("/api/v1/exams")
        questions = await client.get("/api/v1/questions")

    assert me.status_code == 200 and me.json()["college_id"] is None
    assert exams.status_code == 403, exams.text
    assert "platform_admin" in exams.json()["detail"]
    # The SHARED bank has no college to scope over, so it stays open — see
    # CLAUDE_CONTEXT.md §11, "the question bank is SHARED".
    assert questions.status_code == 200, questions.text


@asyncio
async def test_a_platform_admin_token_carrying_a_college_is_refused(
    anon, api_settings
):
    """Self-contradictory tokens are refused rather than half-believed.

    role=platform_admin says "cross-tenant"; a `cid` says "this one tenant".
    Honouring either half would mean choosing which claim to believe about a
    token someone may have crafted. There is no safe choice, so there is no
    choice.
    """
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
         "email": "admin@example.edu", "role": "platform_admin", "cid": COLLEGE_A,
         "typ": "access", "exp": 4102444800},
        api_settings.jwt_signing_key, algorithm=api_settings.jwt_algorithm)

    async with anon(Authorization=f"Bearer {token}") as client:
        response = await client.get(ME)

    assert response.status_code == 401, response.text


# ═════════════════════ 8. the connection /auth runs on ══════════════════════

def test_the_auth_connection_can_see_nothing_else(_dotenv):
    """`get_auth_conn` has no tenant context and no admin bypass.

    It exists because authentication cannot have a tenant context — resolving
    the tenant is its OUTPUT. This asserts the three properties that make that
    safe, rather than trusting the dependency's docstring:

      1. RLS-protected tables return ZERO ROWS (no context matches nothing);
      2. `users` cannot even be read column-wise — `password_hash` is not
         granted to the application role at all (migration 017 §4);
      3. `refresh_tokens` refuses outright, at the privilege layer, so the
         GUC-based policies are not the only thing standing between an
         unauthenticated request and every session on the platform.

    A synchronous test, deliberately: it drives the dependency directly rather
    than through a request, because what is under test is the connection's
    own capabilities.
    """
    import psycopg2

    from api.deps.db import get_auth_conn

    generator = get_auth_conn()
    conn = next(generator)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM answers")
            assert cur.fetchone()[0] == 0, (
                "the auth connection can read answers. It has no tenant "
                "context, so RLS should match nothing — if this returns rows, "
                "PGUSER is a superuser or BYPASSRLS role and every policy in "
                "this database is inert (CLAUDE_CONTEXT.md §6).")

            cur.execute("SELECT count(*) FROM users")
            assert cur.fetchone()[0] == 0

            for statement, what in (
                ("SELECT password_hash FROM users", "users.password_hash"),
                ("SELECT * FROM refresh_tokens", "refresh_tokens"),
            ):
                with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                    with conn.cursor() as probe:
                        probe.execute(statement)
                conn.rollback()
    finally:
        conn.close()
        generator.close()


def test_the_login_read_reaches_exactly_one_row(_dotenv, account, admin_conn):
    """`auth_lookup_user()` is a one-row window, not a bypass.

    The function is SECURITY DEFINER, which is a large hammer, so what it can
    reach is worth asserting directly: the row whose email was passed, and
    nothing else. In particular it must NOT leave the window open — the GUC it
    sets is restored before it returns, so a second read in the same
    transaction sees nothing again.
    """
    teacher = account(COLLEGE_A, "teacher")
    account(COLLEGE_A, "admin")  # a second row that must stay invisible

    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            row = core.users.lookup_user_for_login(cur, teacher["email"])
            assert row is not None
            assert row["user_id"] == teacher["user_id"]
            assert row["password_hash"].startswith("$2")

            # The window closed behind it.
            cur.execute("SELECT count(*) FROM users")
            assert cur.fetchone()[0] == 0, (
                "auth_lookup_user() left its policy window open — every "
                "subsequent read in this transaction can see that row too")

            assert core.users.lookup_user_for_login(
                cur, f"nobody-{uuid.uuid4()}@nowhere.example") is None
    finally:
        conn.rollback()
        conn.close()
