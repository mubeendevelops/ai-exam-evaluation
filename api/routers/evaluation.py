"""api/routers/evaluation.py — evaluate, results, override.

Three endpoints, one rule each that matters more than the rest:

  POST /api/v1/evaluate                 -> 202, never a synchronous score.
  GET  /api/v1/results/{answer_id}      -> 404 for another college, never a leak.
  POST /api/v1/results/{id}/override    -> answer_reviews ONLY, never the ledger.

THE LEDGER RULE, since this is the file most likely to be edited by someone in
a hurry: evaluation_results is APPEND-ONLY (PROJECT_CONTEXT.md rule 2). There
is no code path from this router to an UPDATE against it, and there must never
be. A teacher's override is a new row in answer_reviews; the two are
reconciled at READ time by api/services/evaluation.py::_final_marks. If a
requirement ever seems to need "just updating the score", what it actually
needs is a new ledger row (a re-score, which record_evaluation already handles
by flipping is_current) or a review row — never a mutation.

All three take get_tenant_conn(), so the tenant comes from the authenticated
user and never from the path or body. The path carries an answer_id, and RLS
would happily scope a request to whatever college that answer belongs to if
the API asked it to — so the API doesn't ask.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status

import api.services.evaluation as evaluation_service
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, get_current_user
from api.schemas.evaluation import (
    EvaluateRequest,
    EvaluateResponse,
    OverrideRequest,
    OverrideResponse,
    ResultResponse,
)
from api.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.post(
    "/evaluate",
    response_model=EvaluateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a booklet for evaluation",
    responses={404: {"description": "No such upload for this college."}},
)
def evaluate(
    body: EvaluateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> EvaluateResponse:
    """Queues one booklet_eval job and returns 202 with the job id.

    Nothing is scored inside this request. Booklet evaluation runs OCR, layout
    detection and LLM calls over every region and takes MINUTES (§7D) — past
    any sane HTTP timeout, and it would pin a worker thread and a DB
    connection for the duration.
    """
    settings = _settings(request)

    # A stub score is a FABRICATED score, and persist_question_results appends
    # it to the append-only ledger indistinguishably from a real one. Letting
    # a client ask for that in production would be a way to write fake marks
    # into a student's record, so it is refused outside development.
    if (body.stub or body.stub_llm) and not settings.debug_endpoints_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "stub/stub_llm are development-only. A stubbed score is "
                "fabricated and would be appended to the evaluation_results "
                "ledger like any real one. Enable debug endpoints "
                "(API_ENV=development) to use them."
            ),
        )

    try:
        with conn.cursor() as cur:
            job = evaluation_service.enqueue_booklet_evaluation(
                cur,
                college_id=user.college_id,
                upload_id=body.upload_id,
                exam_id=body.exam_id,
                student_id=body.student_id,
                stub=body.stub,
                stub_llm=body.stub_llm,
                method=body.method,
            )
    except evaluation_service.UploadNotFoundError:
        # Same 404 as a nonexistent upload — see api/routers/jobs.py on why
        # this is not a 403.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No upload {body.upload_id} for this college.",
        )

    return EvaluateResponse(
        job_id=job["job_id"],
        status=job["status"],
        upload_id=body.upload_id,
        exam_id=body.exam_id,
        student_id=body.student_id,
        created_at=job["created_at"],
    )


@router.get(
    "/results/{answer_id}",
    response_model=ResultResponse,
    summary="Full evaluation report for one answer",
    responses={404: {"description": "No such answer for this college."}},
)
def get_result(
    answer_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> ResultResponse:
    """The current score, its per-signal breakdown, per-region components,
    confidence, the whole append-only ledger history, and every review.

    An answer belonging to another college is 404 — identical to a nonexistent
    id, so the endpoint cannot be used to discover which answer ids are real
    elsewhere on the platform.
    """
    try:
        with conn.cursor() as cur:
            report = evaluation_service.get_answer_report(
                cur, answer_id=answer_id, college_id=user.college_id
            )
    except evaluation_service.AnswerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No answer {answer_id} for this college.",
        )

    return ResultResponse(**report)


@router.post(
    "/results/{answer_id}/override",
    response_model=OverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record a teacher's override of an AI score",
    responses={404: {"description": "No such answer for this college."}},
)
def override_result(
    answer_id: uuid.UUID,
    body: OverrideRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> OverrideResponse:
    """Appends an answer_reviews row and moves the answer to 'sme_reviewed'.

    **Writes NOTHING to evaluation_results.** The ledger records what the AI
    computed; the model really did output that number, and overwriting it
    would erase the evidence needed to tell whether the model is
    systematically wrong. The override is a new fact in answer_reviews, and
    GET /results/{answer_id} reconciles the two into `final_marks` at read
    time. tests/test_api/test_evaluation.py asserts the evaluation_results row
    count and every row's contents are byte-identical before and after this
    call.
    """
    _validate_marks(body)

    try:
        with conn.cursor() as cur:
            review = evaluation_service.record_override(
                cur,
                answer_id=answer_id,
                college_id=user.college_id,
                reviewer_id=body.reviewer_id,
                action=body.action,
                final_marks=body.final_marks,
                comment=body.comment,
            )
    except evaluation_service.AnswerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No answer {answer_id} for this college.",
        )
    # No commit here — get_tenant_conn commits on a clean response, so the
    # review row and the status transition land together or not at all.

    return OverrideResponse(**review)


def _validate_marks(body: OverrideRequest) -> None:
    """Keeps the action and the marks consistent.

    The schema permits final_marks to be null so that 'flagged' can exist (the
    column is nullable for exactly that case, per migration 001's comment), so
    the combination has to be checked here rather than by a type.
    """
    if body.action == "overridden" and body.final_marks is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "action='overridden' requires final_marks — an override that "
                "changes nothing is a 'confirmed', and one that has not decided "
                "on a mark is a 'flagged'."
            ),
        )
    if body.action == "flagged" and body.final_marks is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "action='flagged' must not carry final_marks: flagging escalates "
                "an answer without deciding its mark. Use 'overridden' to set one."
            ),
        )
