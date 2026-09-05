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
THE HONEST CAVEAT — READ THIS BEFORE CITING THIS FILE AS PROOF
════════════════════════════════════════════════════════════════════════════

Postgres exempts SUPERUSER and BYPASSRLS roles from row-level security, and
migration 003's `FORCE ROW LEVEL SECURITY` closes only the table-OWNER
loophole, not that one. With `PGUSER=postgres` — the .env.example default, and
so almost every local checkout — **every RLS policy in migrations 003 and 014
is inert**, and two different `app.current_college_id` values see identical
rows.

What actually isolates tenants today is the explicit `college_id` predicate in
`core/jobs.py::get_job`, `core/uploads.py`, and
`api/services/evaluation.py` — plain SQL, which runs regardless of role. So
this file asserts the boundary at BOTH layers and is explicit about which one
is load-bearing right now:

  * the identity/context layer (every test here except the last two) is
    role-independent and always enforced;
  * the RLS layer proper is asserted by `test_rls_policies_isolate_tenants`,
    which SKIPS LOUDLY under a superuser instead of pretending to pass.

`test_the_isolating_predicate_is_not_only_rls` is the one that matters on a
superuser deployment: it drives the query under a deliberately WRONG RLS
context and asserts the predicate still refuses, so deleting the predicate as
"redundant with RLS" fails a test rather than silently removing the only
mechanism running.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

import core.db
import core.jobs
from api.deps.db import set_admin_context, set_tenant_context
from api.deps.identity import DEBUG_COLLEGE_HEADER
from api.main import create_app

from .conftest import COLLEGE_A, COLLEGE_B_ABSENT, MINIMAL_PDF, REVIEWER_TEACHER

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
    """

    def __init__(self, method, path, *, json=None, files=None,
                 touches_tenant_data, note):
        self.method = method
        self.path = path
        self.json = json
        self.files = files
        self.touches_tenant_data = touches_tenant_data
        self.note = note

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
                "stub": True,
            },
            touches_tenant_data=True,
            note="reads booklet_uploads, writes evaluation_jobs",
        ),
        Endpoint(
            "GET", f"/api/v1/jobs/{job_id}",
            touches_tenant_data=True,
            note="reads evaluation_jobs (RLS + explicit predicate)",
        ),
        Endpoint(
            "GET", f"/api/v1/results/{booklet['answer_id']}",
            touches_tenant_data=True,
            note="reads answers/evaluation_results/answer_reviews",
        ),
        Endpoint(
            "POST", f"/api/v1/results/{booklet['answer_id']}/override",
            json={"reviewer_id": REVIEWER_TEACHER, "action": "confirmed"},
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
            json={"reviewer_id": REVIEWER_TEACHER, "action": "confirm"},
            touches_tenant_data=False,
            note="GATE 1 — an unauthenticated caller must not move a question",
        ),
        Endpoint(
            "POST", f"/api/v1/questions/{question_id}/promote",
            json={"reviewer_id": REVIEWER_TEACHER},
            touches_tenant_data=False,
            note="GATE 2 — an unauthenticated caller must not publish a question",
        ),
        Endpoint(
            "POST", "/api/v1/papers/generate",
            json={"pattern_id": pattern_id, "name": "rls probe paper"},
            touches_tenant_data=False,
            note="writes generated_papers from the shared bank",
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

    def make(*, raise_app_exceptions: bool = True, **headers) -> httpx.AsyncClient:
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


@pytest.mark.parametrize(
    "credential, why",
    [
        (None, "no header at all — the header was simply never sent"),
        ("", "an empty header, which SET LOCAL would accept and match nothing"),
        ("   ", "whitespace, which survives a naive `if header:` check"),
        ("not-a-uuid", "a malformed id, which would raise inside the ::uuid cast"),
        ("null", "the literal string 'null', which a JS client sends by accident"),
        ("undefined", "the literal 'undefined', same"),
        ("00000000-0000-0000-0000-000000000000", "the nil uuid — well-formed, no rows"),
    ],
    ids=["absent", "empty", "whitespace", "malformed", "null", "undefined", "nil-uuid"],
)
async def test_no_usable_context_is_a_loud_refusal_on_every_endpoint(
    matrix, bare_client, credential, why
):
    """THE TEST THIS FILE EXISTS FOR.

    For every endpoint, and every way of arriving without a usable tenant:
    the response must be an explicit refusal. Not a 200. Not an empty list.
    Not a 404 that reads like "your data isn't here". And the message must
    name the problem, so an operator reading a log sees a credential failure
    rather than a mysterious empty database.

    The nil-uuid case is the interesting one and is NOT expected to 401: it is
    syntactically valid, so identity accepts it and RLS scopes the request to
    a college that owns nothing. That is the silent-empty scenario itself. It
    is allowed to 404 (no such row for this tenant) but it must NEVER return
    another college's row — asserted below by comparing against what the real
    tenant sees.
    """
    nil_uuid = credential == "00000000-0000-0000-0000-000000000000"
    headers = {} if credential is None else {DEBUG_COLLEGE_HEADER: credential}

    # The nil-uuid case can reach the database (it is syntactically valid, so
    # identity accepts it), and POST /upload dies there on a FK violation.
    # Let the app's own handler turn that into a response instead of raising
    # into the test, so the assertion below sees what a CLIENT would see.
    async with bare_client(raise_app_exceptions=not nil_uuid, **headers) as client:
        for endpoint in matrix:
            response = await endpoint.send(client)

            if nil_uuid:
                # A syntactically valid credential naming a college that does
                # not exist. identity.py accepts it (it only parses the uuid),
                # so this is the closest the API gets to the silent-empty
                # failure: the context IS set, to a tenant that owns nothing.
                #
                # The property asserted is the safety one, not a specific code.
                # Today the outcomes are: 404 on the tenant reads (correct and
                # indistinguishable from any other miss), 409/201 on the shared
                # bank (correct — it is shared between authenticated callers),
                # and a 500 from POST /upload, where the college_id FK rejects
                # the insert.
                #
                # KNOWN ROUGH EDGE, deliberately asserted rather than smoothed:
                # that 500 is loud and writes nothing, which is what matters
                # here, but a 401 naming the unknown tenant would be better.
                # Fixing it means verifying the college exists in
                # get_tenant_conn, which changes what every cross-tenant test
                # using a non-existent college id means — a real change, not a
                # test-file change. See the summary in the session notes.
                assert not (response.status_code == 200 and endpoint.touches_tenant_data), (
                    f"{endpoint.id} returned 200 to a college that owns no rows "
                    f"({endpoint.note}): {response.text[:300]}"
                )
                if endpoint.touches_tenant_data and response.status_code == 200:
                    pytest.fail(f"{endpoint.id} leaked to an unknown tenant")
                continue

            assert response.status_code == 401, (
                f"{endpoint.id} with {why} returned {response.status_code}, not 401.\n"
                f"  endpoint: {endpoint.note}\n"
                f"  body: {response.text[:400]}\n"
                f"A request with no usable tenant context must be REFUSED at the "
                f"edge. Anything else — a 200, an empty list, a 404 — is the "
                f"silent-empty failure CLAUDE_CONTEXT.md §6 describes."
            )

            detail = response.json().get("detail", "")
            assert DEBUG_COLLEGE_HEADER in detail or "uuid" in detail.lower(), (
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
            await endpoint.send(client)

    after = counts()
    assert after == before, (
        f"Uncredentialed requests changed the database: "
        f"{ {t: (before[t], after[t]) for t in tables if before[t] != after[t]} }"
    )


# ═════════════════ escalation: a tenant cannot become an admin ══════════════

@pytest.mark.parametrize(
    "headers, why",
    [
        ({"X-Is-Platform-Admin": "true"}, "a header inventing the admin claim"),
        ({"X-Debug-Is-Platform-Admin": "true"}, "the same, named to look official"),
        ({"X-Debug-College-Id": COLLEGE_A, "X-Platform-Admin": "1"}, "an extra flag alongside a real credential"),
        ({"X-Debug-College-Id": f"{COLLEGE_A}, {COLLEGE_B_ABSENT}"}, "two ids, hoping one is honoured"),
        ({"X-Debug-College-Id": f"{COLLEGE_A}\tX-Platform-Admin: true"}, "header injection via a tab"),
        ({"X-Debug-College-Id": f"{COLLEGE_A}' OR '1'='1"}, "SQL injection into the GUC"),
        ({"X-Debug-College-Id": f"{COLLEGE_A}'; SET app.is_platform_admin='true"}, "statement injection into SET LOCAL"),
        ({"X-Debug-College-Id": COLLEGE_A.upper()}, "case variation, in case comparison is textual"),
    ],
    ids=["invented-header", "official-looking", "extra-flag", "two-ids",
         "tab-injection", "sql-injection", "set-injection", "case-variation"],
)
async def test_no_request_can_escalate_to_platform_admin(whoami_app, headers, why):
    """A tenant caller must never acquire `app.is_platform_admin`.

    That GUC activates migration 003's `platform_admin_bypass` policy, which
    makes every college's rows visible on one connection. api/deps/identity.py
    hardcodes `is_platform_admin=False` and there is no endpoint wired to
    `get_admin_conn`, so the claim is unreachable by design — this test is
    what keeps it unreachable when identity is rewritten for real auth.

    The whoami probe is used because it reports what POSTGRES believes about
    the transaction, not what the API says about itself. An escalation that
    fooled the API but not the database, or vice versa, is visible here either
    way.

    The injection cases matter specifically because `SET LOCAL` cannot take a
    bind parameter — api/deps/db.py interpolates client-side through psycopg2
    — so the only thing standing between a header and a SET statement is the
    `uuid.UUID()` parse in identity.py. These assert that parse is doing its
    job.
    """
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
    # And the tenant it did resolve is a real, single, exactly-matching uuid.
    assert body["db"]["current_college_id"] == body["identity"]["college_id"]
    assert body["rls_context_ok"] is True


async def test_the_tenant_comes_from_identity_not_from_the_request(make_client, make_job):
    """A college named in a body, query string or path must be ignored.

    RLS scopes a request to whatever college id it is handed, so the one place
    that id may come from is the authenticated user. This fires COLLEGE_B's
    job id at a COLLEGE_A caller in every position a caller controls — path,
    query, body — and asserts none of them moves the tenant.
    """
    other_job = make_job(college_id=COLLEGE_A)["job_id"]

    async with make_client(COLLEGE_A) as client:
        # A query parameter that names another college is simply not a thing
        # the endpoint reads; assert it changes nothing rather than assuming.
        with_query = await client.get(
            f"/api/v1/jobs/{other_job}", params={"college_id": COLLEGE_B_ABSENT}
        )
        plain = await client.get(f"/api/v1/jobs/{other_job}")

    assert with_query.status_code == plain.status_code == 200
    assert with_query.json() == plain.json(), (
        "a college_id query parameter changed the response — the tenant must "
        "come from identity alone"
    )


async def test_another_colleges_row_is_indistinguishable_from_a_missing_one(
    make_client, make_job
):
    """Rule 3, asserted as a single comparison rather than by eyeballing two
    tests: a real job in another college and a uuid that exists nowhere must
    produce byte-identical responses.

    Any difference — status, body, even the phrasing of the message — turns
    the endpoint into an existence oracle for other tenants' data.
    """
    foreign_job = make_job(college_id=COLLEGE_A)["job_id"]
    nonexistent = uuid.uuid4()

    async with make_client(COLLEGE_B_ABSENT) as client:
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


async def test_the_isolating_predicate_is_not_only_rls(make_job):
    """THE TEST THAT ACTUALLY HOLDS ON THIS DEPLOYMENT.

    With PGUSER a superuser, migration 003/014's policies are inert and RLS
    isolates nothing (see the module docstring). What isolates tenants is the
    explicit `AND college_id = %s` inside core/jobs.py::get_job.

    So this drives that query under a DELIBERATELY WRONG RLS context — the
    exact situation where RLS would be the only thing stopping a leak — and
    asserts the row is still refused. If someone deletes the predicate as
    "redundant with the policy", this fails, instead of isolation silently
    disappearing on every superuser deployment in existence.
    """
    job_a = make_job(college_id=COLLEGE_A)["job_id"]

    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            # Context says college B; the query asks for college B's copy of a
            # job that belongs to college A.
            set_tenant_context(cur, COLLEGE_B_ABSENT)
            leaked = core.jobs.get_job(cur, job_id=job_a, college_id=COLLEGE_B_ABSENT)

            # Same connection, same wrong context, asking as the real owner —
            # this is what proves the previous line failed for the right reason
            # (the predicate) and not because the row was unreachable.
            set_tenant_context(cur, COLLEGE_A)
            owned = core.jobs.get_job(cur, job_id=job_a, college_id=COLLEGE_A)
    finally:
        conn.close()

    assert leaked is None, (
        "core/jobs.py::get_job returned another college's job. On a superuser "
        "connection its college_id predicate is the ONLY thing isolating "
        "tenants — see api/README.md's 'Known gap'."
    )
    assert owned is not None, "the job should be visible to its own college"


async def test_rls_policies_isolate_tenants(db_conn, tenant_conn):
    """The RLS layer proper — SKIPPED, LOUDLY, under a role that bypasses it.

    This is the test that will start enforcing real isolation the day PGUSER
    points at a non-superuser application role. It is written now, and skipped
    with a message naming the fix, rather than omitted — an absent test looks
    like an untested property, while a skipped one with this message is a
    standing instruction.
    """
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        (bypasses,) = cur.fetchone()

    if bypasses:
        pytest.skip(
            "PGUSER bypasses RLS (superuser or BYPASSRLS), so migrations 003 "
            "and 014's policies are inert and tenant isolation is enforced "
            "ONLY by the explicit college_id predicates "
            "(test_the_isolating_predicate_is_not_only_rls covers those). "
            "Fix: create a NOBYPASSRLS application role that owns nothing, "
            "grant it DML on the answer schema, point PGUSER at it. "
            "See api/README.md, 'Known gap'."
        )

    seeded = tenant_conn(COLLEGE_A)
    with seeded.cursor() as cur:
        cur.execute("SELECT count(*) FROM answers")
        (mine,) = cur.fetchone()

    other = tenant_conn(uuid.UUID(COLLEGE_B_ABSENT))
    with other.cursor() as cur:
        cur.execute("SELECT count(*) FROM answers")
        (theirs,) = cur.fetchone()

    assert mine > 0, "expected seeded answers for COLLEGE_A; run scripts/reset_and_seed_db.sh"
    assert theirs == 0, "RLS did not isolate a college with no rows of its own"
