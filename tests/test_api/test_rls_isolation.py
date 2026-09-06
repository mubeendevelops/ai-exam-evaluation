"""tests/test_api/test_rls_isolation.py — the tenant boundary, asserted
uniformly over EVERY endpoint rather than one router at a time.

════════════════════════════════════════════════════════════════════════════
WHAT THIS FILE IS FOR
════════════════════════════════════════════════════════════════════════════

CLAUDE_CONTEXT.md §6 names the single most likely bug class in this codebase:
the answer-schema RLS policies use `current_setting('app.current_college_id',
true)` — the missing_ok form — so a query run with NO tenant context does not
raise. It returns zero rows. Every call site then reads that as "there is no
such data": a 200 with `[]`, or a 404, or an orphan write. Nothing logs, and
nothing crashes.

So the property under test is not "cross-tenant reads are blocked". It is the
stronger and more specific one:

    A REQUEST WITH NO TENANT CONTEXT MUST FAIL LOUDLY, ON EVERY ENDPOINT,
    AND MUST NEVER PRODUCE A SUCCESSFUL-LOOKING EMPTY RESULT.

Per-router tests already check the pieces they own. They cannot check this,
because the failure mode is *an endpoint someone forgot*. A router added next
month with a hand-rolled connection would pass every test in test_jobs.py and
still be blind. Hence the shape of this module: the endpoint list is derived
from the app's own OpenAPI schema, and `test_every_endpoint_is_covered`
fails if the app exposes a path this matrix does not exercise. Adding an
endpoint without deciding what it does under no context breaks the build.

════════════════════════════════════════════════════════════════════════════
WHY THE REQUESTS USE REAL IDS — the point that makes this evidence
════════════════════════════════════════════════════════════════════════════

A 401 against a made-up uuid proves nothing; the endpoint might be refusing
the id. Every request in the matrix therefore addresses a row that REALLY
EXISTS and belongs to the caller's own college, built by the fixtures. Each
one is fired twice:

    with a credential    -> must NOT be 401/403  (the request is good)
    with no credential   -> must be 401          (the context is what failed)

That pairing is what turns "it returned 401" into "it returned 401 *because
the tenant context was missing*". Without the positive half, this whole file
could pass against an API that rejected every request it received.

════════════════════════════════════════════════════════════════════════════
THE TWO LAYERS, AND WHICH ONE IS RUNNING
════════════════════════════════════════════════════════════════════════════

Until migration 016 this section was a caveat: `PGUSER=postgres` is a
superuser, Postgres exempts SUPERUSER and BYPASSRLS roles from row-level
security, `FORCE ROW LEVEL SECURITY` closes only the table-OWNER loophole, and
so **every policy in migrations 003/014/015 was inert**. Two different
`app.current_college_id` values saw identical rows, and the only thing
isolating tenants was the explicit `college_id` predicate in
`core/jobs.py::get_job`, `core/uploads.py` and `api/services/evaluation.py`.

Migration 016 creates a NOSUPERUSER/NOBYPASSRLS application role that owns
nothing, and `.env.example` points `PGUSER` at it. Both layers now run:

  * the identity/context layer — role-independent, always enforced;
  * the RLS layer proper — asserted by `test_rls_policies_isolate_tenants`,
    which now FAILS (it used to skip) if the configured role bypasses RLS,
    because that is a misconfiguration rather than an accepted state;
  * the explicit predicates — asserted by
    `test_the_isolating_predicate_is_not_only_rls`, which drives the query on
    a connection where RLS is deliberately NOT filtering, so that deleting the
    predicate as "redundant with the policy" fails a test. Belt and braces:
    the braces are fastened now, and the belt is still load-bearing the moment
    someone points PGUSER back at a superuser.

════════════════════════════════════════════════════════════════════════════
"THE OTHER TENANT" IS A REAL COLLEGE
════════════════════════════════════════════════════════════════════════════

Every cross-tenant assertion here uses `COLLEGE_B` — seed_minimal.sql's second
REAL college, with its own student, exam and rows. It used to use an id that
existed nowhere, which made these tests strictly weaker than they read: since
`get_tenant_conn` began verifying the college, an absent id is refused at the
edge with 401, so a "404 for the other tenant" assertion would have been
proving that an unknown caller is rejected — and would have kept passing with
tenant isolation entirely removed. An absent id (`COLLEGE_ABSENT`) still
appears here, but only where the 401 itself is the property under test.
"""
from __future__ import annotations

import base64
import datetime as dt
import json as json_module
import uuid

import httpx
import pytest

import core.db
import core.jobs
from api.deps.db import set_admin_context, set_tenant_context
from api.deps.identity import create_access_token
from api.main import create_app

from .conftest import (
    COLLEGE_A,
    COLLEGE_ABSENT,
    COLLEGE_B,
    COLLEGE_SUSPENDED,
    MINIMAL_PDF,
)

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


# ════════════════════════════ the endpoint matrix ═══════════════════════════

class Endpoint:
    """One callable endpoint, with a request good enough to succeed.

    `touches_tenant_data` records whether the endpoint reads or writes the
    RLS-protected answer schema (True) or only the shared question/paper
    schema (False). BOTH must reject a request with no context — the shared
    bank is shared between *authenticated* colleges, not with the public — but
    only the first group can leak another tenant's rows, so the two are
    distinguished rather than blurred.

    `authenticated` is False for the handful of routes that BY DESIGN answer
    without a bearer token: `GET /health` and the credential-granting half of
    `/auth`. They are still listed — the coverage test below is what forces a
    new endpoint's author to decide which group it is in — but the paired
    "with a credential / without one" assertions do not apply to them, since
    for those routes the credential is the request body. What they do instead
    is asserted in test_auth.py, and
    `test_the_unauthenticated_surface_is_exactly_these_four` pins the list so
    it cannot grow by accident.

    `needs_tenant` is a SECOND and narrower distinction: `GET /auth/me` needs
    a valid token but opens no connection and reads no row, so a token whose
    college has since been suspended is still a perfectly good answer to "who
    am I" while being refused everywhere else. It is the one endpoint for
    which "the credential is bad" and "the tenant is bad" are different
    questions, and the parametrization below asks it the one it can answer.
    """

    def __init__(self, method, path, *, json=None, files=None,
                 touches_tenant_data, note, authenticated=True,
                 needs_tenant=True):
        self.method = method
        self.path = path
        self.json = json
        self.files = files
        self.touches_tenant_data = touches_tenant_data
        self.note = note
        self.authenticated = authenticated
        self.needs_tenant = needs_tenant

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"

    async def send(self, client: httpx.AsyncClient, **kwargs) -> httpx.Response:
        return await client.request(
            self.method, self.path, json=self.json, files=self.files, **kwargs
        )

    def __repr__(self) -> str:
        return self.id


def build_matrix(*, booklet, job_id, question_id, paragraph_id, pattern_id) -> list[Endpoint]:
    """Every endpoint the app exposes, addressed at REAL rows.

    Bodies are the minimum that passes schema validation AND names existing
    resources, so a credentialed call is a genuine success rather than a 404
    or a 422 that would mask whatever the no-credential call proves.
    """
    return [
        Endpoint(
            "POST", "/api/v1/upload",
            files={"file": ("booklet.pdf", MINIMAL_PDF, "application/pdf")},
            touches_tenant_data=True,
            note="writes booklet_uploads, which carries college_id",
        ),
        Endpoint(
            "POST", "/api/v1/evaluate",
            json={
                "upload_id": booklet["upload_id"],
                "exam_id": booklet["exam_id"],
                "student_id": booklet["student_id"],
                "paper_id": booklet["paper_id"],
                "stub": True,
            },
            touches_tenant_data=True,
            note="reads booklet_uploads, writes evaluation_jobs",
        ),
        Endpoint(
            "GET", "/api/v1/jobs",
            touches_tenant_data=True,
            note="lists evaluation_jobs (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", f"/api/v1/jobs/{job_id}",
            touches_tenant_data=True,
            note="reads evaluation_jobs (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", "/api/v1/results",
            touches_tenant_data=True,
            note="lists answers (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", f"/api/v1/results/{booklet['answer_id']}",
            touches_tenant_data=True,
            note="reads answers/evaluation_results/answer_reviews",
        ),
        Endpoint(
            "GET", "/api/v1/uploads",
            touches_tenant_data=True,
            note="lists booklet_uploads (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", "/api/v1/exams",
            touches_tenant_data=True,
            note="lists exams (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", "/api/v1/students",
            touches_tenant_data=True,
            note="lists students (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", "/api/v1/papers",
            touches_tenant_data=False,
            note="shared bank of generated papers — same as /questions",
        ),
        Endpoint(
            "POST", f"/api/v1/results/{booklet['answer_id']}/override",
            json={"action": "confirmed"},
            touches_tenant_data=True,
            note="writes answer_reviews + answer_status_history",
        ),
        Endpoint(
            "GET", "/api/v1/questions",
            touches_tenant_data=False,
            note="shared bank — shared between authenticated colleges, not public",
        ),
        Endpoint(
            "GET", f"/api/v1/questions/{question_id}",
            touches_tenant_data=False,
            note="shared bank",
        ),
        Endpoint(
            "POST", "/api/v1/questions/generate",
            json={"paragraph_id": paragraph_id, "count": 1, "stub_llm": True},
            touches_tenant_data=False,
            note="writes draft questions into the shared bank",
        ),
        Endpoint(
            "POST", f"/api/v1/questions/{question_id}/review",
            json={"action": "confirm"},
            touches_tenant_data=False,
            note="GATE 1 — an unauthenticated caller must not move a question",
        ),
        Endpoint(
            "POST", f"/api/v1/questions/{question_id}/promote",
            touches_tenant_data=False,
            note="GATE 2 — an unauthenticated caller must not publish a question",
        ),
        Endpoint(
            "POST", "/api/v1/papers/generate",
            json={"pattern_id": pattern_id, "name": "rls probe paper"},
            touches_tenant_data=False,
            note="writes generated_papers from the shared bank",
        ),
        Endpoint(
            "GET", "/api/v1/auth/me",
            touches_tenant_data=False, needs_tenant=False,
            note="reads only the token's own claims — no connection at all",
        ),

        # ── the unauthenticated surface, in full ────────────────────────────
        Endpoint(
            "GET", "/health",
            touches_tenant_data=False, authenticated=False,
            note="liveness probe: no credential, and no database either",
        ),
        Endpoint(
            "POST", "/api/v1/auth/login",
            json={"email": "nobody@nowhere.example", "password": "wrong"},
            touches_tenant_data=False, authenticated=False,
            note="THE credential-granting endpoint — a bearer token here "
                 "would be a chicken and an egg",
        ),
        Endpoint(
            "POST", "/api/v1/auth/refresh",
            json={"refresh_token": "not a real refresh token"},
            touches_tenant_data=False, authenticated=False,
            note="authenticated BY THE REFRESH TOKEN in its body, which is a "
                 "credential the access token cannot stand in for — the whole "
                 "point of this endpoint is that the access token has expired",
        ),
        Endpoint(
            "POST", "/api/v1/auth/logout",
            json={"refresh_token": "not a real refresh token"},
            touches_tenant_data=False, authenticated=False,
            note="revokes the refresh token in its body; 204 whether or not "
                 "that token was live, so it is not an oracle",
        ),
    ]


@pytest.fixture
def bare_client(_dotenv, api_settings):
    """A client that sends NO credential by default.

    `make_client` always attaches the debug header, and httpx MERGES a
    client's default headers into every request — popping the key from a
    per-request dict does not remove it, it is merged straight back in. So a
    test that needs the header genuinely absent cannot use `make_client` at
    all; it needs a client that never had one. (This was a real bug in the
    first draft of this file: the "no header" case was silently sending
    COLLEGE_A's credential and passing for the wrong reason.)
    """
    app = create_app(api_settings)

    def make(*, raise_app_exceptions: bool = True, **headers: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, raise_app_exceptions=raise_app_exceptions),
            base_url="http://testserver",
            headers=headers,
        )

    return make


@pytest.fixture
def matrix(make_booklet, make_job, make_question, make_paragraph, make_pattern):
    """The matrix, built over rows that exist and belong to COLLEGE_A."""
    booklet = make_booklet(college_id=COLLEGE_A)
    return build_matrix(
        booklet=booklet,
        job_id=make_job(college_id=COLLEGE_A)["job_id"],
        # 'confirmed' so the review gate refuses it for a GATE reason (409) if
        # it ever runs, and promote would succeed — neither may happen without
        # a credential, which is the whole point.
        question_id=make_question(status="confirmed"),
        paragraph_id=make_paragraph(),
        pattern_id=make_pattern(),
    )


# ══════════════════════ the matrix is the whole surface ═════════════════════

async def test_every_endpoint_is_covered_by_this_module(matrix, api_settings):
    """The app must expose no path this file does not exercise.

    This is the assertion that makes the rest of the module a spec instead of
    a snapshot. The failure it is built for is not a broken endpoint — it is a
    NEW one, written next month, whose author never considered what it does
    with no tenant context. Deriving the list from the app's own OpenAPI
    schema means that endpoint fails this test on the day it is added, while
    the decision is still cheap.

    It also fails if a path is REMOVED and left in the matrix, which keeps the
    two from drifting in the other direction.
    """
    app = create_app(api_settings)
    exposed = {
        f"{method.upper()} {path}"
        for path, ops in app.openapi()["paths"].items()
        for method in ops
    }

    # Path templates ("/api/v1/jobs/{job_id}") vs the concrete paths the
    # matrix calls ("/api/v1/jobs/<uuid>"), compared by their shape.
    def shape(entry: str) -> str:
        method, path = entry.split(" ", 1)
        parts = []
        for part in path.split("/"):
            try:
                uuid.UUID(part)
            except ValueError:
                parts.append("{}" if part.startswith("{") else part)
            else:
                parts.append("{}")
        return f"{method} {'/'.join(parts)}"

    covered = {shape(e.id) for e in matrix}
    exposed_shapes = {shape(e) for e in exposed}

    missing = exposed_shapes - covered
    assert not missing, (
        f"These endpoints exist but are not in this module's matrix, so nothing "
        f"asserts what they do without a tenant context: {sorted(missing)}. "
        f"Add them to build_matrix() — see the module docstring."
    )

    stale = covered - exposed_shapes
    assert not stale, (
        f"The matrix exercises endpoints the app no longer exposes: {sorted(stale)}."
    )


async def test_no_debug_endpoints_are_exposed_in_any_environment(api_settings):
    """`/docs` must render no _debug routes, with debug settings ON or OFF.

    api/routers/debug.py was deleted in the Day 5 hardening pass; the probe it
    provided is now mounted onto the test app by conftest.py instead. This
    asserts the deletion rather than the old env-gate: a gated debug endpoint
    is one mis-set variable from being live, and `enable_debug_endpoints` is
    still a real setting (it gates `stub`/`stub_llm`), so someone turning it
    on in production must not thereby publish an identity probe.
    """
    for enabled in (True, False):
        app = create_app(api_settings.model_copy(update={"enable_debug_endpoints": enabled}))
        paths = list(app.openapi()["paths"])

        assert not [p for p in paths if "_debug" in p or "whoami" in p], (
            f"a debug endpoint is exposed with enable_debug_endpoints={enabled}: {paths}"
        )


# ═══════════════ THE CORE PROPERTY: no context -> a loud refusal ════════════

async def test_every_endpoint_works_with_a_credential(matrix, make_client,
                                                      track_jobs, track_uploads,
                                                      admin_conn):
    """The positive half of the pairing, and it is not optional.

    Every request in the matrix must be accepted when it carries a credential.
    If any of them were malformed — a bad uuid, a missing field, a nonexistent
    row — the no-credential test below would still see a 4xx and pass, while
    proving nothing at all about tenant context. This test is what forbids
    that.

    The assertion is deliberately weak on the exact status (a 409 from a
    review gate is a fine outcome here) and strict on the only thing that
    matters: it must not be 401 or 403, i.e. it must get PAST the identity
    layer.
    """
    created_jobs: list[str] = []

    async with make_client(COLLEGE_A) as client:
        for endpoint in matrix:
            if not endpoint.authenticated:
                # These are exercised in test_auth.py, where the credential
                # is the body rather than the header. Firing /auth/login with
                # a bearer token would assert nothing about either.
                continue

            response = await endpoint.send(client)

            assert response.status_code not in (401, 403), (
                f"{endpoint.id} was rejected at the identity layer even WITH a "
                f"credential ({response.status_code}): {response.text[:300]}. "
                f"Fix the request in build_matrix() — until it is accepted, the "
                f"no-credential assertions below prove nothing."
            )
            assert response.status_code < 500, (
                f"{endpoint.id} 500ed with a credential: {response.text[:300]}"
            )

            # Keep the committed side effects out of the next test run.
            body = response.json() if response.headers.get(
                "content-type", "").startswith("application/json") else {}
            if isinstance(body, dict):
                if "job_id" in body:
                    track_jobs(body["job_id"])
                    created_jobs.append(body["job_id"])
                if "upload_id" in body:
                    track_uploads(body["upload_id"])

    # POST /evaluate leaves a REAL queued booklet_eval job. `track_jobs` would
    # delete it at teardown, but teardown is too late: any test that runs a
    # worker in between claims the oldest queued job of that type, and would
    # get this one instead of its own — a failure that looks like a worker bug
    # and is really a fixture leak. So it goes now, not at teardown.
    if created_jobs:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("DELETE FROM evaluation_jobs WHERE job_id = ANY(%s::uuid[])",
                        ([str(j) for j in created_jobs],))
        admin_conn.commit()


#: The credential kinds where the TOKEN is impeccable and only the college it
#: names is unusable. Everything else in the parametrization below is a token
#: that fails on its own terms (missing, forged, expired, self-contradictory).
TENANT_ONLY_FAILURES = frozenset({"nil-uuid", "unknown-college", "suspended-college"})


@pytest.fixture
def unusable_credential(_dotenv, api_settings, admin_conn, suspended_college):
    """Factory: every way of arriving without a usable tenant, as headers.

    Each case is a DIFFERENT layer failing, and they are enumerated rather
    than represented by one "bad token" because the layers fail differently:
    a missing header never reaches the decoder, a forged one fails the
    signature check, and a perfectly signed token with no `cid` passes every
    cryptographic check and would have sailed through to the connection.
    That last one is the case this whole module exists for.
    """
    from jose import jwt

    from .conftest import _identity_for, _sign

    def token_for(college_id, role="teacher"):
        return _sign(_identity_for(admin_conn, college_id, role), api_settings)

    def signed(claims: dict) -> str:
        """A token signed with the APP'S OWN KEY — so what refuses it is the
        claim check, never the signature."""
        base = {
            "sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
            "email": "someone@example.edu", "typ": "access",
            "exp": 4102444800,
        }
        return jwt.encode({**base, **claims}, api_settings.jwt_signing_key,
                          algorithm=api_settings.jwt_algorithm)

    def make(kind: str) -> dict:
        if kind == "absent":
            return {}
        if kind == "empty":
            return {"Authorization": ""}
        if kind == "whitespace":
            return {"Authorization": "Bearer    "}
        if kind == "not-a-token":
            return {"Authorization": "Bearer not-a-token"}
        if kind == "wrong-scheme":
            # A valid token presented as Basic auth. HTTPBearer must not
            # accept it, or "Authorization: Basic <base64>" from a browser's
            # own prompt would be read as a bearer token.
            return {"Authorization": f"Basic {token_for(COLLEGE_A)}"}
        if kind == "forged":
            return {"Authorization": "Bearer " + jwt.encode(
                {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
                 "email": "x@y.z", "role": "teacher", "cid": COLLEGE_A,
                 "typ": "access", "exp": 4102444800},
                "a key this app has never seen", algorithm="HS256")}
        if kind == "expired":
            identity = _identity_for(admin_conn, COLLEGE_A, "teacher")
            token, _ = create_access_token(
                user_id=identity["user_id"], reviewer_id=identity["reviewer_id"],
                email=identity["email"], role=identity["role"],
                college_id=identity["college_id"], settings=api_settings,
                now=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2))
            return {"Authorization": f"Bearer {token}"}
        if kind == "no-college":
            return {"Authorization": f"Bearer {signed({'role': 'teacher', 'cid': None})}"}
        if kind == "null-college":
            return {"Authorization": f"Bearer {signed({'role': 'teacher', 'cid': 'null'})}"}
        if kind == "unknown-role":
            return {"Authorization": f"Bearer {signed({'role': 'superuser', 'cid': COLLEGE_A})}"}
        if kind == "nil-uuid":
            return {"Authorization": "Bearer " + token_for(
                "00000000-0000-0000-0000-000000000000")}
        if kind == "unknown-college":
            return {"Authorization": f"Bearer {token_for(COLLEGE_ABSENT)}"}
        if kind == "suspended-college":
            return {"Authorization": f"Bearer {token_for(COLLEGE_SUSPENDED)}"}
        raise AssertionError(f"unknown credential kind {kind!r}")

    return make


@pytest.mark.parametrize(
    "kind, why",
    [
        ("absent", "no Authorization header at all"),
        ("empty", "an empty Authorization header"),
        ("whitespace", "a Bearer scheme with nothing after it"),
        ("not-a-token", "a credential that is not a JWT"),
        ("wrong-scheme", "a REAL token presented as Basic rather than Bearer"),
        ("forged", "a perfectly-shaped token signed with the wrong key — the "
                   "one case where only the signature check can refuse it"),
        ("expired", "a token this app really signed, two days ago"),
        ("no-college", "OUR OWN SIGNATURE, role=teacher, and NO college: the "
                       "token that would set no tenant context and make every "
                       "query return zero rows"),
        ("null-college", "the literal string 'null' as the college claim, "
                         "which a JS client produces by accident"),
        ("unknown-role", "a role no migration defines, which must not be "
                         "treated as 'some role with no privileges'"),
        ("nil-uuid", "the nil uuid — well-formed, and no college has it"),
        ("unknown-college", "a plausible uuid naming a college that does not exist"),
        ("suspended-college", "a REAL college the platform has suspended"),
    ],
)
async def test_no_usable_context_is_a_loud_refusal_on_every_endpoint(
    matrix, bare_client, unusable_credential, kind, why
):
    """THE TEST THIS FILE EXISTS FOR.

    For every endpoint, and every way of arriving without a usable tenant:
    the response must be an explicit refusal. Not a 200. Not an empty list.
    Not a 404 that reads like "your data isn't here". And the message must
    name the problem, so an operator reading a log sees a credential failure
    rather than a mysterious empty database.

    THE CASE TO READ FIRST IS `no-college`. It is a token THIS APP SIGNED,
    unexpired, structurally perfect, whose role is `teacher` and whose `cid`
    is null. Every cryptographic check passes. If `user_from_claims` did not
    refuse it, `get_tenant_conn` would be handed college_id=None — and the
    honest-looking result would be a 200 with an empty list on every read
    endpoint in this matrix. That is the §6 failure arriving through the front
    door with a valid signature, and it is why the check is at the edge rather
    than left to the database.

    The unknown/suspended college cases used to be the interesting exception
    here and are not any more: `get_tenant_conn` runs one SELECT against
    `colleges` before `SET LOCAL`, so they are refused at the edge like every
    other unusable credential.
    """
    headers = unusable_credential(kind)

    async with bare_client(**headers) as client:
        for endpoint in matrix:
            if not endpoint.authenticated:
                continue
            if kind in TENANT_ONLY_FAILURES and not endpoint.needs_tenant:
                # The token itself is fine here — it is the TENANT it names
                # that cannot act. GET /auth/me neither opens a connection nor
                # reads a row, so it answers from the claims and is right to.
                # That is asserted positively below rather than skipped
                # silently.
                continue

            response = await endpoint.send(client)

            assert response.status_code == 401, (
                f"{endpoint.id} with {why} returned {response.status_code}, not 401.\n"
                f"  endpoint: {endpoint.note}\n"
                f"  body: {response.text[:400]}\n"
                f"A request with no usable tenant context must be REFUSED at the "
                f"edge. Anything else — a 200, an empty list, a 404 — is the "
                f"silent-empty failure CLAUDE_CONTEXT.md §6 describes."
            )
            # The standard header that says which scheme to use. Its absence
            # is what makes a 401 unactionable for a generic HTTP client.
            assert response.headers.get("WWW-Authenticate") == "Bearer", (
                f"{endpoint.id}: 401 without a WWW-Authenticate header")

            detail = response.json().get("detail", "")
            assert any(word in detail.lower() for word in
                       ("token", "college", "role", "bearer")), (
                f"{endpoint.id}: the 401 does not say what was wrong "
                f"({detail!r}). A bare 'unauthorized' sends the next reader "
                f"looking at the database."
            )

            # And nothing that reads as a successful, empty answer.
            body = response.json()
            assert body != [] and body != {}, f"{endpoint.id} returned an empty success shape"
            assert "questions" not in body and "job_id" not in body, (
                f"{endpoint.id} returned data-shaped keys on a refusal: {body}"
            )


@pytest.mark.parametrize("kind", sorted(TENANT_ONLY_FAILURES))
async def test_auth_me_still_answers_when_only_the_TENANT_is_unusable(
    matrix, bare_client, unusable_credential, kind
):
    """The other half of the skip above, asserted rather than assumed.

    A token for a suspended (or nonexistent) college is a VALID token: this
    app signed it, it has not expired, and every claim in it is consistent.
    What is wrong is the tenant, and `GET /auth/me` does not act as a tenant —
    it reports the caller's own claims and opens no connection. So it answers
    200 while every endpoint that touches data answers 401, and both are
    correct.

    This test exists so that the `continue` in the parametrization above is a
    stated exception with a reason, rather than a hole nobody notices when
    /auth/me grows a database read. The day it does, this fails and the skip
    has to be revisited — which is exactly the right time.
    """
    async with bare_client(**unusable_credential(kind)) as client:
        response = await client.get("/api/v1/auth/me")

    assert response.status_code == 200, response.text
    body = response.json()
    # It reports the caller's OWN identity and nothing else — no college
    # name, no status, nothing about the tenant it could not have read.
    assert set(body) == {"user_id", "reviewer_id", "email", "role", "college_id"}


async def test_the_unauthenticated_surface_is_exactly_these_four(matrix):
    """Only /health and the three /auth entry points answer without a token.

    The matrix above SKIPS the endpoints marked `authenticated=False`, which
    would be a hole in it if that flag could be set casually: an endpoint
    added with `authenticated=False` would silently stop being checked for
    tenant isolation. So the exempt list is pinned here by name. Adding to it
    is a deliberate act with a failing test in front of it, which is the point.

    Each of the four is exempt for a DIFFERENT and stated reason:
      /health          reports nothing but its own aliveness, and no database.
      /auth/login      IS how a credential is obtained.
      /auth/refresh    is authenticated by the refresh token in its body; the
                       access token is expired by definition when it is called.
      /auth/logout     acts on the refresh token in its body, and answers 204
                       either way, so it reveals nothing.
    """
    exempt = {e.id for e in matrix if not e.authenticated}
    assert exempt == {
        "GET /health",
        "POST /api/v1/auth/login",
        "POST /api/v1/auth/refresh",
        "POST /api/v1/auth/logout",
    }, exempt


async def test_a_refused_request_writes_nothing(matrix, bare_client, admin_conn):
    """A refusal must also be a no-op.

    The write endpoints are the ones where a silent-context bug does lasting
    damage: an INSERT under no context is rejected by the RLS WITH CHECK
    clause, but an INSERT under the WRONG context succeeds and writes into
    another college. This snapshots every table the matrix can write and
    asserts the counts are identical after firing every endpoint without a
    credential.

    Counts rather than contents because the tables are shared with other tests
    running in the same database; a count that did not move is sufficient to
    show this request wrote nothing, and does not make the test depend on the
    rest of the suite's state.
    """
    tables = ("booklet_uploads", "evaluation_jobs", "answer_reviews",
              "answer_status_history", "questions", "question_reviews",
              "question_status_history", "generated_papers", "evaluation_results")

    def counts() -> dict[str, int]:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            out = {}
            for table in tables:
                cur.execute(f"SELECT count(*) FROM {table}")
                out[table] = cur.fetchone()[0]
            return out

    before = counts()

    async with bare_client() as client:
        for endpoint in matrix:
            if not endpoint.authenticated:
                # /auth/login and /auth/refresh are MEANT to be reachable
                # here, and login deliberately does not write. Firing them
                # would prove nothing about a refusal.
                continue
            await endpoint.send(client)

    after = counts()
    assert after == before, (
        f"Uncredentialed requests changed the database: "
        f"{ {t: (before[t], after[t]) for t in tables if before[t] != after[t]} }"
    )


# ═════════════════ escalation: a tenant cannot become an admin ══════════════

@pytest.fixture
def escalation_attempt(api_settings, admin_conn):
    """Factory: every way a tenant caller might try to claim cross-tenant access.

    All of these were headers when identity was a header. They are claims now,
    and the interesting ones are the three that a signature CANNOT refuse:
    a token we really signed, carrying an extra `is_platform_admin` claim, a
    contradictory role, or an injection payload where a college id belongs.
    Those reach the claim-reading code, which is where the refusal has to
    happen.
    """
    from jose import jwt

    from .conftest import _identity_for, _sign

    def ours(claims: dict) -> str:
        """Signed with the APP'S OWN KEY: nothing but the claim check can
        refuse it."""
        base = {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
                "email": "teacher@example.edu", "typ": "access",
                "exp": 4102444800, "role": "teacher", "cid": COLLEGE_A}
        return jwt.encode({**base, **claims}, api_settings.jwt_signing_key,
                          algorithm=api_settings.jwt_algorithm)

    def valid() -> str:
        return _sign(_identity_for(admin_conn, COLLEGE_A, "teacher"), api_settings)

    def make(kind: str) -> dict:
        if kind == "invented-header":
            return {"Authorization": f"Bearer {valid()}",
                    "X-Is-Platform-Admin": "true"}
        if kind == "official-looking":
            return {"Authorization": f"Bearer {valid()}",
                    "X-Debug-Is-Platform-Admin": "true"}
        if kind == "extra-claim":
            return {"Authorization": "Bearer " + ours({"is_platform_admin": True})}
        if kind == "admin-claim-with-college":
            # role says cross-tenant, cid says one tenant: a token that would
            # have to be half-believed to be honoured at all.
            return {"Authorization": "Bearer " + ours({"role": "platform_admin"})}
        if kind == "forged-admin":
            return {"Authorization": "Bearer " + jwt.encode(
                {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
                 "email": "a@b.c", "role": "platform_admin", "cid": None,
                 "typ": "access", "exp": 4102444800},
                "a key this app has never seen", algorithm="HS256")}
        if kind == "alg-none":
            # The classic attack, and it has to be HAND-BUILT: python-jose
            # refuses to encode `alg: none` at all ("Algorithm none not
            # supported"), which is a good default and is also why a test
            # that used its encoder would silently not be testing this.
            # `decode_access_token` pins algorithms=[HS256], so the token is
            # refused before its claims are read.
            def b64(raw: bytes) -> str:
                return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

            header = b64(json_module.dumps({"alg": "none", "typ": "JWT"}).encode())
            payload = b64(json_module.dumps(
                {"sub": str(uuid.uuid4()), "rid": str(uuid.uuid4()),
                 "email": "a@b.c", "role": "platform_admin", "cid": None,
                 "typ": "access", "exp": 4102444800}).encode())
            return {"Authorization": f"Bearer {header}.{payload}."}
        if kind == "two-ids":
            return {"Authorization": "Bearer " + ours({"cid": f"{COLLEGE_A}, {COLLEGE_B}"})}
        if kind == "sql-injection":
            return {"Authorization": "Bearer " + ours({"cid": f"{COLLEGE_A}' OR '1'='1"})}
        if kind == "set-injection":
            return {"Authorization": "Bearer " + ours(
                {"cid": f"{COLLEGE_A}'; SET app.is_platform_admin='true"})}
        if kind == "case-variation":
            return {"Authorization": "Bearer " + ours({"cid": COLLEGE_A.upper()})}
        raise AssertionError(f"unknown escalation kind {kind!r}")

    return make


@pytest.mark.parametrize(
    "kind, why",
    [
        ("invented-header", "a header inventing the admin claim beside a real token"),
        ("official-looking", "the same, named to look official"),
        ("extra-claim", "OUR OWN SIGNATURE plus an is_platform_admin claim the "
                        "code does not read"),
        ("admin-claim-with-college", "our signature, role=platform_admin, and a "
                                     "college — a token that contradicts itself"),
        ("forged-admin", "a platform_admin token signed with the wrong key"),
        ("alg-none", "an UNSIGNED token claiming platform_admin — the alg:none attack"),
        ("two-ids", "two college ids in one claim, hoping one is honoured"),
        ("sql-injection", "SQL injection where the college id goes"),
        ("set-injection", "statement injection aimed at SET LOCAL itself"),
        ("case-variation", "case variation, in case the comparison is textual"),
    ],
)
async def test_no_request_can_escalate_to_platform_admin(
    whoami_app, escalation_attempt, kind, why
):
    """A tenant caller must never acquire `app.is_platform_admin`.

    That GUC activates migration 003's `platform_admin_bypass` policy, which
    makes every college's rows visible on one connection. It is set by
    `get_tenant_conn` for exactly one input — a token whose verified `role`
    claim is `platform_admin` — so this test is the standing proof that no
    OTHER input reaches it.

    The whoami probe is used because it reports what POSTGRES believes about
    the transaction, not what the API says about itself. An escalation that
    fooled the API but not the database, or vice versa, is visible here either
    way.

    The injection cases matter specifically because `SET LOCAL` cannot take a
    bind parameter — api/deps/db.py interpolates client-side through psycopg2
    — so the only thing between a claim and a SET statement is the
    `uuid.UUID()` parse in identity.py. These assert that parse is doing its
    job, now on a claim rather than on a header.

    `extra-claim` is the one to read: it is a token THIS APP SIGNED, with an
    `is_platform_admin: true` claim added. It is harmless only because
    `user_from_claims` derives admin-ness from `role` and never reads that
    key — `CurrentUser.is_platform_admin` is a property, not a field, so
    there is no attribute for a stray claim to land in.
    """
    headers = escalation_attempt(kind)

    transport = httpx.ASGITransport(app=whoami_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/_test/whoami", headers=headers)

    if response.status_code == 401:
        return  # refused outright — the strongest possible outcome

    assert response.status_code == 200, (
        f"{why}: unexpected {response.status_code} — {response.text[:300]}"
    )
    body = response.json()

    assert body["identity"]["is_platform_admin"] is False, (
        f"{why} produced an API-level admin claim: {body['identity']}"
    )
    assert body["db"]["is_platform_admin"] in (None, "", "false"), (
        f"{why} SET app.is_platform_admin in Postgres: {body['db']}. "
        f"This is a full cross-tenant bypass."
    )


async def test_the_tenant_comes_from_identity_not_from_the_request(make_client,
                                                                  make_job,
                                                                  college_b):
    """A college named in a body, query string or path must be ignored.

    RLS scopes a request to whatever college id it is handed, so the one place
    that id may come from is the authenticated user. Two halves:

      * A's own job, asked for with `?college_id=<B>` — the answer must be
        identical to asking without it, i.e. the parameter buys nothing;
      * B's job, asked for by an A credential naming B in the query string —
        the 404 is what says the parameter cannot MOVE the tenant. That leg
        needed a real college B: against an id that owned no rows, "still 404"
        was true no matter what the endpoint did with the parameter.
    """
    own_job = make_job(college_id=COLLEGE_A)["job_id"]
    b_job = make_job(college_id=college_b["college_id"])["job_id"]

    async with make_client(COLLEGE_A) as client:
        with_query = await client.get(
            f"/api/v1/jobs/{own_job}", params={"college_id": COLLEGE_B}
        )
        plain = await client.get(f"/api/v1/jobs/{own_job}")
        borrowed = await client.get(
            f"/api/v1/jobs/{b_job}", params={"college_id": COLLEGE_B}
        )

    assert with_query.status_code == plain.status_code == 200
    assert with_query.json() == plain.json(), (
        "a college_id query parameter changed the response — the tenant must "
        "come from identity alone"
    )
    assert borrowed.status_code == 404, (
        f"naming college B in the query string reached B's job from an A "
        f"credential ({borrowed.status_code}): {borrowed.text[:300]}"
    )
    # The 404 body echoes the id that was ASKED for, exactly as it does for an
    # id that exists nowhere (test_another_colleges_row_is_indistinguishable_
    # from_a_missing_one pins that), so the id appearing here is not a leak —
    # what must not appear is anything ABOUT B's job.
    assert "blob_url" not in borrowed.text and "booklet_evaluation" not in borrowed.text


async def test_another_colleges_row_is_indistinguishable_from_a_missing_one(
    make_client, make_job, college_b
):
    """Rule 3, asserted as a single comparison rather than by eyeballing two
    tests: a real job in another college and a uuid that exists nowhere must
    produce byte-identical responses.

    Any difference — status, body, even the phrasing of the message — turns
    the endpoint into an existence oracle for other tenants' data.
    """
    foreign_job = make_job(college_id=COLLEGE_A)["job_id"]
    nonexistent = uuid.uuid4()

    async with make_client(COLLEGE_B) as client:
        foreign = await client.get(f"/api/v1/jobs/{foreign_job}")
        missing = await client.get(f"/api/v1/jobs/{nonexistent}")

    assert foreign.status_code == missing.status_code == 404
    # Normalise the id out of each message; what remains must be identical.
    assert foreign.json()["detail"].replace(str(foreign_job), "X") == \
           missing.json()["detail"].replace(str(nonexistent), "X")


# ════════════════════ platform admin: the sanctioned bypass ═════════════════

async def test_platform_admin_sees_across_colleges(make_job, admin_conn):
    """`get_admin_conn`'s context is what the batch/system paths run under, and
    it must see every tenant — that is its entire purpose (CLAUDE_CONTEXT.md
    §6; scripts/evaluate_pending.py and core/booklet_persist.py rely on it).

    Asserted at the connection layer because NO ENDPOINT is wired to
    `get_admin_conn`, deliberately: identity.py cannot authorize an admin yet,
    so exposing a cross-tenant connection over HTTP would be a bypass with no
    authorization in front of it. The day an admin endpoint is added, this
    test is already here to say what it may see.
    """
    job_a = make_job(college_id=COLLEGE_A)["job_id"]

    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("SELECT count(*) FROM evaluation_jobs WHERE job_id = %s",
                        (str(job_a),))
            (visible,) = cur.fetchone()
            cur.execute("SELECT count(DISTINCT college_id) FROM evaluation_jobs")
            (colleges,) = cur.fetchone()
    finally:
        conn.close()

    assert visible == 1, "the platform-admin context could not see a real job"
    assert colleges >= 1


async def test_the_isolating_predicate_is_not_only_rls(make_job, college_b):
    """THE BELT, TESTED WITH THE BRACES DELIBERATELY UNFASTENED.

    `core/jobs.py::get_job` filters `AND college_id = %s` itself, and migration
    016 does not make that redundant. It was the ONLY thing isolating tenants
    on every pre-016 deployment (superuser PGUSER ⇒ inert policies), and it is
    the only thing left the moment anyone points PGUSER back at a superuser —
    a one-line .env edit with no visible symptom.

    So this asserts the predicate WITHOUT letting RLS do the work: the query
    runs on a platform-admin connection, where 014's permissive bypass policy
    makes every college's jobs visible. Anything that refuses the row there is
    the predicate, because there is nothing else left. Deleting it as
    "redundant with the policy" fails here rather than silently removing a
    layer.

    The wrong-tenant-context leg is kept as well: it is the shape the pre-016
    version of this test had, and it is what a superuser deployment still
    exercises.
    """
    job_a = make_job(college_id=COLLEGE_A)["job_id"]

    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            # RLS deliberately NOT filtering: the admin bypass policy is
            # permissive, so this connection can see every tenant's jobs.
            set_admin_context(cur)
            leaked = core.jobs.get_job(cur, job_id=job_a, college_id=COLLEGE_B)
            # Same connection, same context, asking as the real owner — proof
            # the row was reachable and the predicate is what refused it.
            owned = core.jobs.get_job(cur, job_id=job_a, college_id=COLLEGE_A)

            # And with the policy fastened too, which is the deployment's
            # actual configuration since 016.
            set_tenant_context(cur, COLLEGE_B)
            leaked_under_rls = core.jobs.get_job(cur, job_id=job_a,
                                                 college_id=COLLEGE_B)
    finally:
        conn.close()

    assert leaked is None, (
        "core/jobs.py::get_job returned another college's job on a connection "
        "where RLS was bypassed. Its college_id predicate is the only "
        "isolation left on a superuser deployment — see migrations/"
        "016_application_role.sql."
    )
    assert leaked_under_rls is None
    assert owned is not None, "the job should be visible to its own college"


async def test_rls_policies_isolate_tenants(db_conn, tenant_conn, college_b,
                                            make_booklet):
    """THE RLS LAYER PROPER — and it RUNS now.

    This test used to skip, loudly, on every deployment: `PGUSER=postgres` is a
    superuser, and Postgres exempts SUPERUSER/BYPASSRLS roles from row-level
    security entirely, so migrations 003/014/015's policies were inert.
    Migration 016 creates the NOSUPERUSER/NOBYPASSRLS application role they
    always needed, and `.env.example` points PGUSER at it.

    So a bypassing role is now a MISCONFIGURATION, not a state to skip on, and
    this fails instead of skipping. A skip here would mean the suite reports
    green while the property this whole file is about — two tenants, two sets
    of rows — is untested.

    The shape is the success criterion itself: two REAL colleges, each with a
    real answer of its own, and one query — the same query — run under two
    values of `app.current_college_id`, returning different rows. The old
    version compared A's rows against a college that owned nothing, where "0
    rows" is also what a broken query, an empty table, or a missing GUC
    returns.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT current_user, rolsuper OR rolbypassrls FROM pg_roles "
            "WHERE rolname = current_user"
        )
        role, bypasses = cur.fetchone()

    assert not bypasses, (
        f"PGUSER={role!r} bypasses RLS (superuser or BYPASSRLS), so the tenant "
        f"policies in migrations 003/014/015 are INERT and two colleges see "
        f"the same rows. This is no longer a tolerated configuration: run "
        f"migrations/016_application_role.sql and point PGUSER at the "
        f"application role it creates (see .env.example). Isolation is "
        f"currently held up only by the explicit college_id predicates."
    )

    a = make_booklet(college_id=COLLEGE_A)
    b = make_booklet(college_id=college_b["college_id"],
                     student_id=college_b["student_id"],
                     exam_id=college_b["exam_id"])

    def answers_visible_to(college_id) -> set[str]:
        conn = tenant_conn(college_id)
        with conn.cursor() as cur:
            cur.execute("SELECT answer_id::text FROM answers")
            return {row[0] for row in cur.fetchall()}

    seen_by_a = answers_visible_to(COLLEGE_A)
    seen_by_b = answers_visible_to(uuid.UUID(COLLEGE_B))

    # Each tenant sees its own row...
    assert a["answer_id"] in seen_by_a
    assert b["answer_id"] in seen_by_b
    # ...and not the other's. This is the whole point of the file.
    assert a["answer_id"] not in seen_by_b, (
        "college B's connection can read college A's answer — RLS is not "
        "isolating tenants"
    )
    assert b["answer_id"] not in seen_by_a
    assert seen_by_a and seen_by_b and seen_by_a != seen_by_b

    # A connection with NO tenant context at all sees nothing — the fail-closed
    # behaviour every other module's docstring depends on, asserted once here.
    blind = core.db.get_connection()
    try:
        with blind.cursor() as cur:
            cur.execute("SELECT count(*) FROM answers")
            (visible,) = cur.fetchone()
    finally:
        blind.rollback()
        blind.close()
    assert visible == 0, (
        "a connection with no app.current_college_id could read answers — the "
        "policies are not being applied to this role"
    )
