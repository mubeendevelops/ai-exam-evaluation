"""tests/test_api/test_papers.py — POST /api/v1/papers/generate.

WHAT THIS FILE IS REALLY TESTING is the far end of the two gates in
test_questions.py. Those tests prove a question cannot reach 'live' without
two human decisions; these prove that 'live' is the only thing that can reach
a student's exam paper. Together they close the loop: unreviewed text cannot
be printed on a paper, because the only door into a paper checks for a status
only review can produce.

`test_generation_assigns_only_live_questions` is the one that pins it, and it
does so by making a draft and a confirmed question that MATCH THE SLOT
PERFECTLY and asserting the generator took the live one instead — a test that
simply generated a paper and found live questions in it would pass even if
the status filter were deleted, since most of the bank is live anyway.

ON TENANCY: papers, patterns and the question bank are all shared across
colleges (migrations 006 line 15, 008 line 15, 003 line 5 — none of these
tables has a college_id, none is under RLS). So there is no cross-tenant 404
to assert here, and `test_generation_draws_from_the_shared_bank` says that
explicitly rather than leaving its absence to be read as an oversight. See
test_questions.py's header for the full reasoning; the real isolation proofs
live in test_jobs.py and test_evaluation.py, over the answer schema.
"""
from __future__ import annotations

import uuid

import pytest

from api.deps.identity import create_access_token
from tests.test_api.conftest import COLLEGE_A, COLLEGE_B

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

GENERATE = "/api/v1/papers/generate"

#: Slot marks nothing else in the bank uses. With marks_tolerance=0 this means
#: the only questions that can possibly fill these slots are the ones a test
#: created, so "the generator chose X" is an assertion about the generator and
#: not about whatever else happens to be seeded.
ODD_MARKS_LONG = 9.25
ODD_MARKS_SHORT = 2.75


# ═══════════════════════════════ GET /papers (list) ═════════════════════════

async def test_papers_list_is_shared_across_colleges(make_client, make_pattern,
                                                      make_question, college_b):
    """A generated paper is visible under ANY college's credential — the list
    endpoint over the same shared, non-tenanted table
    test_generation_draws_from_the_shared_bank exercises for POST
    /papers/generate. get_tenant_conn authenticates the caller; it does not
    scope this query (migration 008 line 15 — no college_id, no RLS)."""
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)

    async with make_client(COLLEGE_A) as client:
        created = await client.post(GENERATE, json={
            "pattern_id": pattern_id, "name": "Shared list paper",
            "marks_tolerance": 0.0,
        })
    assert created.status_code == 201, created.text
    paper_id = created.json()["paper_id"]

    async with make_client(COLLEGE_B) as client:
        theirs = await client.get("/api/v1/papers", params={"pattern_id": pattern_id})

    assert theirs.status_code == 200, theirs.text
    assert paper_id in {p["paper_id"] for p in theirs.json()["items"]}


async def test_papers_list_filters_by_status_and_pattern(make_client, make_pattern,
                                                          make_question):
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))
    other_pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_SHORT, "short"),))
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)
    make_question(status="live", style="short", marks_max=ODD_MARKS_SHORT)

    async with make_client(COLLEGE_A) as client:
        mine = await client.post(GENERATE, json={
            "pattern_id": pattern_id, "name": "Pattern-filtered paper",
            "marks_tolerance": 0.0,
        })
        await client.post(GENERATE, json={
            "pattern_id": other_pattern_id, "name": "A different pattern's paper",
            "marks_tolerance": 0.0,
        })

        for_pattern = await client.get("/api/v1/papers", params={"pattern_id": pattern_id})
        finalized = await client.get("/api/v1/papers", params={"status": "finalized"})

    assert for_pattern.status_code == 200, for_pattern.text
    ids = {p["paper_id"] for p in for_pattern.json()["items"]}
    assert mine.json()["paper_id"] in ids
    assert all(p["pattern_id"] == pattern_id for p in for_pattern.json()["items"])
    # Generation always leaves a paper 'draft' — see PaperGenerateResponse.
    assert mine.json()["paper_id"] not in {p["paper_id"] for p in finalized.json()["items"]}


async def test_papers_list_limit_above_max_is_clamped_not_rejected(make_client, make_pattern,
                                                                    make_question):
    """A limit far above MAX_LIMIT (200) is CLAMPED, not answered with 422 —
    see api/deps/pagination.py."""
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)

    async with make_client(COLLEGE_A) as client:
        await client.post(GENERATE, json={
            "pattern_id": pattern_id, "name": "Clamp test paper",
            "marks_tolerance": 0.0,
        })
        response = await client.get("/api/v1/papers", params={"limit": 100_000})

    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 200


async def test_generation_assigns_only_live_questions(make_client, make_pattern,
                                                      make_question, admin_conn):
    """THE PROOF THAT REVIEW CANNOT BE BYPASSED VIA PAPER GENERATION.

    Three questions match the slot exactly on style and marks. One is a draft,
    one is confirmed, one is live. Only the live one may be assigned — the
    other two are, respectively, a question nobody has reviewed and a question
    reviewed but not yet published, and putting either in front of a student
    is the failure the two-gate flow exists to prevent.
    """
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))

    draft_id = make_question(status="draft", style="long", marks_max=ODD_MARKS_LONG)
    confirmed_id = make_question(status="confirmed", style="long", marks_max=ODD_MARKS_LONG)
    live_id = make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)

    async with make_client(COLLEGE_A) as client:
        response = await client.post(GENERATE, json={
            "pattern_id": pattern_id,
            "name": "Only-live proof paper",
            "marks_tolerance": 0.0,
        })

    assert response.status_code == 201, response.text
    body = response.json()

    assigned = [leaf["question_id"]
                for section in body["sections"]
                for slot in section["slots"]
                for leaf in slot["leaves"] if leaf["assigned"]]

    assert assigned == [live_id]
    assert draft_id not in assigned
    assert confirmed_id not in assigned

    # And the persisted rows agree with the response — every question actually
    # written into paper_questions is live.
    with admin_conn.cursor() as cur:
        cur.execute("""
            SELECT q.status
            FROM   paper_questions pq
            JOIN   paper_sections  ps ON ps.paper_section_id = pq.paper_section_id
            JOIN   questions       q  ON q.question_id = pq.question_id
            WHERE  ps.paper_id = %s
        """, (body["paper_id"],))
        statuses = [r[0] for r in cur.fetchall()]

    assert statuses == ["live"]


async def test_a_paper_persists_its_full_structure(make_client, make_pattern,
                                                   make_question, admin_conn):
    """A generated paper is a real row set, not just a response body.

    Checks generated_papers -> paper_sections -> paper_questions all landed,
    because the endpoint returns 201 from an in-request transaction and a
    commit that silently did not happen would look identical to the caller.
    """
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),
                                     ("Q2", ODD_MARKS_SHORT, "short")))
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)
    make_question(status="live", style="short", marks_max=ODD_MARKS_SHORT)

    async with make_client(COLLEGE_A) as client:
        author = client.identity["reviewer_id"]
        response = await client.post(GENERATE, json={
            "pattern_id": pattern_id,
            "name": "Structure paper",
            "marks_tolerance": 0.0,
        })

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "draft", "generation fills a paper; it does not publish one"
    assert body["total_filled"] == 2
    assert body["total_slots"] == 2
    assert body["warnings"] == []

    with admin_conn.cursor() as cur:
        cur.execute("SELECT name, status, generated_by FROM generated_papers "
                    "WHERE paper_id = %s", (body["paper_id"],))
        paper = cur.fetchone()
        cur.execute("""
            SELECT COUNT(*)
            FROM   paper_questions pq
            JOIN   paper_sections  ps ON ps.paper_section_id = pq.paper_section_id
            WHERE  ps.paper_id = %s
        """, (body["paper_id"],))
        (assigned_count,) = cur.fetchone()

    assert paper[0] == "Structure paper"
    assert paper[1] == "draft"
    # RE-4: generated_by is no longer a request field. Every paper made over
    # HTTP is attributed to the account that asked for it — the NULL branch
    # ("system-generated", migration 008) stays reachable only from the CLI.
    assert paper[2] == author, "the paper must be attributed to its caller"
    assert assigned_count == 2


async def test_an_unfillable_mandatory_slot_persists_nothing(make_client, make_pattern,
                                                             make_question, admin_conn):
    """A mandatory slot with no live match is a 409 AND leaves no paper behind.

    core/paper_generator.py inserts the paper row before it discovers the gap,
    so the rollback is doing real work here. A half-written paper that survived
    the failure would be a paper with missing mandatory questions sitting in
    the table looking generated.
    """
    # A live question exists, but at the WRONG marks — with tolerance 0 nothing
    # can fill the slot.
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),),
                              is_mandatory=True)
    make_question(status="live", style="long", marks_max=ODD_MARKS_SHORT)

    async with make_client(COLLEGE_A) as client:
        response = await client.post(GENERATE, json={
            "pattern_id": pattern_id,
            "name": "Should not exist",
            "marks_tolerance": 0.0,
        })

    assert response.status_code == 409, response.text
    assert "Q1" in response.json()["detail"], "the error must name the unfilled slot"

    with admin_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM generated_papers WHERE pattern_id = %s",
                    (pattern_id,))
        (papers,) = cur.fetchone()

    assert papers == 0, "a refused generation must not leave a paper row behind"


async def test_an_unfillable_optional_slot_is_a_warning_not_a_failure(make_client,
                                                                      make_pattern,
                                                                      make_question):
    """An optional section's gap yields a valid, degraded paper plus a warning
    naming the slot — the caller is told what is missing rather than handed a
    quietly shorter paper."""
    pattern_id = make_pattern(
        slots=(("Q1", ODD_MARKS_LONG, "long"), ("Q2", ODD_MARKS_SHORT, "short")),
        is_mandatory=False,
    )
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)
    # Nothing for Q2.

    async with make_client(COLLEGE_A) as client:
        response = await client.post(GENERATE, json={
            "pattern_id": pattern_id,
            "name": "Degraded paper",
            "marks_tolerance": 0.0,
        })

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["total_filled"] == 1
    assert body["total_slots"] == 2
    # Structured warnings (slot_label, section, marks, reason), not prose —
    # Hardening pass 2026-09-06. is_complete/filled_marks are the same fact
    # surfaced WITHOUT reading warnings at all.
    assert body["is_complete"] is False
    assert body["filled_marks"] == ODD_MARKS_LONG
    assert body["pattern_total_marks"] == ODD_MARKS_LONG + ODD_MARKS_SHORT
    assert any(w["slot_label"] == "Q2" for w in body["warnings"]), body["warnings"]
    q2_warning = next(w for w in body["warnings"] if w["slot_label"] == "Q2")
    assert q2_warning["marks"] == ODD_MARKS_SHORT
    assert q2_warning["section"]
    assert q2_warning["reason"]


async def test_generation_404s_on_an_unknown_pattern(make_client):
    async with make_client(COLLEGE_A) as client:
        response = await client.post(GENERATE, json={
            "pattern_id": str(uuid.uuid4()), "name": "ghost pattern",
        })

    assert response.status_code == 404, response.text


async def test_a_retired_pattern_cannot_produce_a_paper(make_client, make_pattern,
                                                        make_question):
    """A retired pattern is a 409: it exists, but generating from a template
    someone deliberately withdrew would put a superseded exam format in front
    of students."""
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),), is_active=False)
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)

    async with make_client(COLLEGE_A) as client:
        response = await client.post(GENERATE, json={
            "pattern_id": pattern_id, "name": "From a retired pattern",
        })

    assert response.status_code == 409, response.text
    assert "retired" in response.json()["detail"].lower()


async def test_a_token_naming_an_unknown_reviewer_cannot_generate_a_paper(
    make_client, make_pattern, make_question, admin_conn, api_settings
):
    """A paper's author must be a real reviewer — a 400, and no paper written.

    Ownership of a paper is the record of who is accountable for it; accepting
    an unknown id would produce a paper attributed to nobody while looking
    attributed.

    RE-4 changed how this scenario is reached, not whether it matters: the
    author used to be `generated_by` in the request body, so an unknown one
    was a client sending a bad uuid. It is now the caller's own `rid` claim,
    so the only way here is a token whose reviewer no longer exists.
    """
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))
    make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)

    token, _ = create_access_token(
        user_id=uuid.uuid4(), reviewer_id=uuid.uuid4(),
        email="ghost@example.edu", role="teacher", college_id=COLLEGE_A,
        settings=api_settings)

    async with make_client(COLLEGE_A) as client:
        response = await client.post(
            GENERATE,
            json={"pattern_id": pattern_id, "name": "Ghost author"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 400, response.text

    with admin_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM generated_papers WHERE pattern_id = %s",
                    (pattern_id,))
        (papers,) = cur.fetchone()
    assert papers == 0


async def test_generation_requires_a_credential(make_client, make_pattern):
    """A blank Authorization header is a 401 before any generation happens."""
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))

    async with make_client(COLLEGE_A) as client:
        response = await client.post(GENERATE,
                                     headers={"Authorization": ""},
                                     json={"pattern_id": pattern_id, "name": "no auth"})

    assert response.status_code == 401, response.text


async def test_generation_draws_from_the_shared_bank(make_client, make_pattern,
                                                     make_question, college_b):
    """College B can generate a paper from a pattern and questions created
    without reference to any college — because patterns, papers and the
    question bank have no college_id at all.

    This is the documented design (migrations 003 line 5, 006 line 15, 008
    line 15), asserted rather than assumed so that tenanting any of it has to
    break a test that names where the decision was made. It is NOT a claim
    that tenant isolation is working — there is nothing here to isolate. The
    isolation proofs are in test_jobs.py and test_evaluation.py.

    College B is seed_minimal.sql's real second college. It used to be an id
    that existed nowhere, which get_tenant_conn now refuses with 401 — so this
    test would have failed on authentication rather than telling us anything
    about the bank.
    """
    pattern_id = make_pattern(slots=(("Q1", ODD_MARKS_LONG, "long"),))
    live_id = make_question(status="live", style="long", marks_max=ODD_MARKS_LONG)

    async with make_client(COLLEGE_B) as client:
        response = await client.post(GENERATE, json={
            "pattern_id": pattern_id,
            "name": "Paper for college B",
            "marks_tolerance": 0.0,
        })

    assert response.status_code == 201, response.text
    assigned = [leaf["question_id"]
                for section in response.json()["sections"]
                for slot in section["slots"]
                for leaf in slot["leaves"] if leaf["assigned"]]
    assert assigned == [live_id]
