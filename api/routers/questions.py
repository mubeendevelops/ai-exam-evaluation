"""api/routers/questions.py — the question bank and its mandatory review gates.

╔══════════════════════════════════════════════════════════════════════════╗
║ HUMAN REVIEW IS MANDATORY. THIS FILE MUST NEVER GROW A PATH AROUND IT.   ║
╚══════════════════════════════════════════════════════════════════════════╝

A question becomes answerable by students only at status='live' (the
trg_answers_question_must_be_live trigger enforces that in the database), and
'live' is reachable only by walking two gates in order, each requiring a named
reviewer:

    generate ──> draft ──review(confirm)──> confirmed ──promote──> live
                   │
                   └────review(reject)───> rejected

Both transitions are executed by scripts/review_question.py — the same
functions the CLI calls, not a copy — so the API and the terminal cannot drift
into disagreeing about what the gates are. The endpoints here are HTTP shape
around those calls.

WHAT WOULD BREAK THE INVARIANT, so that a future reader recognises it as a
change of policy rather than a convenience:

  * a `status` field on the generate request, or any default other than draft;
  * a combined confirm+promote endpoint, or a `promote=true` flag on review;
  * relaxing promote's required status from 'confirmed' to "anything but
    rejected", which is the natural-looking edit that silently deletes the
    quality gate for drafts;
  * catching GateViolationError and retrying, or answering it as anything
    other than a refusal.

The test that proves this holds is
tests/test_api/test_questions.py::test_promote_cannot_skip_the_review_gate —
it POSTs promote on a draft and asserts both the 409 AND that the row is still
'draft' afterwards. The second assertion is the load-bearing one: a 409 with a
promoted row would be the worst possible outcome and would still pass a
status-code-only test.

TENANCY: `get_tenant_conn` authenticates the caller and keeps this router on
the same connection discipline as every other endpoint (api/deps/db.py). It
does NOT scope these reads or writes to a college, because it cannot: the
question bank is shared platform-wide by design — migration 003 line 5
("Question schema (12 tables) stays UNCHANGED — shared question bank, no
tenant column, readable/usable by every college") and line 341, which names
questions, question_reviews and question_status_history as excluded from RLS.
So a question created by one college IS visible to another, on purpose, and
tests/test_api/test_questions.py pins that so the day it changes, it changes
deliberately and with a migration. Do not add a college_id filter here to
make it look isolated; that would be isolation theatre over a shared table.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

import api.services.questions as questions_service
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, get_current_user
from api.deps.pagination import Pagination, get_pagination
from api.schemas.questions import (
    GenerateQuestionsRequest,
    GenerateQuestionsResponse,
    GeneratedQuestion,
        QuestionDetail,
    QuestionListResponse,
    QuestionSourceType,
    QuestionStatus,
    QuestionStyle,
    ReviewRequest,
    TransitionResponse,
    question_to_detail,
    question_to_summary,
)
from api.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


# ──────────────────────────────── reads ─────────────────────────────────────

@router.get(
    "",
    response_model=QuestionListResponse,
    summary="List and filter the question bank",
)
def list_questions(
    status_filter: QuestionStatus | None = Query(
        default=None, alias="status",
        description="Filter by lifecycle status. `draft` is the review queue.",
    ),
    style: QuestionStyle | None = Query(default=None),
    source_type: QuestionSourceType | None = Query(default=None),
    is_ai_generated: bool | None = Query(
        default=None, description="true = LLM-generated, false = human-authored."
    ),
    paper_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Only questions assigned to this generated paper. This is the "
            "closest available filter to 'questions for exam X': no question "
            "links to an exam in this schema, only to a paper, via "
            "paper_questions -> paper_sections -> generated_papers."
        ),
    ),
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> QuestionListResponse:
    """A filtered page of the shared bank, newest first.

    `status` is aliased from a parameter named `status_filter` because
    `status` is also the FastAPI status module imported in this file; the
    alias keeps the wire name right without shadowing it.

    limit/offset now come from the shared api/deps/pagination.py dependency,
    the same one every list endpoint added in this pass uses — this endpoint
    used to declare its own `Query(le=200)`, which REJECTED a limit above
    MAX_LIMIT with 422. The shared dependency CLAMPS instead (see its
    docstring); this is the one behavioural change from bringing /questions
    in line with the rest.
    """
    with conn.cursor() as cur:
        rows, total = questions_service.list_bank(
            cur, status=status_filter, style=style, source_type=source_type,
            is_ai_generated=is_ai_generated, paper_id=paper_id,
            limit=pagination.limit, offset=pagination.offset,
        )

    return QuestionListResponse(
        questions=[question_to_summary(r) for r in rows],
        total=total,
        limit=pagination.limit,
        offset=pagination.offset,
    )


@router.get(
    "/{question_id}",
    response_model=QuestionDetail,
    summary="One question, with its source paragraph and review history",
    responses={404: {"description": "No such question."}},
)
def get_question(
    question_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> QuestionDetail:
    """Everything a reviewer needs before deciding: the question, the content
    it was generated from, and every prior review — the same payload
    `review_question.py --show` prints."""
    try:
        with conn.cursor() as cur:
            detail = questions_service.get_one(cur, question_id=question_id)
    except questions_service.QuestionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return question_to_detail(detail)


# ───────────────────────────── generation ───────────────────────────────────

@router.post(
    "/generate",
    response_model=GenerateQuestionsResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate draft questions from a paragraph",
    responses={
        403: {"description": "stub_llm requested outside development."},
        404: {"description": "No such paragraph."},
        409: {"description": "The paragraph is superseded."},
    },
)
def generate_questions(
    body: GenerateQuestionsRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> GenerateQuestionsResponse:
    """Creates `count` questions at status='draft' and returns them.

    201, not 202: unlike booklet evaluation this runs inline. Generating a
    handful of questions is one LLM call, and the caller needs the ids back to
    put them in front of a reviewer — a job id would make the very next step
    (review) a polling loop for no benefit.

    Every question returned is a DRAFT. None of them is usable in a paper
    until it has been through both gates below.
    """
    settings = _settings(request)

    # Stub text is not a question — it is placeholder filler that would sit in
    # the bank looking like a real draft, and a hurried reviewer could confirm
    # and promote it into a student's exam paper. Same reasoning, and the same
    # 403, as the stub guard on POST /api/v1/evaluate.
    if body.stub_llm and not settings.debug_endpoints_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "stub_llm is development-only. It writes placeholder text into "
                "the shared question bank as an ordinary draft, where it is "
                "indistinguishable from a real generated question at review "
                "time. Enable debug endpoints (API_ENV=development) to use it."
            ),
        )

    try:
        with conn.cursor() as cur:
            created = questions_service.generate(
                cur,
                paragraph_id=body.paragraph_id,
                count=body.count,
                style=body.style,
                intent_hint=body.intent_hint,
                stub_llm=body.stub_llm,
            )
    except questions_service.ParagraphNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except questions_service.ParagraphNotUsableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return GenerateQuestionsResponse(
        paragraph_id=body.paragraph_id,
        count=len(created),
        questions=[GeneratedQuestion(**q) for q in created],
    )


# ──────────────────────────── gate 1: review ────────────────────────────────

@router.post(
    "/{question_id}/review",
    response_model=TransitionResponse,
    summary="QUALITY GATE — confirm or reject a draft question",
    responses={
        400: {"description": "No such reviewer."},
        404: {"description": "No such question."},
        409: {"description": "The question is not a draft — it was already reviewed."},
    },
)
def review_question(
    question_id: uuid.UUID,
    body: ReviewRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> TransitionResponse:
    """draft -> confirmed | rejected. Fires only from 'draft'.

    Re-reviewing an already-decided question is a 409, not an overwrite. That
    is the CLI's behaviour too, and it is deliberate: changing a decision that
    is already recorded in question_reviews is a re-open operation with its
    own audit meaning, which nothing in this codebase implements yet. Silently
    allowing it here would let a second reviewer erase the first one's
    judgement with no trace that it happened.

    Writes two rows: question_reviews (who decided what, and why) and
    question_status_history (the transition). Both come from
    review_question.py; neither is written here.
    """
    try:
        with conn.cursor() as cur:
            result = questions_service.review(
                cur,
                question_id=question_id,
                # RE-4: attributed to the authenticated caller, never to a
                # reviewer named in the body. See ReviewRequest's docstring.
                reviewer_id=user.reviewer_id,
                action=body.action,
                comment=body.comment,
            )
    except questions_service.QuestionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except questions_service.ReviewerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except questions_service.GateViolationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return TransitionResponse(**result)


# ─────────────────────────── gate 2: promote ────────────────────────────────

@router.post(
    "/{question_id}/promote",
    response_model=TransitionResponse,
    summary="PUBLISH GATE — make a confirmed question live",
    responses={
        400: {"description": "No such reviewer."},
        404: {"description": "No such question."},
        409: {
            "description": (
                "The question is not 'confirmed'. A draft returns this — it "
                "must be reviewed first; promotion cannot substitute for "
                "review."
            )
        },
    },
)
def promote_question(
    question_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> TransitionResponse:
    """confirmed -> live. Fires only from 'confirmed'.

    THIS IS THE ENDPOINT THAT MUST NOT BE MADE LENIENT. Posting it against a
    draft is a 409 and the question stays a draft: the request is refused, not
    queued, not upgraded, not partially applied. `promote` is a separate
    operational decision from `review` — one says the content is correct, the
    other says students may now be asked it — and collapsing them would mean
    one person's click puts an unreviewed AI-generated question into an exam.

    TAKES NO REQUEST BODY. Its only field was `reviewer_id`, which RE-4
    deleted — the promoting reviewer is the authenticated caller.

    No question_reviews row is written; promotion is not a content judgement.
    The transition IS recorded in question_status_history, so the audit trail
    is complete either way.
    """
    try:
        with conn.cursor() as cur:
            result = questions_service.promote(
                # RE-4: the promoting reviewer is the authenticated caller.
                cur, question_id=question_id, reviewer_id=user.reviewer_id
            )
    except questions_service.QuestionNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except questions_service.ReviewerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except questions_service.GateViolationError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{exc} Promotion is the publish gate and does not substitute "
                f"for review: a question must be confirmed by a reviewer "
                f"before it can go live."
            ),
        )

    return TransitionResponse(**result)
