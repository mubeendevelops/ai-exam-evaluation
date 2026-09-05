"""tests/test_api/test_evaluation.py — evaluate, results, override.

The three things worth proving here, in descending order of how much damage
getting them wrong would do:

  1. THE APPEND-ONLY LEDGER. An override must never touch evaluation_results.
     Asserted by snapshotting every row for the answer — contents, not just a
     count — before and after, so an in-place UPDATE of a score fails the test
     rather than passing a row-count check.
  2. TENANT ISOLATION. Another college's answer is 404 from /results, and
     another college's upload is 404 from /evaluate.
  3. THE LOOP ACTUALLY RUNS. upload -> evaluate -> poll -> results, through
     the API, through the real worker, through core/booklet_evaluator.py,
     with no network call to Groq.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest

import core.db
import core.jobs
from api.deps.db import set_admin_context
from tests.test_api.conftest import (
    COLLEGE_A,
    COLLEGE_B_ABSENT,
    MINIMAL_PDF,
    REVIEWER_TEACHER,
)

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

EVALUATE = "/api/v1/evaluate"


def _load_worker():
    """The real scripts/run_job_worker.py, loaded by path (scripts/ is a
    directory of CLI entry points, not a package). Importing the real worker
    rather than reimplementing its loop is the point — a test against a copy
    of the worker proves nothing about the worker."""
    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "run_job_worker.py"
    spec = importlib.util.spec_from_file_location("run_job_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _admin_conn():
    conn = core.db.get_connection()
    with conn.cursor() as cur:
        set_admin_context(cur)
    return conn


# ══════════════════════════════ POST /evaluate ══════════════════════════════

async def test_evaluate_enqueues_a_job_whose_payload_round_trips(
    make_client, make_booklet, track_jobs
):
    """202, a queued booklet_eval job, and a payload the worker can act on.

    The payload assertions matter more than they look. `source_scan_url` is
    NOT something the client sent — the endpoint resolved it from upload_id,
    tenant-scoped, so the worker never has to repeat that lookup in a context
    where a wrong tenant would have nobody left to catch it. If that resolution
    were dropped, this test is what notices.
    """
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "stub": True,
        })

    assert response.status_code == 202, response.text
    body = response.json()
    track_jobs(body["job_id"])

    assert body["status"] == "queued"
    assert body["upload_id"] == booklet["upload_id"]
    assert body["exam_id"] == booklet["exam_id"]
    assert body["student_id"] == booklet["student_id"]

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            job = core.jobs.get_job(cur, job_id=body["job_id"], college_id=COLLEGE_A)
    finally:
        conn.close()

    assert job["job_type"] == "booklet_eval"
    assert job["status"] == "queued"
    payload = job["payload"]
    assert payload["upload_id"] == booklet["upload_id"]
    assert payload["exam_id"] == booklet["exam_id"]
    assert payload["student_id"] == booklet["student_id"]
    # Resolved server-side from the upload, not supplied by the client.
    assert payload["source_scan_url"] == booklet["blob_url"]
    assert payload["options"]["stub"] is True


async def test_evaluate_for_another_colleges_upload_is_404(make_client, make_booklet):
    """An upload_id belonging to college A must be 404 for college B — the
    same 404 as an id that exists nowhere, so the endpoint cannot be used to
    discover which upload ids are real elsewhere on the platform.

    Also the only integrity check on this id: migration 015 deliberately puts
    no FK from evaluation_jobs.payload to booklet_uploads, so without the
    tenant-scoped resolution in the service a client could queue a job against
    any uuid at all.
    """
    booklet = make_booklet(college_id=COLLEGE_A)

    async with make_client(COLLEGE_B_ABSENT) as stranger:
        theirs = await stranger.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
        })
        made_up = await stranger.post(EVALUATE, json={
            "upload_id": str(uuid.uuid4()),
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
        })

    assert theirs.status_code == 404, theirs.text
    assert made_up.status_code == 404
    # Nothing about the real upload leaked into the 404 body.
    assert booklet["blob_url"] not in theirs.text


async def test_evaluate_rejects_stub_when_debug_is_off(make_booklet, _dotenv):
    """A stub score is FABRICATED and is appended to the append-only ledger
    exactly like a real one, so a production client must not be able to ask
    for one. 403 outside development.
    """
    import httpx

    from api.deps.identity import DEBUG_COLLEGE_HEADER
    from api.main import create_app
    from api.settings import Settings

    booklet = make_booklet()
    prod_app = create_app(Settings(api_env="production", enable_debug_endpoints=False,
                                   storage_mode="dummy"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=prod_app),
        base_url="http://testserver",
        headers={DEBUG_COLLEGE_HEADER: COLLEGE_A},
    ) as ac:
        response = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "stub": True,
        })

    assert response.status_code == 403, response.text
    assert "fabricated" in response.json()["detail"].lower()


async def test_evaluate_requires_credentials(make_client, make_booklet):
    booklet = make_booklet()
    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(EVALUATE, headers={"X-Debug-College-Id": ""}, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
        })
    assert response.status_code == 401, response.text


# ═══════════════════════════ GET /results/{id} ══════════════════════════════

async def test_results_for_another_colleges_answer_is_404(make_client, make_booklet):
    """THE CROSS-TENANT PROOF for this endpoint.

    College B asks for college A's answer by its exact id and gets 404 —
    indistinguishable from a nonexistent id, and carrying none of the answer's
    content. 403 would say "this exists, you may not have it", which is an
    existence oracle over other colleges' answer ids.
    """
    booklet = make_booklet(college_id=COLLEGE_A)
    answer_id = booklet["answer_id"]

    async with make_client(COLLEGE_A) as owner:
        mine = await owner.get(f"/api/v1/results/{answer_id}")
    async with make_client(COLLEGE_B_ABSENT) as stranger:
        theirs = await stranger.get(f"/api/v1/results/{answer_id}")
        made_up = await stranger.get(f"/api/v1/results/{uuid.uuid4()}")

    assert mine.status_code == 200, mine.text
    assert mine.json()["answer_id"] == answer_id

    assert theirs.status_code == 404, theirs.text
    assert made_up.status_code == 404
    # Not one field of the answer came back.
    assert booklet["question_id"] not in theirs.text
    assert booklet["blob_url"] not in theirs.text
    assert "stack" not in theirs.text.lower()


async def test_results_for_an_unscored_answer_is_null_not_zero(make_client, make_booklet):
    """An answer that has not been evaluated reports evaluation=null.

    Not a score of 0.0. "Not scored" and "scored zero" are different facts
    about a student's work, and §7D keeps them apart everywhere else in this
    pipeline; the API must not collapse them at the last step.
    """
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        response = await ac.get(f"/api/v1/results/{booklet['answer_id']}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evaluation"] is None
    assert body["history"] == []
    assert body["final_marks"]["marks"] is None
    assert body["final_marks"]["source"] is None
    assert body["status"] == "pending_evaluation"
    assert body["marks_max"] == booklet["marks_max"]


async def test_malformed_answer_id_is_422(make_client):
    """Rejected by FastAPI before any SQL runs — a non-uuid reaching the query
    would raise inside Postgres's ::uuid cast and surface as a 500."""
    async with make_client(COLLEGE_A) as ac:
        response = await ac.get("/api/v1/results/not-a-uuid")
    assert response.status_code == 422, response.text


# ════════════════════════ POST /results/{id}/override ═══════════════════════

async def test_override_writes_answer_reviews_and_never_touches_the_ledger(
    make_client, make_booklet, admin_conn, ledger_snapshot, track_jobs
):
    """THE APPEND-ONLY GUARANTEE, proven rather than asserted in a comment.

    The answer is scored first, so there IS a ledger row to be corrupted —
    an override against an unscored answer would pass this test trivially.
    Then every evaluation_results row for the answer is snapshotted WITH ITS
    CONTENTS (score, explanation, is_current, evaluated_at, metrics) before and
    after the override.

    A row count alone would not be enough: the violation this guards against
    is an in-place UPDATE of the score, which leaves the count identical. The
    full-row comparison is what makes the guarantee real.
    """
    worker = _load_worker()
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        queued = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "stub": True,
        })
        assert queued.status_code == 202, queued.text
        track_jobs(queued.json()["job_id"])

        assert worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                                  job_types=["booklet_eval"]) is not None

        before = ledger_snapshot(booklet["answer_id"])
        assert len(before) == 1, (
            "expected the stub run to have appended exactly one ledger row — "
            "one row per QUESTION is decision 1 in core/booklet_evaluator.py"
        )

        override = await ac.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={
                "reviewer_id": "44444444-4444-4444-4444-444444444444",  # seeded Dr. Rao
                "action": "overridden",
                "final_marks": 9.0,
                "comment": "Student's phrasing differs but the content is correct.",
            },
        )

        after = ledger_snapshot(booklet["answer_id"])
        result = await ac.get(f"/api/v1/results/{booklet['answer_id']}")

    assert override.status_code == 201, override.text
    body = override.json()
    assert body["action"] == "overridden"
    assert body["final_marks"] == 9.0
    assert body["evaluation_results_untouched"] is True
    assert body["status"] == "sme_reviewed"
    assert body["status_changed"] is True

    # ── THE ASSERTION THIS TEST EXISTS FOR ──────────────────────────────────
    assert after == before, (
        "an override MUST NOT touch evaluation_results. That ledger is "
        "append-only (PROJECT_CONTEXT.md rule 2) and records what the AI "
        "computed; a teacher's disagreement is a new fact in answer_reviews, "
        "not a correction to the model's output."
    )

    # The review really landed, in answer_reviews.
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            "SELECT reviewer_id, action, final_marks, comment, college_id "
            "  FROM answer_reviews WHERE answer_id = %s",
            (booklet["answer_id"],),
        )
        rows = cur.fetchall()

    assert len(rows) == 1
    reviewer_id, action, final_marks, comment, college_id = rows[0]
    assert action == "overridden"
    assert final_marks == 9.0
    assert comment.startswith("Student's phrasing")
    # college_id is derived by migration 003's trigger, never sent by the API.
    assert str(college_id) == COLLEGE_A

    # And the read endpoint reconciles the two: the override wins, but the AI's
    # score is still visible beside it rather than being overwritten.
    report = result.json()
    assert report["final_marks"]["marks"] == 9.0
    assert report["final_marks"]["source"] == "sme_override"
    assert report["final_marks"]["ai_score"] == pytest.approx(before[0][1], rel=1e-6)
    assert len(report["reviews"]) == 1
    assert report["status"] == "sme_reviewed"


async def test_override_on_an_unscored_answer_still_records_the_review(
    make_client, make_booklet, ledger_snapshot
):
    """A teacher may override an answer the AI never scored.

    Refusing this would make an unscorable answer (the AI failed, or the region
    was flagged) permanently unreviewable — the opposite of what the review
    queue exists for. The ledger stays empty throughout: no row is invented to
    hang the override off.
    """
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={"reviewer_id": "44444444-4444-4444-4444-444444444444",
                  "action": "overridden", "final_marks": 4.0},
        )
        report = await ac.get(f"/api/v1/results/{booklet['answer_id']}")

    assert response.status_code == 201, response.text
    assert ledger_snapshot(booklet["answer_id"]) == [], (
        "an override must not create an evaluation_results row — every row "
        "there carries a reference FK and plugin attribution a human review "
        "does not have"
    )
    body = report.json()
    assert body["evaluation"] is None
    assert body["final_marks"]["marks"] == 4.0
    assert body["final_marks"]["source"] == "sme_override"
    assert body["final_marks"]["ai_score"] is None


async def test_second_override_appends_rather_than_replacing(
    make_client, make_booklet, admin_conn
):
    """Two teachers reviewing the same answer produce two review rows.

    answer_reviews is the log of who said what; the newest override with an
    explicit mark is the one that stands, and the earlier one stays readable.
    The status does not transition twice ('sme_reviewed' is re-entrant), which
    is what status_changed=False reports.
    """
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        first = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                              json={"reviewer_id": "44444444-4444-4444-4444-444444444444",
                                    "action": "overridden", "final_marks": 5.0})
        second = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                               json={"reviewer_id": "55555555-5555-5555-5555-555555555555",
                                     "action": "overridden", "final_marks": 7.5})
        report = await ac.get(f"/api/v1/results/{booklet['answer_id']}")

    assert first.status_code == second.status_code == 201
    assert first.json()["status_changed"] is True
    assert second.json()["status_changed"] is False, (
        "'sme_reviewed' is re-entrant — a second review writes a row without "
        "inventing a transition"
    )

    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("SELECT count(*) FROM answer_reviews WHERE answer_id = %s",
                    (booklet["answer_id"],))
        assert cur.fetchone()[0] == 2

    body = report.json()
    assert len(body["reviews"]) == 2
    assert body["final_marks"]["marks"] == 7.5, "the newest override stands"


async def test_flagged_override_carries_no_marks(make_client, make_booklet):
    """'flagged' escalates without deciding a mark, so final_marks must be
    absent — and a 'flagged' review must not displace the AI score."""
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        bad = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                            json={"reviewer_id": "44444444-4444-4444-4444-444444444444",
                                  "action": "flagged", "final_marks": 3.0})
        missing_marks = await ac.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={"reviewer_id": "44444444-4444-4444-4444-444444444444",
                  "action": "overridden"})
        good = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                             json={"reviewer_id": "44444444-4444-4444-4444-444444444444",
                                   "action": "flagged", "comment": "Illegible."})

    assert bad.status_code == 422, bad.text
    assert missing_marks.status_code == 422, missing_marks.text
    assert good.status_code == 201, good.text
    assert good.json()["final_marks"] is None


async def test_override_on_another_colleges_answer_is_404(make_client, make_booklet,
                                                          admin_conn):
    """And writes nothing — an override that 404s must not leave a review row
    behind under either college."""
    booklet = make_booklet(college_id=COLLEGE_A)

    async with make_client(COLLEGE_B_ABSENT) as stranger:
        response = await stranger.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={"reviewer_id": "44444444-4444-4444-4444-444444444444",
                  "action": "overridden", "final_marks": 10.0},
        )

    assert response.status_code == 404, response.text
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("SELECT count(*) FROM answer_reviews WHERE answer_id = %s",
                    (booklet["answer_id"],))
        assert cur.fetchone()[0] == 0


# ═════════════════════ end to end, entirely through the API ═════════════════

async def test_full_loop_upload_evaluate_poll_results_override_in_stub_mode(
    make_client, make_booklet, track_uploads, track_jobs, ledger_snapshot, monkeypatch
):
    """THE DAY'S SUCCESS CRITERION.

    upload -> evaluate -> poll the job -> fetch the result -> override ->
    re-read, every step over HTTP, with the real worker and the real
    core/booklet_evaluator.

    The override leg was missing until the Day 5 hardening pass: this
    docstring claimed it, and the test stopped at the result. It is here now,
    and it closes the loop on the property the whole design turns on — a human
    can change the mark a student receives without altering the record of what
    the model actually said.

    ZERO NETWORK CALLS TO GROQ, and that is asserted rather than assumed:
    core.llm._generate is monkeypatched to blow up, so if stub mode ever
    stopped short-circuiting the LLM path this test fails loudly instead of
    quietly making a paid API call from the test suite.

    The upload posted here and the booklet fixture are separate on purpose —
    the API cannot ingest a PDF into answer_blocks rows yet (that is
    scripts/ingest_booklet.py's job, §7C), so the loop uploads a real file AND
    evaluates a booklet whose regions already exist. That gap is the honest
    state of the wiring today, and this test documents it rather than hiding it
    behind a fixture that pretends ingestion happened.
    """
    def _explode(*args, **kwargs):
        raise AssertionError(
            "core.llm._generate was called during a stub-mode run — stub mode "
            "must not reach Groq."
        )

    monkeypatch.setattr("core.llm._generate", _explode)

    worker = _load_worker()
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        # 1. upload a real PDF
        uploaded = await ac.post("/api/v1/upload", files={
            "file": ("booklet.pdf", MINIMAL_PDF, "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        track_uploads(uploaded.json()["upload_id"])

        # 2. queue an evaluation for the ingested booklet
        queued = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "stub": True,
        })
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]
        track_jobs(job_id)

        # 3. the job is queued and reports itself as such
        polled = await ac.get(f"/api/v1/jobs/{job_id}")
        assert polled.json()["status"] == "queued"
        assert polled.json()["progress"]["stage"] == "queued"

        # 4. run the real worker
        processed = worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                                       job_types=["booklet_eval"])
        assert processed is not None and processed["job_id"] == job_id

        done = await ac.get(f"/api/v1/jobs/{job_id}")

        # 5. fetch the per-answer result
        result = await ac.get(f"/api/v1/results/{booklet['answer_id']}")

        # 6. a teacher disagrees and overrides — the last leg of the loop
        ledger_before = ledger_snapshot(booklet["answer_id"])
        override = await ac.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={
                "reviewer_id": REVIEWER_TEACHER,
                "action": "overridden",
                "final_marks": 7.5,
                "comment": "Correct method, arithmetic slip in the last step.",
            },
        )
        ledger_after = ledger_snapshot(booklet["answer_id"])

        # 7. and the report now reflects the human decision
        reconciled = await ac.get(f"/api/v1/results/{booklet['answer_id']}")

    job = done.json()
    assert job["status"] == "succeeded", job
    assert job["progress"]["stage"] == "done"
    assert job["result"]["stub"] is True
    assert job["result"]["counts"]["questions"] >= 1
    assert job["result"]["persistence"]["written"] >= 1

    report = result.json()
    assert report["answer_id"] == booklet["answer_id"]
    assert report["evaluation"] is not None, "the worker should have appended a ledger row"
    assert report["evaluation"]["score"] is not None
    assert report["evaluation"]["plugin"], "the ledger row must name what produced it"
    # Aggregated from the regions — one ledger row per QUESTION, with every
    # region's own result preserved underneath it.
    assert len(report["components"]) >= 1
    assert report["confidence"]["question"] is not None
    assert len(report["history"]) == 1
    assert report["final_marks"]["source"] == "ai"
    # Scoring moved the answer out of pending_evaluation.
    assert report["status"] == "ai_scored"

    # ── the override leg ────────────────────────────────────────────────────
    assert override.status_code == 201, override.text
    assert override.json()["final_marks"] == 7.5
    assert override.json()["evaluation_results_untouched"] is True

    # The loop's whole point: a human decision changed the mark WITHOUT
    # touching what the AI recorded. Asserted here on the full row contents,
    # not a count, for the same reason
    # test_override_writes_answer_reviews_and_never_touches_the_ledger does —
    # an in-place UPDATE leaves the count identical.
    assert ledger_after == ledger_before, (
        "the override mutated the append-only ledger during the end-to-end loop"
    )

    final = reconciled.json()
    assert final["final_marks"]["marks"] == 7.5
    assert final["final_marks"]["source"] == "sme_override"
    # The AI's number survives beside the human's, which is what makes the
    # ledger worth keeping — you can still tell what the model said.
    assert final["final_marks"]["ai_score"] == pytest.approx(
        report["evaluation"]["score"], rel=1e-6)
    assert final["status"] == "sme_reviewed"
    assert len(final["reviews"]) == 1
    # Still exactly one ledger row: an override is not a re-score.
    assert len(final["history"]) == 1


async def test_evaluating_a_booklet_with_no_regions_fails_loudly(
    make_client, make_booklet, track_jobs
):
    """A booklet nobody has ingested must FAIL, not succeed with an empty
    report.

    An empty report would aggregate to a total of zero and read, to a teacher,
    exactly like a student who wrote nothing. The failure names the missing
    step (scripts/ingest_booklet.py) instead.
    """
    worker = _load_worker()
    booklet = make_booklet()

    # Delete the regions, leaving the answer with nothing to score.
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM answer_blocks WHERE answer_id = %s",
                        (booklet["answer_id"],))
        conn.commit()
    finally:
        conn.close()

    async with make_client(COLLEGE_A) as ac:
        queued = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "stub": True,
        })
        job_id = queued.json()["job_id"]
        track_jobs(job_id)

        worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                           job_types=["booklet_eval"])
        done = await ac.get(f"/api/v1/jobs/{job_id}")

    body = done.json()
    assert body["status"] == "failed", body
    assert "ingest" in body["error"].lower()
    assert body["result"] is None
