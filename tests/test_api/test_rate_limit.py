"""tests/test_api/test_rate_limit.py — per-college API quotas
(api/deps/quota.py), Hardening pass 2026-09-06.

Nothing limited POST /evaluate, /questions/generate, /papers/generate or
/upload before this pass — core/llm.py's token bucket paces OUTBOUND Groq
calls one process makes, not how many callers ask it to make them. These
tests prove the missing half: a caller that exceeds its configured rate gets
429 + Retry-After, BEFORE the endpoint does its real work (a 404 for a
made-up resource id still counts as a call — the limiter is a route-level
dependency, resolved ahead of the handler body, on purpose: see
api/deps/quota.py::rate_limit), and one college's burst never affects
another's — the isolation property that makes "leaking another tenant's
existence via its quota" impossible, since the counters never touch.

A dedicated, TINY-limit app is built per test (mirroring the existing
`prod_settings` pattern in test_evaluation.py / test_questions.py) rather
than relying on `api_settings`'s production-sized defaults — driving those
to their real limit would mean dozens of requests per test for no extra
coverage, since api/deps/ratelimit.py::SlidingWindowLimiter is the same
class the login endpoint uses, already proven at the mechanism level by
test_auth.py's login rate-limit tests.
"""
from __future__ import annotations

import uuid

import httpx
import pytest

from api.main import create_app
from api.settings import Settings
from tests.test_api.conftest import COLLEGE_A, COLLEGE_B

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


def _tiny_limits(**overrides) -> Settings:
    """A tiny limit (2 per 5 minutes) on every quota'd endpoint, so a test
    trips it in 3 calls instead of dozens. The window is long enough that a
    slow CI run cannot let the window itself expire mid-test and mask the
    trip as "it recovered", the same reasoning test_auth.py's login-limiter
    tests use for their own window."""
    return Settings(
        enable_debug_endpoints=True, storage_mode="dummy",
        evaluate_rate_limit_attempts=2, evaluate_rate_limit_window_seconds=300,
        questions_generate_rate_limit_attempts=2,
        questions_generate_rate_limit_window_seconds=300,
        papers_generate_rate_limit_attempts=2, papers_generate_rate_limit_window_seconds=300,
        upload_rate_limit_attempts=2, upload_rate_limit_window_seconds=300,
        **overrides,
    )


@pytest.fixture
def tiny_limit_client(sign_token):
    """Factory: tiny_limit_client(college_id) -> an authenticated client on a
    fresh app whose four quota'd endpoints all trip at 2 calls."""
    settings = _tiny_limits()
    app = create_app(settings)

    def make(college_id=COLLEGE_A, *, role: str = "teacher") -> httpx.AsyncClient:
        token = sign_token(settings, college_id, role=role)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"},
        )

    return make


def _evaluate_body() -> dict:
    """A well-formed but nonexistent booklet — 404s inside the handler, but
    only AFTER the rate-limit dependency has already counted the call. Using
    ids nothing resolves to means these tests write nothing to the database
    and need no cleanup fixture."""
    return {
        "upload_id": str(uuid.uuid4()), "exam_id": str(uuid.uuid4()),
        "student_id": str(uuid.uuid4()), "paper_id": str(uuid.uuid4()),
    }


# ═══════════════════════════ the mechanism, on /evaluate ════════════════════

async def test_evaluate_trips_the_rate_limit_and_returns_retry_after(tiny_limit_client):
    async with tiny_limit_client(COLLEGE_A) as client:
        for _ in range(2):
            response = await client.post("/api/v1/evaluate", json=_evaluate_body())
            # Not rate-limited yet — refused for the ordinary reason (no such
            # upload), which is the point: the limit counts the CALL, not
            # whether it would have succeeded.
            assert response.status_code == 404, response.text

        limited = await client.post("/api/v1/evaluate", json=_evaluate_body())

    assert limited.status_code == 429, limited.text
    retry_after = int(limited.headers["Retry-After"])
    assert 0 < retry_after <= 300
    assert "rate limit" in limited.json()["detail"].lower()


async def test_evaluate_rate_limit_is_scoped_per_college_not_shared(
    tiny_limit_client, college_b,
):
    """College A's burst must not touch college B's quota for the SAME
    endpoint. Keyed by college (api/deps/quota.py::_quota_key), never by IP
    and never shared — the property that makes the limiter unable to leak
    one tenant's activity to another: a caller cannot learn "college X is
    busy right now" by watching its own quota move, because the two never
    interact.
    """
    async with tiny_limit_client(COLLEGE_A) as client_a:
        for _ in range(3):
            await client_a.post("/api/v1/evaluate", json=_evaluate_body())
        confirmed_limited = await client_a.post("/api/v1/evaluate", json=_evaluate_body())
    assert confirmed_limited.status_code == 429, confirmed_limited.text

    async with tiny_limit_client(COLLEGE_B) as client_b:
        unaffected = await client_b.post("/api/v1/evaluate", json=_evaluate_body())

    assert unaffected.status_code == 404, (
        "college B's first call must be refused for the ordinary reason (no "
        "such upload) — 429 here would mean the limiter is shared across "
        "colleges."
    )


async def test_a_rate_limited_response_carries_no_information_about_other_colleges(
    tiny_limit_client,
):
    """The 429 body is the same generic message regardless of who is calling
    or what they were trying to do — it must not become a side channel that
    confirms or denies anything about another tenant."""
    async with tiny_limit_client(COLLEGE_A) as client:
        for _ in range(2):
            await client.post("/api/v1/evaluate", json=_evaluate_body())
        limited = await client.post("/api/v1/evaluate", json=_evaluate_body())

    detail = limited.json()["detail"]
    assert COLLEGE_A not in detail
    assert COLLEGE_B not in detail
    assert "college" not in detail.lower()


# ══════════════════ the same dependency, wired to the other three ═══════════

async def test_questions_generate_rate_limit_trips(tiny_limit_client):
    async with tiny_limit_client(COLLEGE_A) as client:
        for _ in range(2):
            response = await client.post(
                "/api/v1/questions/generate",
                json={"paragraph_id": str(uuid.uuid4()), "count": 1, "stub_llm": True},
            )
            assert response.status_code == 404, response.text

        limited = await client.post(
            "/api/v1/questions/generate",
            json={"paragraph_id": str(uuid.uuid4()), "count": 1, "stub_llm": True},
        )

    assert limited.status_code == 429, limited.text
    assert int(limited.headers["Retry-After"]) > 0


async def test_papers_generate_rate_limit_trips(tiny_limit_client):
    async with tiny_limit_client(COLLEGE_A) as client:
        for _ in range(2):
            response = await client.post("/api/v1/papers/generate", json={
                "pattern_id": str(uuid.uuid4()), "name": "Rate limit probe",
            })
            assert response.status_code == 404, response.text

        limited = await client.post("/api/v1/papers/generate", json={
            "pattern_id": str(uuid.uuid4()), "name": "Rate limit probe",
        })

    assert limited.status_code == 429, limited.text
    assert int(limited.headers["Retry-After"]) > 0


async def test_upload_rate_limit_trips(tiny_limit_client):
    """A non-PDF body 415s INSIDE the handler — after the rate-limit
    dependency has already counted the call, and before anything is stored,
    so this needs no `track_uploads` cleanup either."""
    not_a_pdf = {"file": ("not-a-booklet.pdf", b"definitely not a pdf", "application/pdf")}

    async with tiny_limit_client(COLLEGE_A) as client:
        for _ in range(2):
            response = await client.post("/api/v1/upload", files=not_a_pdf)
            assert response.status_code == 415, response.text

        limited = await client.post("/api/v1/upload", files=not_a_pdf)

    assert limited.status_code == 429, limited.text
    assert int(limited.headers["Retry-After"]) > 0


# ═══════════════════════ the OTHER limit: concurrent backlog ════════════════

async def test_evaluate_refuses_once_the_concurrent_job_cap_is_reached(
    sign_token, make_booklet, track_jobs, admin_conn,
):
    """MAX_QUEUED_JOBS_PER_COLLEGE — not the per-minute rate above — is what
    actually protects the shared Groq daily quota (api/deps/quota.py's module
    docstring): a college with `cap` jobs already queued/running is refused a
    NEW one regardless of how slowly it queued them, i.e. regardless of
    whether it would ever trip the rate limiter at all.
    """
    from api.deps.db import set_admin_context

    settings = Settings(
        enable_debug_endpoints=True, storage_mode="dummy",
        max_queued_jobs_per_college=1,
        # A generous rate limit — this test is about the JOB cap, not the
        # per-minute one, so the rate limiter must not be what trips first.
        evaluate_rate_limit_attempts=50, evaluate_rate_limit_window_seconds=60,
    )
    app = create_app(settings)
    token = sign_token(settings, COLLEGE_A)

    booklet = make_booklet()
    # A pre-existing queued job for this college, planted directly (not
    # through the API) so the cap is already at its configured limit (1)
    # before this test's own request.
    import core.jobs
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        existing = core.jobs.enqueue_job(
            cur, college_id=COLLEGE_A, job_type="booklet_eval",
            payload={"probe": "pre-existing backlog"},
        )
    admin_conn.commit()
    track_jobs(existing["job_id"])

    def _job_count() -> int:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute(
                "SELECT COUNT(*) FROM evaluation_jobs WHERE college_id = %s",
                (COLLEGE_A,),
            )
            (count,) = cur.fetchone()
        return count

    before = _job_count()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        response = await client.post("/api/v1/evaluate", json={
            "upload_id": booklet["upload_id"], "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"], "paper_id": booklet["paper_id"],
        })

    assert response.status_code == 429, response.text
    assert "queued or running" in response.json()["detail"]
    assert int(response.headers["Retry-After"]) > 0

    # Refusing the request must not have queued anything anyway — the cap
    # check runs BEFORE enqueue_evaluation_pipeline, in the same transaction.
    assert _job_count() == before, "a refused request must not queue a job"
