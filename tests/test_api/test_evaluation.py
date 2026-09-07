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
import json
import pathlib
import uuid

import pytest

import api.services.evaluation as evaluation_service
import core.db
import core.jobs
import core.storage
from api.deps.db import set_admin_context
from tests.test_api.conftest import (
    COLLEGE_A,
    COLLEGE_B,
    COLLEGE_B_OWNER,
    MINIMAL_PDF,
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
            "paper_id": booklet["paper_id"],
            "stub": True,
        })

    assert response.status_code == 202, response.text
    body = response.json()
    track_jobs(body["job_id"])

    assert body["status"] == "queued"
    assert body["upload_id"] == booklet["upload_id"]
    assert body["exam_id"] == booklet["exam_id"]
    assert body["student_id"] == booklet["student_id"]
    assert body["paper_id"] == booklet["paper_id"]
    # This booklet's regions already exist, so no ingestion was queued and
    # the job the client polls IS the evaluation.
    assert body["job_type"] == "booklet_eval"
    assert body["ingest_job_id"] is None

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


async def test_evaluate_for_another_colleges_upload_is_404(make_client, make_booklet,
                                                          college_b, track_jobs):
    """An upload_id belonging to college A must be 404 for college B — the
    same 404 as an id that exists nowhere, so the endpoint cannot be used to
    discover which upload ids are real elsewhere on the platform.

    Also the only integrity check on this id: migration 015 deliberately puts
    no FK from evaluation_jobs.payload to booklet_uploads, so without the
    tenant-scoped resolution in the service a client could queue a job against
    any uuid at all.

    College B is a REAL college here (it used to be an id that existed
    nowhere), and the last leg makes that matter: B successfully queues a job
    against ITS OWN upload with the same credential. Without it, all three
    404s would be equally explained by "B is refused everywhere", which is
    what an absent college id would now actually produce — a 401.
    """
    booklet = make_booklet(college_id=COLLEGE_A)
    b_booklet = make_booklet(college_id=college_b["college_id"],
                             student_id=college_b["student_id"],
                             exam_id=college_b["exam_id"])

    async with make_client(COLLEGE_B) as stranger:
        theirs = await stranger.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
        })
        made_up = await stranger.post(EVALUATE, json={
            "upload_id": str(uuid.uuid4()),
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
        })

    assert theirs.status_code == 404, theirs.text
    assert made_up.status_code == 404
    # Nothing about the real upload leaked into the 404 body.
    assert booklet["blob_url"] not in theirs.text

    # The same credential, on B's own upload, works — so the 404 above is
    # isolation and not rejection.
    async with make_client(COLLEGE_B) as owner:
        own = await owner.post(EVALUATE, json={
            "upload_id": b_booklet["upload_id"],
            "exam_id": b_booklet["exam_id"],
            "student_id": b_booklet["student_id"],
            "paper_id": b_booklet["paper_id"],
            "stub": True,
        })
    assert own.status_code == 202, own.text
    track_jobs(own.json()["job_id"])


async def test_evaluate_rejects_stub_when_debug_is_off(make_booklet, sign_token,
                                                      _dotenv):
    """A stub score is FABRICATED and is appended to the append-only ledger
    exactly like a real one, so a production client must not be able to ask
    for one. 403 outside development.
    """
    import httpx

    from api.main import create_app
    from api.settings import Settings

    booklet = make_booklet()
    # A production app needs a real signing key — create_app refuses to boot
    # without one outside development, which is itself the behaviour under
    # test in test_auth.py.
    prod_settings = Settings(api_env="production", enable_debug_endpoints=False,
                             storage_mode="dummy",
                             jwt_secret="a signing key for this test's app",
                             # CORS_ORIGINS has no permissive default outside
                             # development (Hardening pass 2026-09-06) — a
                             # production app refuses to boot without one.
                             cors_origins="https://example.test")
    prod_app = create_app(prod_settings)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=prod_app),
        base_url="http://testserver",
        headers={"Authorization": f"Bearer {sign_token(prod_settings)}"},
    ) as ac:
        response = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
            "stub": True,
        })

    assert response.status_code == 403, response.text
    assert "fabricated" in response.json()["detail"].lower()


async def test_evaluate_requires_credentials(make_client, make_booklet):
    booklet = make_booklet()
    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(EVALUATE, headers={"Authorization": ""}, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
        })
    assert response.status_code == 401, response.text


# ═══════════════════════════ GET /results/{id} ══════════════════════════════

async def test_results_for_another_colleges_answer_is_404(make_client, make_booklet,
                                                         college_b):
    """THE CROSS-TENANT PROOF for this endpoint.

    College B asks for college A's answer by its exact id and gets 404 —
    indistinguishable from a nonexistent id, and carrying none of the answer's
    content. 403 would say "this exists, you may not have it", which is an
    existence oracle over other colleges' answer ids.
    """
    booklet = make_booklet(college_id=COLLEGE_A)
    answer_id = booklet["answer_id"]
    b_booklet = make_booklet(college_id=college_b["college_id"],
                             student_id=college_b["student_id"],
                             exam_id=college_b["exam_id"])

    async with make_client(COLLEGE_A) as owner:
        mine = await owner.get(f"/api/v1/results/{answer_id}")
    async with make_client(COLLEGE_B) as stranger:
        theirs = await stranger.get(f"/api/v1/results/{answer_id}")
        made_up = await stranger.get(f"/api/v1/results/{uuid.uuid4()}")
        # B's own answer, same credential: 200. This is what makes the 404
        # above isolation rather than a rejected caller — college B is a real,
        # active tenant with rows of its own.
        own = await stranger.get(f"/api/v1/results/{b_booklet['answer_id']}")

    assert mine.status_code == 200, mine.text
    assert mine.json()["answer_id"] == answer_id
    assert own.status_code == 200, own.text
    assert own.json()["answer_id"] == b_booklet["answer_id"]

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
    assert body["history"]["items"] == []
    assert body["history"]["total"] == 0
    assert body["history"]["truncated"] is False
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


# ═══════════════════════════════ GET /results (list) ════════════════════════

async def test_results_list_is_isolated_by_tenant(make_client, make_booklet, college_b):
    a = make_booklet(college_id=COLLEGE_A)
    b = make_booklet(college_id=college_b["college_id"],
                     student_id=college_b["student_id"],
                     exam_id=college_b["exam_id"])

    async with make_client(COLLEGE_A) as client:
        mine = await client.get("/api/v1/results")
    async with make_client(COLLEGE_B) as client:
        theirs = await client.get("/api/v1/results")

    assert mine.status_code == theirs.status_code == 200
    mine_ids = {r["answer_id"] for r in mine.json()["items"]}
    theirs_ids = {r["answer_id"] for r in theirs.json()["items"]}

    assert a["answer_id"] in mine_ids
    assert a["answer_id"] not in theirs_ids
    assert b["answer_id"] in theirs_ids
    assert b["answer_id"] not in mine_ids


async def test_results_list_limit_above_max_is_clamped_not_rejected(make_client, make_booklet):
    """A limit far above MAX_LIMIT (200) is CLAMPED, not answered with 422 —
    see api/deps/pagination.py."""
    make_booklet()

    async with make_client(COLLEGE_A) as client:
        response = await client.get("/api/v1/results", params={"limit": 100_000})

    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 200


async def test_results_list_filters_by_exam_student_and_status(make_client, make_booklet):
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as client:
        matching = await client.get("/api/v1/results", params={
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "status": "pending_evaluation",
        })
        mismatched_status = await client.get("/api/v1/results",
                                             params={"status": "finalized"})

    assert matching.status_code == 200, matching.text
    ids = {r["answer_id"] for r in matching.json()["items"]}
    assert booklet["answer_id"] in ids

    other_ids = {r["answer_id"] for r in mismatched_status.json()["items"]}
    assert booklet["answer_id"] not in other_ids


async def test_results_list_filters_by_needs_review(make_client, make_booklet, admin_conn):
    """needs_review is rolled up from answer_blocks, not a column on answers
    itself — see core/results.py."""
    booklet = make_booklet()
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("UPDATE answer_blocks SET needs_review = true WHERE block_id = %s",
                    (booklet["block_id"],))
    admin_conn.commit()

    async with make_client(COLLEGE_A) as client:
        flagged = await client.get("/api/v1/results", params={"needs_review": "true"})
        clean = await client.get("/api/v1/results", params={"needs_review": "false"})

    flagged_ids = {r["answer_id"] for r in flagged.json()["items"]}
    assert booklet["answer_id"] in flagged_ids
    assert booklet["answer_id"] not in {r["answer_id"] for r in clean.json()["items"]}
    flagged_row = next(r for r in flagged.json()["items"]
                       if r["answer_id"] == booklet["answer_id"])
    assert flagged_row["needs_review"] is True


async def test_results_list_orders_stably_under_tied_timestamps(
    make_client, admin_conn, make_booklet
):
    """Two answers submitted at the IDENTICAL timestamp must still page
    without skipping or repeating a row — the answer_id tiebreak in
    core/results.py::list_results is what makes that safe."""
    first = make_booklet()
    second = make_booklet()

    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("SELECT submitted_at FROM answers WHERE answer_id = %s",
                    (first["answer_id"],))
        (tied_at,) = cur.fetchone()
        cur.execute("UPDATE answers SET submitted_at = %s WHERE answer_id = ANY(%s::uuid[])",
                    (tied_at, [first["answer_id"], second["answer_id"]]))
    admin_conn.commit()

    async with make_client(COLLEGE_A) as client:
        page1 = await client.get("/api/v1/results", params={
            "exam_id": first["exam_id"], "limit": 1, "offset": 0})
        page2 = await client.get("/api/v1/results", params={
            "exam_id": first["exam_id"], "limit": 1, "offset": 1})

    seen = {page1.json()["items"][0]["answer_id"], page2.json()["items"][0]["answer_id"]}
    assert seen == {first["answer_id"], second["answer_id"]}, (
        "tied submitted_at values caused a row to repeat or be skipped across pages"
    )


# ══════════════════════════ GET /results/booklets ════════════════════════

def _score(admin_conn, booklet, *, score, status):
    """Directly inserts a current evaluation_results row and sets the
    answer's status — these tests only need the resulting DB state, not a
    real evaluation run."""
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            """
            INSERT INTO evaluation_results
                (answer_id, reference_answer_variant_id, evaluator_type, score, is_current)
            VALUES (%s, %s, 'ai', %s, true)
            """,
            (booklet["answer_id"], booklet["variant_id"], score),
        )
        cur.execute("UPDATE answers SET status = %s WHERE answer_id = %s",
                    (status, booklet["answer_id"]))
    admin_conn.commit()


async def test_booklets_list_collapses_a_students_answers_into_one_row(
    make_client, make_booklet, admin_conn, college_b
):
    """Two answers for the same student+exam are one booklet: answer_count/
    scored_count reflect both, but total_score/max_score only cover the
    SCORED one — see core/booklet_summary.py.

    Uses college_b's dedicated (student_id, exam_id) rather than
    make_booklet()'s COLLEGE_A defaults — those default ids are also
    seed_minimal.sql's demo student/exam, which a developer's own manual
    testing against the same local DB can be adding answers to concurrently,
    making an exact answer_count/len(items) assertion flaky. college_b is
    synthetic reference data nothing else writes to."""
    owner = dict(college_id=COLLEGE_B, **COLLEGE_B_OWNER)
    first = make_booklet(marks_max=10.0, **owner)
    second = make_booklet(marks_max=6.0, **owner)
    _score(admin_conn, first, score=8.0, status="ai_scored")
    # second stays pending_evaluation / unscored.

    async with make_client(COLLEGE_B) as client:
        response = await client.get("/api/v1/results/booklets", params={
            "exam_id": first["exam_id"], "student_id": first["student_id"],
        })

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert len(items) == 1
    row = items[0]
    assert row["student_id"] == first["student_id"]
    assert row["exam_id"] == first["exam_id"]
    assert row["answer_count"] == 2
    assert row["scored_count"] == 1
    assert row["total_score"] == 8.0
    assert row["max_score"] == 10.0  # only the scored answer's marks_max
    assert row["status"] == "pending_evaluation"  # least-progressed answer wins
    assert row["needs_review"] is False


async def test_booklets_list_status_rollup_prefers_flagged_over_anything_else(
    make_client, make_booklet, admin_conn, college_b
):
    """Uses college_b's dedicated ids for the same reason as the collapsing
    test above — an exact single-row assertion needs a pair nothing else is
    concurrently writing to."""
    owner = dict(college_id=COLLEGE_B, **COLLEGE_B_OWNER)
    first = make_booklet(**owner)
    second = make_booklet(**owner)
    _score(admin_conn, first, score=9.0, status="finalized")
    _score(admin_conn, second, score=3.0, status="flagged")

    async with make_client(COLLEGE_B) as client:
        response = await client.get("/api/v1/results/booklets", params={
            "exam_id": first["exam_id"], "student_id": first["student_id"],
        })

    row = response.json()["items"][0]
    assert row["status"] == "flagged", (
        "one flagged answer must win the booklet's rolled-up status even "
        "when every other answer is finalized"
    )


async def test_booklets_list_needs_review_rolls_up_from_any_answer(
    make_client, make_booklet, admin_conn
):
    booklet = make_booklet()
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("UPDATE answer_blocks SET needs_review = true WHERE block_id = %s",
                    (booklet["block_id"],))
    admin_conn.commit()

    async with make_client(COLLEGE_A) as client:
        flagged = await client.get("/api/v1/results/booklets", params={"needs_review": "true"})
        clean = await client.get("/api/v1/results/booklets", params={"needs_review": "false"})

    key = (booklet["student_id"], booklet["exam_id"])
    flagged_keys = {(r["student_id"], r["exam_id"]) for r in flagged.json()["items"]}
    clean_keys = {(r["student_id"], r["exam_id"]) for r in clean.json()["items"]}
    assert key in flagged_keys
    assert key not in clean_keys


async def test_booklets_list_is_isolated_by_tenant(make_client, make_booklet, college_b):
    a = make_booklet(college_id=COLLEGE_A)
    b = make_booklet(college_id=college_b["college_id"],
                     student_id=college_b["student_id"],
                     exam_id=college_b["exam_id"])

    async with make_client(COLLEGE_A) as client:
        mine = await client.get("/api/v1/results/booklets")
    async with make_client(COLLEGE_B) as client:
        theirs = await client.get("/api/v1/results/booklets")

    assert mine.status_code == theirs.status_code == 200
    mine_keys = {(r["student_id"], r["exam_id"]) for r in mine.json()["items"]}
    theirs_keys = {(r["student_id"], r["exam_id"]) for r in theirs.json()["items"]}

    assert (a["student_id"], a["exam_id"]) in mine_keys
    assert (a["student_id"], a["exam_id"]) not in theirs_keys
    assert (b["student_id"], b["exam_id"]) in theirs_keys
    assert (b["student_id"], b["exam_id"]) not in mine_keys


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
        caller_reviewer_id = ac.identity["reviewer_id"]
        queued = await ac.post(EVALUATE, json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
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
                # NO reviewer_id (RE-4): the review is attributed to the
                # authenticated caller, and sending one is now a 422.
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
    # RE-4: attributed to the AUTHENTICATED caller. This is the assertion that
    # would fail if `reviewer_id` were ever read from the body again — and the
    # one that proves the value is not merely "some reviewer that exists".
    assert reviewer_id == caller_reviewer_id
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
    assert len(report["reviews"]["items"]) == 1
    assert report["reviews"]["total"] == 1
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
            json={"action": "overridden", "final_marks": 4.0},
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
                              json={"action": "overridden", "final_marks": 5.0})
        second = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                               json={"action": "overridden", "final_marks": 7.5})
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
    assert len(body["reviews"]["items"]) == 2
    assert body["reviews"]["total"] == 2
    assert body["final_marks"]["marks"] == 7.5, "the newest override stands"


async def test_flagged_override_carries_no_marks(make_client, make_booklet):
    """'flagged' escalates without deciding a mark, so final_marks must be
    absent — and a 'flagged' review must not displace the AI score."""
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        bad = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                            json={"action": "flagged", "final_marks": 3.0})
        missing_marks = await ac.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={"action": "overridden"})
        good = await ac.post(f"/api/v1/results/{booklet['answer_id']}/override",
                             json={"action": "flagged", "comment": "Illegible."})

    assert bad.status_code == 422, bad.text
    assert missing_marks.status_code == 422, missing_marks.text
    assert good.status_code == 201, good.text
    assert good.json()["final_marks"] is None


async def test_override_on_another_colleges_answer_is_404(make_client, make_booklet,
                                                          college_b, admin_conn):
    """And writes nothing — an override that 404s must not leave a review row
    behind under either college.

    The caller is a real, active second college (it used to be an id that
    existed nowhere, which since get_tenant_conn started verifying the college
    would be refused at the edge with 401 and prove nothing about the
    override path at all).
    """
    booklet = make_booklet(college_id=COLLEGE_A)

    async with make_client(COLLEGE_B) as stranger:
        response = await stranger.post(
            f"/api/v1/results/{booklet['answer_id']}/override",
            json={"action": "overridden", "final_marks": 10.0},
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

    The upload posted here and the booklet fixture are separate on purpose:
    this test covers the RE-SCORE path, where the regions already exist and
    POST /evaluate queues a booklet_eval directly. The path where they do not
    — upload, ingest, then score, with no CLI step — is
    test_upload_then_evaluate_ingests_and_scores_with_no_cli_step below. Both
    are real now; until the booklet_ingest job existed only this one was.
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
            "paper_id": booklet["paper_id"],
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
    assert len(report["components"]["items"]) >= 1
    assert report["confidence"]["question"] is not None
    # ...and every region is tied back to the component that scored it, which
    # is what lets the Results screen point at a rectangle on a page and say
    # which score it contributed to.
    regions = report["regions"]["items"]
    assert regions, "a scored answer must report the regions it was scored from"
    scored_regions = [r for r in regions if r["scored"]]
    assert scored_regions, "no region was linked to a component"
    for region in scored_regions:
        assert region["component_index"] is not None
        assert 0 <= region["component_index"] < len(report["components"]["items"])
        assert region["merged_with"] >= 1
        assert region["question"], "the region must name the question label it was assigned"
        assert region["component_score"] is not None
    assert len(report["history"]["items"]) == 1
    assert report["history"]["total"] == 1
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
    assert len(final["reviews"]["items"]) == 1
    # Still exactly one ledger row: an override is not a re-score.
    assert len(final["history"]["items"]) == 1


async def test_evaluating_a_booklet_with_no_regions_fails_loudly(
    make_client, make_booklet, track_jobs
):
    """A booklet_eval job whose booklet has no regions must FAIL, not succeed
    with an empty report.

    An empty report would aggregate to a total of zero and read, to a teacher,
    exactly like a student who wrote nothing. The failure names the missing
    step instead.

    THE JOB IS QUEUED DIRECTLY, not through POST /api/v1/evaluate, and that is
    the point of this test now rather than a shortcut. Since the booklet_ingest
    job exists, the endpoint checks for regions and queues an INGESTION when
    there are none — so it can no longer produce this state, and a test that
    went through it would silently stop testing the failure posture and start
    testing the ingest path instead. The state is still reachable (regions
    deleted between the check and the claim, a hand-queued job, a requeue after
    the answer was cleaned up), so the guard still has to hold, and it has to
    keep naming ingestion as the missing step.
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

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            job = evaluation_service.enqueue_booklet_evaluation(
                cur,
                college_id=COLLEGE_A,
                upload_id=booklet["upload_id"],
                exam_id=booklet["exam_id"],
                student_id=booklet["student_id"],
                stub=True,
            )
        conn.commit()
    finally:
        conn.close()
    job_id = job["job_id"]
    track_jobs(job_id)

    worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                       job_types=["booklet_eval"])

    async with make_client(COLLEGE_A) as ac:
        done = await ac.get(f"/api/v1/jobs/{job_id}")

    body = done.json()
    assert body["status"] == "failed", body
    assert "ingest" in body["error"].lower()
    assert body["result"] is None


# ═══════════════ upload -> ingest -> evaluate, with no CLI step ═════════════

async def test_upload_then_evaluate_ingests_and_scores_with_no_cli_step(
    make_client, make_booklet, track_uploads, track_jobs, monkeypatch,
    sample_booklet_bytes,
):
    """THE GAP THIS DAY CLOSED: upload a PDF, ask for an evaluation, and get a
    score — without anyone running scripts/ingest_booklet.py in between.

    Until the booklet_ingest job existed, POST /upload stored a file that
    nothing segmented, and POST /evaluate then failed with NoRegionsError
    unless a human had run the CLI by hand. Both halves now run in the worker:
    an ingest job writes the answer_blocks, and chains into the evaluation job
    that scores them.

    Stub mode throughout, and no network: the segmenter's stub path replaces
    the layout model and the OCR ensemble, the plugins' stub paths replace the
    extractors and Groq, and core.llm._generate is monkeypatched to blow up so
    that a stub run which quietly stopped being a stub run fails here instead
    of spending money.

    The PDF is REAL (the 4-page benchmark booklet) even though segmentation is
    stubbed, because everything before segmentation is not stubbed at all —
    the file really is fetched back out of storage and really is rasterized by
    pypdfium2. That is the half of this wiring that a fake PDF would skip.
    """
    def _explode(*args, **kwargs):
        raise AssertionError(
            "core.llm._generate was called during a stub-mode run — stub mode "
            "must not reach Groq."
        )

    monkeypatch.setattr("core.llm._generate", _explode)

    worker = _load_worker()

    # A booklet that has been uploaded but NOT ingested: a paper whose 'Q1'
    # slot holds a live question, and no answers or answer_blocks at all.
    booklet = make_booklet(ingested=False)

    async with make_client(COLLEGE_A) as ac:
        uploaded = await ac.post("/api/v1/upload", files={
            "file": ("booklet.pdf", sample_booklet_bytes, "application/pdf")})
        assert uploaded.status_code == 201, uploaded.text
        upload_id = uploaded.json()["upload_id"]
        track_uploads(upload_id)

        queued = await ac.post(EVALUATE, json={
            "upload_id": upload_id,
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
            "stub": True,
        })
        assert queued.status_code == 202, queued.text
        body = queued.json()
        ingest_job_id = body["job_id"]
        track_jobs(ingest_job_id)

        # An INGESTION was queued, and the response says so rather than
        # leaving the client to discover it from a job that is not what it
        # asked for.
        assert body["job_type"] == "booklet_ingest"
        assert body["ingest_job_id"] == ingest_job_id

        # ── the ingest pass ────────────────────────────────────────────────
        processed = worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                                       job_types=["booklet_ingest"])
        assert processed is not None and processed["job_id"] == ingest_job_id

        ingested = (await ac.get(f"/api/v1/jobs/{ingest_job_id}")).json()
        assert ingested["status"] == "succeeded", ingested
        assert ingested["job_type"] == "booklet_ingest"
        assert ingested["result"]["counts"]["blocks_written"] >= 1
        assert ingested["result"]["counts"]["pages"] >= 1
        assert ingested["result"]["skipped"] is None

        # ── which chained into the evaluation pass ─────────────────────────
        eval_job_id = ingested["result"]["evaluation_job_id"]
        assert eval_job_id, "a successful ingestion must queue the evaluation"
        track_jobs(eval_job_id)

        processed = worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                                       job_types=["booklet_eval"])
        assert processed is not None and processed["job_id"] == eval_job_id

        scored = (await ac.get(f"/api/v1/jobs/{eval_job_id}")).json()
        assert scored["status"] == "succeeded", scored
        assert scored["result"]["counts"]["questions"] >= 1

    # And the regions the ingestion wrote are really there, attributed to the
    # upload the client asked about — which is what makes them findable again
    # when this booklet is re-scored.
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*)
                  FROM answers a
                  JOIN answer_blocks ab ON ab.answer_id = a.answer_id
                 WHERE a.student_id = %s AND a.exam_id = %s
                   AND a.source_scan_url = %s
                """,
                (booklet["student_id"], booklet["exam_id"],
                 uploaded.json()["blob_url"]),
            )
            assert cur.fetchone()[0] >= 1
    finally:
        conn.close()


async def test_re_evaluating_an_ingested_booklet_does_not_duplicate_blocks(
    make_client, make_booklet, track_uploads, track_jobs, monkeypatch,
    sample_booklet_bytes,
):
    """A re-score reads the regions that exist. It does not re-segment.

    This is the whole reason ingestion is its own job type rather than a phase
    of booklet_eval. answer_blocks rows carry region provenance (migration 013)
    and have no version history (PROJECT_CONTEXT.md §7 open decision 2), and
    core/booklet_persist.py::insert_region_block is an unconditional INSERT —
    so an ingestion that ran a second time would not replace a student's
    regions, it would DOUBLE them, and every region would then be scored
    twice.

    Asserted at two levels, because either alone would pass for the wrong
    reason: the block ids are byte-identical before and after (a count alone
    would miss a delete-and-rewrite that happened to produce the same number),
    and the second /evaluate queues a booklet_eval directly with no ingestion
    job at all.
    """
    monkeypatch.setattr("core.llm._generate", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("stub mode must not reach Groq")))

    worker = _load_worker()
    booklet = make_booklet(ingested=False)

    def block_rows():
        conn = _admin_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ab.block_id, ab.page_number, ab.region_bbox,
                           ab.sequence_order
                      FROM answers a
                      JOIN answer_blocks ab ON ab.answer_id = a.answer_id
                     WHERE a.student_id = %s AND a.exam_id = %s
                       AND a.source_scan_url = %s
                     ORDER BY ab.block_id
                    """,
                    (booklet["student_id"], booklet["exam_id"], blob_url),
                )
                return cur.fetchall()
        finally:
            conn.close()

    async with make_client(COLLEGE_A) as ac:
        uploaded = await ac.post("/api/v1/upload", files={
            "file": ("booklet.pdf", sample_booklet_bytes, "application/pdf")})
        upload_id = uploaded.json()["upload_id"]
        blob_url = uploaded.json()["blob_url"]
        track_uploads(upload_id)

        request = {
            "upload_id": upload_id,
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
            "stub": True,
        }

        first = (await ac.post(EVALUATE, json=request)).json()
        track_jobs(first["job_id"])
        assert first["job_type"] == "booklet_ingest"

        worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                           job_types=["booklet_ingest"])
        ingested = (await ac.get(f"/api/v1/jobs/{first['job_id']}")).json()
        assert ingested["status"] == "succeeded", ingested
        track_jobs(ingested["result"]["evaluation_job_id"])
        worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                           job_types=["booklet_eval"])

        after_first = block_rows()
        assert after_first, "the first pass should have written some regions"

        # ── ask for the very same evaluation again ─────────────────────────
        second = (await ac.post(EVALUATE, json=request)).json()
        track_jobs(second["job_id"])

        # No ingestion this time: the regions exist, so the endpoint queues
        # the evaluation directly.
        assert second["job_type"] == "booklet_eval"
        assert second["ingest_job_id"] is None

        worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                           job_types=["booklet_eval"])
        rescored = (await ac.get(f"/api/v1/jobs/{second['job_id']}")).json()
        assert rescored["status"] == "succeeded", rescored

    assert block_rows() == after_first, (
        "re-evaluating must not touch answer_blocks — same rows, same ids, "
        "same bboxes, same order"
    )


async def test_a_requeued_ingest_job_neither_re_ingests_nor_double_queues(
    make_client, make_booklet, track_uploads, track_jobs, sample_booklet_bytes
):
    """Running the SAME ingest job twice is safe on both halves.

    This is the worker's known crash window (scripts/run_job_worker.py: killed
    between finishing the work and marking the row terminal, recovered with
    --requeue-stalled). The second run must not duplicate the student's
    regions, and must not queue a second evaluation of the same booklet.
    """
    worker = _load_worker()
    booklet = make_booklet(ingested=False)

    async with make_client(COLLEGE_A) as ac:
        uploaded = await ac.post("/api/v1/upload", files={
            "file": ("booklet.pdf", sample_booklet_bytes, "application/pdf")})
        track_uploads(uploaded.json()["upload_id"])
        queued = (await ac.post(EVALUATE, json={
            "upload_id": uploaded.json()["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
            "stub": True,
        })).json()
    ingest_job_id = queued["job_id"]
    track_jobs(ingest_job_id)

    worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                       job_types=["booklet_ingest"])

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            job = core.jobs.get_job(cur, job_id=ingest_job_id, college_id=COLLEGE_A)
            first_eval_id = job["result"]["evaluation_job_id"]
            track_jobs(first_eval_id)
            blocks_after_first = _block_count(cur, booklet, uploaded.json()["blob_url"])
            # Put it back in the queue exactly as --requeue-stalled would.
            core.jobs.requeue(cur, job_id=ingest_job_id, error="simulated stall")
        conn.commit()
    finally:
        conn.close()

    worker.process_one(stub=True, stub_seconds=0.0, dry_run=False,
                       job_types=["booklet_ingest"])

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            job = core.jobs.get_job(cur, job_id=ingest_job_id, college_id=COLLEGE_A)
            assert job["status"] == "succeeded", job
            # The re-run recognised the booklet as already ingested...
            assert job["result"]["skipped"] == "already_ingested"
            # ...wrote nothing...
            assert _block_count(cur, booklet, uploaded.json()["blob_url"]) == \
                blocks_after_first
            # ...and pointed at the evaluation job it queued the first time
            # rather than queueing a second one.
            assert job["result"]["evaluation_job_id"] == first_eval_id
            cur.execute(
                """
                SELECT count(*) FROM evaluation_jobs
                 WHERE job_type = 'booklet_eval' AND payload->>'upload_id' = %s
                """,
                (uploaded.json()["upload_id"],),
            )
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def _block_count(cur, booklet, blob_url) -> int:
    cur.execute(
        """
        SELECT count(*)
          FROM answers a
          JOIN answer_blocks ab ON ab.answer_id = a.answer_id
         WHERE a.student_id = %s AND a.exam_id = %s AND a.source_scan_url = %s
        """,
        (booklet["student_id"], booklet["exam_id"], blob_url),
    )
    return int(cur.fetchone()[0])


# ════════════════ per-region detail and the page-image endpoint ═════════════
#
# The Results screen (UI-2B) shows a scanned page beside the text read out of
# each region on it. Two properties matter more than the rest here:
#
#   1. ONE CALL DRIVES THE SCREEN. GET /results/{answer_id} carries every
#      region's page, bbox, classification, flags and reading order, plus the
#      merged text §7D actually scored AND the seam between the regions it was
#      built from. A UI that had to reconstruct the merge would get the
#      separator wrong; one that only got the merged string could not show a
#      teacher which page a sentence came from.
#   2. THE IMAGE URL IS A CREDENTIAL, AND THE PAGE IS SOMEONE'S. It expires, it
#      is minted per request, it is never written to the database, and asking
#      for another college's page is byte-identical to asking for one that does
#      not exist.


def _add_region(admin_conn, answer_id, *, page, sequence, bbox, content=None,
                block_type="text", label="text", confidence=0.95,
                needs_review=False, page_image_url=None):
    """One extra answer_blocks row, the shape booklet ingestion writes.

    `content=None` is the REAL ingested shape — core/booklet_persist.py writes
    it as a literal NULL for every region cut out of a scan — so the tests
    below can build both cases: a region whose text is persisted and one whose
    is not.
    """
    block_id = str(uuid.uuid4())
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            """
            INSERT INTO answer_blocks (block_id, answer_id, block_type, content,
                                       sequence_order, page_number, region_bbox,
                                       page_image_url, classification_label,
                                       classification_confidence, needs_review)
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
            """,
            (block_id, answer_id, block_type, content, sequence, page,
             json.dumps(bbox), page_image_url, label, confidence, needs_review),
        )
    admin_conn.commit()
    return block_id


def _add_extraction(admin_conn, block_id, *, text, ocr_confidence=None,
                    plugin="text_extraction", plugin_version="0.2.0",
                    mode="ocr", engines=None):
    """One current answer_block_extractions row (migration 019) — the shape
    core/booklet_evaluator.py's persist_extractions() writes after a real
    evaluation run, built directly here so a test can set up the READ side
    (GET /results/{answer_id}) without running a real OCR pipeline. Cleaned
    up by tests/test_api/conftest.py's make_booklet teardown, which deletes
    answer_block_extractions by block_id BEFORE deleting answer_blocks
    (migration 019's FK has no ON DELETE CASCADE)."""
    extraction_id = str(uuid.uuid4())
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            """
            INSERT INTO answer_block_extractions
                (extraction_id, block_id, text, ocr_confidence, plugin,
                 plugin_version, mode, engines, is_current)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, TRUE)
            """,
            (extraction_id, block_id, text, ocr_confidence, plugin,
             plugin_version, mode, json.dumps(engines) if engines is not None else None),
        )
    admin_conn.commit()
    return extraction_id


def _set_page_images(admin_conn, answer_id, ref):
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            "UPDATE answer_blocks SET page_image_url = %s WHERE answer_id = %s",
            (ref, str(answer_id)),
        )
    admin_conn.commit()


async def test_results_carry_per_region_text_and_bbox_for_a_multi_region_question(
    make_client, make_booklet, admin_conn
):
    """A question whose answer spans a page break comes back as REGIONS, not
    as one anonymous blob of text.

    This is the payload the Results screen is built on: every region carries
    the page it is on and the rectangle it occupies (so the overlay and
    zoom-to-region have something to point at), and `merged_text` carries both
    the string §7D scored and the offset of each region inside it — the seam.
    """
    booklet = make_booklet()
    answer_id = booklet["answer_id"]
    # The fixture's own block is page 1 / sequence 0 and carries answer_text.
    second = _add_region(admin_conn, answer_id, page=2, sequence=1,
                         bbox=[10, 20, 300, 120], content="It continues here.")

    async with make_client(COLLEGE_A) as ac:
        response = await ac.get(f"/api/v1/results/{answer_id}")

    assert response.status_code == 200, response.text
    body = response.json()

    regions = body["regions"]["items"]
    assert body["regions"]["total"] == 2
    assert body["regions"]["truncated"] is False
    assert [r["page_number"] for r in regions] == [1, 2], \
        "regions must come back in READING ORDER — page, then sequence"
    assert [r["reading_order"] for r in regions] == [0, 1]

    first, cont = regions
    assert cont["block_id"] == second
    assert cont["region_bbox"] == [10, 20, 300, 120]
    assert cont["text"] == "It continues here."
    assert cont["text_source"] == "answer_blocks.content"
    assert cont["block_type"] == "text"
    assert cont["classification_label"] == "text"
    assert cont["classification_confidence"] == 0.95
    assert cont["needs_review"] is False
    assert cont["ingestion_flags"] == []

    # BOTH shapes, which is the whole requirement: the merged text that was
    # scored, and which region each part of it came from.
    merged = body["merged_text"]
    assert merged["available"] is True
    assert merged["text"] == f"{first['text']}{merged['separator']}It continues here."
    assert [p["block_id"] for p in merged["parts"]] == [first["block_id"], second]
    assert [p["page_number"] for p in merged["parts"]] == [1, 2]
    # The seam: the second region starts exactly where the first one's text
    # plus the separator ends, so a UI can highlight the page break.
    seam = merged["parts"][1]
    assert seam["offset"] == len(first["text"]) + len(merged["separator"])
    assert merged["text"][seam["offset"]:seam["offset"] + seam["length"]] == \
        "It continues here."

    # And the page strip the left-hand panel needs.
    assert [p["page_number"] for p in body["pages"]] == [1, 2]
    assert all(p["has_image"] is False for p in body["pages"]), \
        "these fixture blocks have no page_image_url, and the response says so"


async def test_a_region_with_no_persisted_extraction_says_so_rather_than_lying(
    make_client, make_booklet, admin_conn
):
    """The "never tried" half of migration 019's two-state distinction.

    A region cut out of a scan has `content` NULL (ingestion writes it that
    way), and NO answer_block_extractions row exists for it yet — the
    booklet has never been evaluated, or the region's extraction failed
    before anything was written (core/booklet_evaluator.persist_extractions
    only writes a row for extraction.ok). The response must report that as
    "never extracted", not as an empty answer, and must refuse to show a
    merged answer with a hole in it. See the test right after this one for
    the OTHER half: a row that exists but whose extraction genuinely found
    nothing.
    """
    booklet = make_booklet()
    scanned = _add_region(admin_conn, booklet["answer_id"], page=2, sequence=1,
                          bbox=[0, 0, 100, 50], content=None, needs_review=True)

    async with make_client(COLLEGE_A) as ac:
        body = (await ac.get(f"/api/v1/results/{booklet['answer_id']}")).json()

    region = next(r for r in body["regions"]["items"] if r["block_id"] == scanned)
    assert region["text"] is None
    assert region["text_source"] is None, \
        "a null text_source is what tells a client 'not stored' from 'blank'"
    assert region["extraction_available"] is False
    assert region["needs_review"] is True
    assert region["ingestion_flags"] == ["needs_review_at_ingestion"]

    merged = body["merged_text"]
    assert merged["available"] is False
    assert merged["text"] is None, "a partial merge would read as a complete answer"
    assert "no persisted extraction" in merged["reason"]


async def test_a_region_with_a_persisted_extraction_returns_the_real_text(
    make_client, make_booklet, admin_conn
):
    """The other half: once core/booklet_evaluator.py has written a current
    answer_block_extractions row for a scanned region (migration 019), the
    API returns its actual text, confidence and provenance instead of null —
    this is what lets RegionCard.tsx show a real OCR read next to the score
    instead of the old hardcoded "not stored" placeholder.
    """
    booklet = make_booklet()
    scanned = _add_region(admin_conn, booklet["answer_id"], page=2, sequence=1,
                          bbox=[0, 0, 100, 50], content=None, needs_review=True)
    _add_extraction(admin_conn, scanned, text="Handwritten answer, OCR'd.",
                    ocr_confidence=0.82, mode="ocr", engines=["paddleocr"])

    async with make_client(COLLEGE_A) as ac:
        body = (await ac.get(f"/api/v1/results/{booklet['answer_id']}")).json()

    region = next(r for r in body["regions"]["items"] if r["block_id"] == scanned)
    assert region["text"] == "Handwritten answer, OCR'd."
    assert region["text_source"] == "answer_block_extractions"
    assert region["extraction_available"] is True
    assert region["ocr_confidence"] == pytest.approx(0.82, abs=1e-3)
    assert region["ocr_confidence_source"] == "answer_block_extractions"
    assert region["extraction_plugin"] == "text_extraction"
    assert region["extraction_plugin_version"] == "0.2.0"
    assert region["extraction_mode"] == "ocr"
    assert region["extraction_engines"] == ["paddleocr"]

    # Both this region and the booklet fixture's own first region have real
    # text now, so the merge that §7D scored is fully reconstructable.
    merged = body["merged_text"]
    assert merged["available"] is True
    assert merged["text"].endswith("Handwritten answer, OCR'd.")


async def test_a_persisted_extraction_that_found_nothing_is_not_a_blank_region(
    make_client, make_booklet, admin_conn
):
    """migration 019's COMMENT ON COLUMN text: NULL is legitimate when an
    extraction genuinely ran and found nothing — a real, recorded outcome,
    distinct from never having tried at all (the test two above this one).
    text_source must say the row exists even though `text` itself is null.
    """
    booklet = make_booklet()
    scanned = _add_region(admin_conn, booklet["answer_id"], page=2, sequence=1,
                          bbox=[0, 0, 100, 50], content=None, needs_review=True)
    _add_extraction(admin_conn, scanned, text=None, ocr_confidence=0.05,
                    mode="ocr", engines=["tesseract"])

    async with make_client(COLLEGE_A) as ac:
        body = (await ac.get(f"/api/v1/results/{booklet['answer_id']}")).json()

    region = next(r for r in body["regions"]["items"] if r["block_id"] == scanned)
    assert region["text"] is None
    assert region["text_source"] == "answer_block_extractions", \
        "a row exists (extraction ran) even though its text is null"
    assert region["extraction_available"] is True
    assert region["ocr_confidence"] == pytest.approx(0.05, abs=1e-3)

    # The merge still refuses to show a partial answer — an empty part is
    # still a missing part.
    merged = body["merged_text"]
    assert merged["available"] is False


async def test_page_image_returns_a_short_lived_presigned_url(
    make_client, make_booklet, admin_conn, monkeypatch
):
    """The happy path: a URL, an expiry, and nothing written back."""
    booklet = make_booklet()
    _set_page_images(admin_conn, booklet["answer_id"], "bucket/pages/page-1.png")

    calls: list[tuple] = []

    def fake_presign(ref, expires_in=3600):
        calls.append((ref, expires_in))
        return f"https://storage.example/{ref}?sig={len(calls)}"

    monkeypatch.setattr(core.storage, "presigned_get_url", fake_presign)

    async with make_client(COLLEGE_A) as ac:
        response = await ac.get(
            f"/api/v1/results/{booklet['answer_id']}/pages/1/image")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer_id"] == booklet["answer_id"]
    assert body["page_number"] == 1
    assert body["url"] == "https://storage.example/bucket/pages/page-1.png?sig=1"
    assert body["expires_in"] == evaluation_service.PAGE_IMAGE_URL_TTL_SECONDS == 300
    assert body["expires_at"]
    # The stable ref went to core/storage.py unchanged, with the SHORT expiry —
    # not boto3's hour-long default.
    assert calls == [("bucket/pages/page-1.png", 300)]


async def test_a_presigned_url_is_generated_per_request_and_never_stored(
    make_client, make_booklet, admin_conn, monkeypatch
):
    """Two calls, two URLs, and NOTHING in the database ever holds one.

    CLAUDE_CONTEXT.md §10: page_image_url/blob_url are stable "bucket/key"
    strings, never presigned URLs — a presigned URL expires, and a value baked
    into a column has to stay valid indefinitely. This asserts the column is
    untouched after two requests AND that no answer-schema column anywhere
    contains the minted URL.
    """
    booklet = make_booklet()
    _set_page_images(admin_conn, booklet["answer_id"], "bucket/pages/page-1.png")

    minted: list[str] = []

    def fake_presign(ref, expires_in=3600):
        minted.append(f"https://storage.example/{ref}?sig={len(minted)}&exp={expires_in}")
        return minted[-1]

    monkeypatch.setattr(core.storage, "presigned_get_url", fake_presign)

    async with make_client(COLLEGE_A) as ac:
        first = await ac.get(f"/api/v1/results/{booklet['answer_id']}/pages/1/image")
        second = await ac.get(f"/api/v1/results/{booklet['answer_id']}/pages/1/image")

    assert first.status_code == second.status_code == 200
    assert first.json()["url"] != second.json()["url"], \
        "each request must mint its own URL — a cached one would outlive its expiry"

    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            "SELECT DISTINCT page_image_url, blob_url FROM answer_blocks "
            " WHERE answer_id = %s",
            (booklet["answer_id"],),
        )
        rows = cur.fetchall()
        assert rows == [("bucket/pages/page-1.png", None)], \
            "the stored reference must still be the stable bucket/key one"

        # Nothing anywhere in this answer's rows learned the URL.
        for url in minted:
            cur.execute(
                """
                SELECT count(*) FROM answer_blocks
                 WHERE answer_id = %s
                   AND (coalesce(page_image_url, '') LIKE %s
                        OR coalesce(blob_url, '')   LIKE %s)
                """,
                (booklet["answer_id"], f"%{url}%", f"%{url}%"),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) FROM answers WHERE answer_id = %s "
                "  AND coalesce(source_scan_url, '') LIKE %s",
                (booklet["answer_id"], f"%{url}%"),
            )
            assert cur.fetchone()[0] == 0


async def test_page_image_for_another_colleges_answer_is_404_and_indistinguishable(
    make_client, make_booklet, admin_conn, college_b, monkeypatch
):
    """THE CROSS-TENANT PROOF for the image endpoint.

    College B asks for college A's page by its exact answer id and gets a 404
    that is byte-identical to the one a made-up answer id gets — and to the one
    a page this booklet does not have gets. 403 would confirm the answer
    exists, turning the endpoint into an existence oracle over other colleges'
    ids; and because the URL is minted only after the tenant-scoped query
    resolves, a stranger's request never reaches core/storage.py at all.
    """
    booklet = make_booklet(college_id=COLLEGE_A)
    _set_page_images(admin_conn, booklet["answer_id"], "bucket/pages/page-1.png")
    b_booklet = make_booklet(college_id=college_b["college_id"],
                             student_id=college_b["student_id"],
                             exam_id=college_b["exam_id"])
    _set_page_images(admin_conn, b_booklet["answer_id"], "bucket/pages/b-page-1.png")

    presigned_for: list[str] = []

    def fake_presign(ref, expires_in=3600):
        presigned_for.append(ref)
        return f"https://storage.example/{ref}"

    monkeypatch.setattr(core.storage, "presigned_get_url", fake_presign)

    path = f"/api/v1/results/{booklet['answer_id']}/pages/1/image"
    async with make_client(COLLEGE_A) as owner:
        mine = await owner.get(path)
    async with make_client(COLLEGE_B) as stranger:
        theirs = await stranger.get(path)
        made_up = await stranger.get(f"/api/v1/results/{uuid.uuid4()}/pages/1/image")
        no_such_page = await stranger.get(
            f"/api/v1/results/{b_booklet['answer_id']}/pages/99/image")
        own = await stranger.get(
            f"/api/v1/results/{b_booklet['answer_id']}/pages/1/image")

    assert mine.status_code == 200, mine.text
    # B is a real, active tenant with a page of its own — which is what makes
    # the 404 below isolation rather than a rejected caller.
    assert own.status_code == 200, own.text

    assert theirs.status_code == 404, theirs.text
    assert made_up.status_code == 404
    assert no_such_page.status_code == 404
    assert theirs.json()["detail"].replace(booklet["answer_id"], "X") == \
        made_up.json()["detail"].replace(made_up.request.url.path.split("/")[4], "X")
    # Not one fact about A's row came back...
    assert "bucket/pages/page-1.png" not in theirs.text
    assert "storage.example" not in theirs.text
    # ...and no URL was ever minted for A's page on B's behalf.
    assert presigned_for == ["bucket/pages/page-1.png", "bucket/pages/b-page-1.png"]


async def test_page_image_with_no_stored_image_is_the_same_404(
    make_client, make_booklet
):
    """A block predating booklet ingestion has a NULL page_image_url (migration
    013's columns are all nullable). That is a 404 too, with the same body —
    "nothing to serve" and "not yours" must not be tellable apart."""
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        response = await ac.get(
            f"/api/v1/results/{booklet['answer_id']}/pages/1/image")

    assert response.status_code == 404, response.text
    assert "No page 1 image" in response.json()["detail"]


async def test_a_dummy_storage_page_image_is_503_not_a_fabricated_url(
    make_client, make_booklet, admin_conn
):
    """core/storage.py's dummy mode is STORAGE_MODE's default and does no I/O:
    a "dummy-storage/..." ref is a placeholder with no object behind it, so
    there is no presigned form to hand out. 503 names the misconfiguration
    instead of 404ing (which would send an operator hunting a tenant bug) or
    returning a URL that cannot resolve."""
    booklet = make_booklet()
    _set_page_images(admin_conn, booklet["answer_id"],
                     "dummy-storage/pages/page-1.png")

    async with make_client(COLLEGE_A) as ac:
        response = await ac.get(
            f"/api/v1/results/{booklet['answer_id']}/pages/1/image")

    assert response.status_code == 503, response.text
    assert "STORAGE_MODE" in response.json()["detail"]
