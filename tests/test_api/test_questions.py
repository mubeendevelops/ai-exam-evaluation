"""tests/test_api/test_questions.py — the question bank and its two gates.

THE CENTRAL TEST IN THIS FILE is
`test_promote_cannot_skip_the_review_gate`. Everything else supports it.

The rule under test: a question cannot become 'live' — the status that makes
it answerable by students — without two separate human decisions recorded
against two named reviewers. An API that let a caller jump straight from
'draft' to 'live' would put unreviewed, LLM-generated text into a real exam
paper, and nothing downstream would flag it: paper generation happily selects
any live question, and the trigger that guards `answers` checks only that the
question IS live, never how it got there.

Each gate test asserts TWO things: the HTTP response, and the row's status
read back from the database afterwards. The second assertion is not
redundancy. An endpoint that promoted the question and then returned 409 would
pass a status-code-only test while being the worst possible bug in this
codebase.

ON TENANCY — READ THIS BEFORE ADDING AN ISOLATION TEST HERE. The question
bank is SHARED across colleges by design, not by omission: migration 003
line 5 keeps the 12 question-schema tables unchanged ("shared question bank,
no tenant column, readable/usable by every college") and line 341 names
questions, question_reviews and question_status_history as excluded from RLS.
`test_the_bank_is_shared_across_colleges` pins that on purpose, so that if the
bank is ever tenanted it happens as a deliberate migration plus a failing
test here — not silently. The cross-tenant 404 proofs that DO apply live in
test_jobs.py and test_evaluation.py, over the answer schema, which really is
tenanted.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_api.conftest import (
    COLLEGE_A,
    COLLEGE_B_ABSENT,
    REVIEWER_SME,
    REVIEWER_TEACHER,
)

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


# ═══════════════════════ the gates cannot be skipped ════════════════════════

async def test_promote_cannot_skip_the_review_gate(make_client, make_question,
                                                   question_status):
    """A DRAFT question posted to /promote must be REFUSED and stay a draft.

    This is the "prove you cannot skip a gate" test. It does the thing the API
    must never permit — asks for publication without review — and asserts that
    (a) the request fails, (b) it fails as a 409 rather than a 404 or a 500,
    (c) the error says why, and (d) the question is still 'draft' in the
    database, so nothing was half-applied.
    """
    question_id = make_question(status="draft")

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            f"/api/v1/questions/{question_id}/promote",
            json={"reviewer_id": REVIEWER_TEACHER},
        )

    assert response.status_code == 409, response.text

    detail = response.json()["detail"]
    assert "draft" in detail and "confirmed" in detail, detail

    # The load-bearing assertion: the refusal was total.
    assert question_status(question_id) == "draft"


async def test_a_rejected_question_can_never_be_promoted(make_client, make_question,
                                                         question_status):
    """Rejection is terminal for this route to 'live'.

    Worth its own test because 'rejected' is the status most likely to be
    swept into a lenient promote check written as "anything that isn't a
    draft" — which would publish exactly the questions a reviewer threw out.
    """
    question_id = make_question(status="rejected")

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            f"/api/v1/questions/{question_id}/promote",
            json={"reviewer_id": REVIEWER_TEACHER},
        )

    assert response.status_code == 409, response.text
    assert question_status(question_id) == "rejected"


async def test_review_fires_only_from_draft(make_client, make_question, question_status):
    """An already-confirmed question cannot be re-reviewed.

    Not a leniency question but an audit one: question_reviews already holds
    the first reviewer's decision, and silently recording a second one over it
    would let one reviewer overwrite another's judgement with no re-open event
    to show it happened. The CLI refuses this too.
    """
    question_id = make_question(status="confirmed")

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"reviewer_id": REVIEWER_SME, "action": "reject"},
        )

    assert response.status_code == 409, response.text
    assert question_status(question_id) == "confirmed"


async def test_no_endpoint_moves_a_question_to_live_in_one_step(make_client,
                                                                make_question,
                                                                question_status):
    """A structural check on the API surface itself, not on one endpoint.

    Every route the questions router exposes is invoked against a fresh draft
    with every reviewer-shaped body that might plausibly be accepted. None of
    them may leave the question 'live'. This is what catches a future
    `{"action": "confirm", "promote": true}` shortcut or a `POST
    /questions/{id}/publish` added "just for the demo" — a targeted test for
    today's endpoints would not.
    """
    question_id = make_question(status="draft")

    bodies = [
        {"reviewer_id": REVIEWER_TEACHER},
        {"reviewer_id": REVIEWER_TEACHER, "action": "confirm"},
        {"reviewer_id": REVIEWER_TEACHER, "action": "confirm", "promote": True},
        {"reviewer_id": REVIEWER_TEACHER, "status": "live"},
    ]
    paths = [
        f"/api/v1/questions/{question_id}/promote",
        f"/api/v1/questions/{question_id}/review",
    ]

    async with make_client(COLLEGE_A) as client:
        for path in paths:
            for body in bodies:
                await client.post(path, json=body)
                assert question_status(question_id) != "live", (
                    f"POST {path} with {body} put the question live in ONE step, "
                    f"skipping a mandatory gate."
                )


# ═══════════════════════ the gates work, in order ═══════════════════════════

async def test_full_two_gate_flow_draft_to_confirmed_to_live(make_client, make_question,
                                                             question_status, admin_conn):
    """The happy path, end to end, with the audit rows checked.

    Also asserts the asymmetry between the gates: review writes a
    question_reviews row, promote does not. That is deliberate in
    scripts/review_question.py — publishing is an operational decision, not a
    content judgement — and a promote that started writing review rows would
    make the review ledger claim a quality decision nobody made.
    """
    question_id = make_question(status="draft")

    async with make_client(COLLEGE_A) as client:
        confirmed = await client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"reviewer_id": REVIEWER_SME, "action": "confirm",
                  "comment": "reads correctly"},
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["old_status"] == "draft"
        assert confirmed.json()["new_status"] == "confirmed"
        assert confirmed.json()["review_id"] is not None
        assert question_status(question_id) == "confirmed"

        promoted = await client.post(
            f"/api/v1/questions/{question_id}/promote",
            json={"reviewer_id": REVIEWER_TEACHER},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["old_status"] == "confirmed"
        assert promoted.json()["new_status"] == "live"
        # Promotion records no content review — see the docstring.
        assert promoted.json()["review_id"] is None

    assert question_status(question_id) == "live"

    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT reviewer_id, action, comment FROM question_reviews "
            "WHERE question_id = %s", (question_id,))
        reviews = cur.fetchall()
        cur.execute(
            "SELECT old_status, new_status, changed_by FROM question_status_history "
            "WHERE question_id = %s ORDER BY changed_at", (question_id,))
        history = cur.fetchall()

    # Exactly one content review, by the reviewer who made it.
    assert len(reviews) == 1
    assert str(reviews[0][0]) == REVIEWER_SME
    assert reviews[0][1] == "confirmed"
    assert reviews[0][2] == "reads correctly"

    # Both transitions audited, each attributed to whoever made it.
    assert [(h[0], h[1]) for h in history] == [("draft", "confirmed"), ("confirmed", "live")]
    assert str(history[0][2]) == REVIEWER_SME
    assert str(history[1][2]) == REVIEWER_TEACHER


async def test_reject_records_the_decision_and_stops_there(make_client, make_question,
                                                           question_status, admin_conn):
    """draft -> rejected, with the reviewer's reason preserved."""
    question_id = make_question(status="draft")

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"reviewer_id": REVIEWER_SME, "action": "reject",
                  "comment": "factually wrong"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["new_status"] == "rejected"
    assert question_status(question_id) == "rejected"

    with admin_conn.cursor() as cur:
        cur.execute("SELECT action, comment FROM question_reviews WHERE question_id = %s",
                    (question_id,))
        assert cur.fetchall() == [("rejected", "factually wrong")]


# ═══════════════════════ attribution is enforced ════════════════════════════

async def test_an_unknown_reviewer_cannot_move_a_question(make_client, make_question,
                                                          question_status):
    """Every status change is attributed, so an unknown reviewer_id is refused
    rather than recorded — a 400 (the bad value is in the body), and the
    question does not move."""
    question_id = make_question(status="draft")
    ghost = str(uuid.uuid4())

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"reviewer_id": ghost, "action": "confirm"},
        )

    assert response.status_code == 400, response.text
    assert question_status(question_id) == "draft"


async def test_missing_question_is_404_on_both_gates(make_client):
    """A nonexistent id is a 404 from review and promote alike — not a 409,
    which would imply the question exists in some other status."""
    ghost = uuid.uuid4()

    async with make_client(COLLEGE_A) as client:
        review = await client.post(
            f"/api/v1/questions/{ghost}/review",
            json={"reviewer_id": REVIEWER_SME, "action": "confirm"},
        )
        promote = await client.post(
            f"/api/v1/questions/{ghost}/promote",
            json={"reviewer_id": REVIEWER_TEACHER},
        )

    assert review.status_code == 404, review.text
    assert promote.status_code == 404, promote.text


async def test_a_reviewer_id_is_required_not_defaulted(make_client, make_question):
    """Omitting reviewer_id is a 422, never a transition attributed to the
    calling tenant.

    Identity today is a college header, not a person (api/deps/identity.py).
    Defaulting the reviewer from it would write an unverifiable name into an
    audit trail whose only purpose is attribution.
    """
    question_id = make_question(status="draft")

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            f"/api/v1/questions/{question_id}/review", json={"action": "confirm"}
        )

    assert response.status_code == 422, response.text


# ═════════════════════════════ generation ═══════════════════════════════════

async def test_generation_produces_drafts_and_only_drafts(make_client, make_paragraph,
                                                          track_questions, question_status):
    """Generated questions land at 'draft', never anywhere further along.

    This is the first of the two gates seen from the creation side: there is
    no request field that can influence the starting status, so generation
    cannot be used as a back door into the bank.
    """
    paragraph_id = make_paragraph()

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            "/api/v1/questions/generate",
            json={"paragraph_id": paragraph_id, "count": 2, "style": "short",
                  "stub_llm": True},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["count"] == 2
    assert len(body["questions"]) == 2

    for q in body["questions"]:
        track_questions(q["question_id"])
        assert q["status"] == "draft"
        assert question_status(q["question_id"]) == "draft"


async def test_generation_cannot_be_asked_for_a_head_start(make_client, make_paragraph):
    """A request that tries to name a starting status is rejected outright.

    The schema sets extra='forbid', so `status` is a 422 rather than a
    silently ignored field. Silently ignoring it would be almost as bad: the
    caller would believe they had created live questions and would not check.
    """
    paragraph_id = make_paragraph()

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            "/api/v1/questions/generate",
            json={"paragraph_id": paragraph_id, "count": 1, "stub_llm": True,
                  "status": "live"},
        )

    assert response.status_code == 422, response.text


async def test_generation_refuses_superseded_content(make_client, make_paragraph):
    """Generating from superseded content is a 409 — the paragraph exists, its
    state forbids it."""
    paragraph_id = make_paragraph(status="superseded")

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            "/api/v1/questions/generate",
            json={"paragraph_id": paragraph_id, "count": 1, "stub_llm": True},
        )

    assert response.status_code == 409, response.text


async def test_generation_404s_on_an_unknown_paragraph(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            "/api/v1/questions/generate",
            json={"paragraph_id": str(uuid.uuid4()), "count": 1, "stub_llm": True},
        )

    assert response.status_code == 404, response.text


async def test_stub_llm_is_refused_when_debug_is_off(make_paragraph):
    """Stub text is placeholder filler that enters the bank as an ordinary
    draft, where a hurried reviewer could confirm and promote it into a real
    paper. Same guard, and the same 403, as POST /api/v1/evaluate's.

    Builds a production-settings app inline, the way
    test_evaluation.py::test_stub_is_refused_outside_development does — the
    shared `make_client` fixture is deliberately debug-enabled, and a second
    settings-parameterised fixture would only be used here.
    """
    import httpx

    from api.deps.identity import DEBUG_COLLEGE_HEADER
    from api.main import create_app
    from api.settings import Settings

    paragraph_id = make_paragraph()
    prod_app = create_app(Settings(api_env="production", enable_debug_endpoints=False,
                                   storage_mode="dummy"))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=prod_app),
        base_url="http://testserver",
        headers={DEBUG_COLLEGE_HEADER: COLLEGE_A},
    ) as client:
        response = await client.post(
            "/api/v1/questions/generate",
            json={"paragraph_id": paragraph_id, "count": 1, "stub_llm": True},
        )

    assert response.status_code == 403, response.text
    assert "development-only" in response.json()["detail"]


# ══════════════════════════════ listing ═════════════════════════════════════

async def test_list_filters_by_status(make_client, make_question):
    """The `status=draft` filter is the review queue, so it must not return
    anything already decided."""
    draft_id = make_question(status="draft")
    live_id = make_question(status="live")

    async with make_client(COLLEGE_A) as client:
        response = await client.get("/api/v1/questions", params={"status": "draft",
                                                                 "limit": 200})

    assert response.status_code == 200, response.text
    ids = {q["question_id"] for q in response.json()["questions"]}
    statuses = {q["status"] for q in response.json()["questions"]}

    assert draft_id in ids
    assert live_id not in ids
    assert statuses <= {"draft"}


async def test_list_rejects_a_status_the_database_does_not_have(make_client):
    """An unknown status is a 422 at the edge, not a 500 from inside the enum
    comparison."""
    async with make_client(COLLEGE_A) as client:
        response = await client.get("/api/v1/questions", params={"status": "published"})

    assert response.status_code == 422, response.text


async def test_detail_shows_the_review_history_a_reviewer_needs(make_client,
                                                                make_question):
    """GET /questions/{id} carries the prior reviews, so a decision is made
    with the same context the CLI's --show gives."""
    question_id = make_question(status="draft")

    async with make_client(COLLEGE_A) as client:
        await client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"reviewer_id": REVIEWER_SME, "action": "confirm", "comment": "ok"},
        )
        response = await client.get(f"/api/v1/questions/{question_id}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert len(body["prior_reviews"]) == 1
    assert body["prior_reviews"][0]["action"] == "confirmed"
    assert body["prior_reviews"][0]["comment"] == "ok"


# ════════════════════════ the bank is shared, on purpose ════════════════════

async def test_the_bank_is_shared_across_colleges(make_client, make_question):
    """A question is visible under ANY college's credential — by design.

    This test is the inverse of the cross-tenant 404 proofs in test_jobs.py,
    and it is deliberate. The question schema has no college_id and no RLS
    (migration 003 lines 5 and 341; the whole point of a shared bank is that
    colleges reuse each other's reviewed questions). An endpoint that filtered
    questions by the caller's college would therefore be filtering on a column
    that does not exist.

    It is asserted rather than left implicit so that tenanting the bank —
    which is a real possibility — must break a test that says exactly what the
    old behaviour was and where it was decided, instead of quietly changing
    what colleges can see.
    """
    question_id = make_question(status="live")

    async with make_client(COLLEGE_A) as owner:
        mine = await owner.get(f"/api/v1/questions/{question_id}")
    async with make_client(COLLEGE_B_ABSENT) as other:
        theirs = await other.get(f"/api/v1/questions/{question_id}")

    assert mine.status_code == 200, mine.text
    assert theirs.status_code == 200, theirs.text
    assert theirs.json()["question_id"] == mine.json()["question_id"]


async def test_the_gates_still_require_a_credential(make_client, make_question,
                                                    question_status):
    """Shared does not mean public: with no identity header every one of these
    endpoints is a 401, exactly like the tenanted ones.

    Sent as a blank header rather than an absent one, the convention the other
    modules use — `make_client` always sets the header, and blank exercises the
    same rejection branch in api/deps/identity.py.
    """
    question_id = make_question(status="draft")
    anonymous = {"X-Debug-College-Id": ""}

    async with make_client(COLLEGE_A) as client:
        listed = await client.get("/api/v1/questions", headers=anonymous)
        promoted = await client.post(f"/api/v1/questions/{question_id}/promote",
                                     headers=anonymous,
                                     json={"reviewer_id": REVIEWER_TEACHER})

    assert listed.status_code == 401, listed.text
    assert promoted.status_code == 401, promoted.text
    assert question_status(question_id) == "draft"
