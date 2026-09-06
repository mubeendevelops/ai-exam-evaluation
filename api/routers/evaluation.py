"""api/routers/evaluation.py — evaluate, results, page images, override.

Five endpoints, one rule each that matters more than the rest:

  POST /api/v1/evaluate                 -> 202, never a synchronous score.
  GET  /api/v1/results                  -> one row per answer, not the report.
  GET  /api/v1/results/{answer_id}      -> 404 for another college, never a leak.
  GET  /api/v1/results/{id}/pages/{n}/image
                                        -> a URL that EXPIRES, generated per
                                           request and stored nowhere.
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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

import api.services.evaluation as evaluation_service
import api.services.ingestion as ingestion_service
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, require_college_user
from api.deps.pagination import Pagination, get_pagination
from api.deps.quota import check_concurrent_job_cap, rate_limit
from api.schemas.evaluation import (
    AnswerStatus,
    EvaluateRequest,
    EvaluateResponse,
    OverrideRequest,
    OverrideResponse,
    PageImageResponse,
    ResultResponse,
    ResultSummary,
    result_to_summary,
)
from api.schemas.pagination import Page
from api.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


def _settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


@router.post(
    "/evaluate",
    response_model=EvaluateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a booklet for evaluation",
    responses={
        404: {"description": "No such upload, or no such paper."},
        429: {
            "description": (
                "Either this college's request rate for this endpoint, or "
                "its concurrent queued+running job backlog "
                "(MAX_QUEUED_JOBS_PER_COLLEGE), is at its configured cap. "
                "`Retry-After` says how long to wait — see api/deps/quota.py."
            )
        },
    },
    dependencies=[Depends(rate_limit("evaluate"))],
)
def evaluate(
    body: EvaluateRequest,
    request: Request,
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
) -> EvaluateResponse:
    """Queues the work this booklet needs and returns 202 with the job id.

    Nothing is scored inside this request. Booklet evaluation runs OCR, layout
    detection and LLM calls over every region and takes MINUTES (§7D) — past
    any sane HTTP timeout, and it would pin a worker thread and a DB
    connection for the duration.

    TWO SHAPES, and the response says which one happened:

      * booklet has no persisted regions -> one `booklet_ingest` job, which
        segments the PDF into answer_blocks and then chains into the
        `booklet_eval` job whose id appears in its `result`.
      * booklet is already ingested      -> one `booklet_eval` job directly,
        re-using the regions that exist.

    The second case is what makes re-scoring cheap AND safe: ingestion is not
    re-run, so a student's answer_blocks are written once and never rewritten
    by a re-score (§7C, and core/booklet_pipeline.py on why a second ingestion
    would duplicate rather than replace them). This endpoint used to require
    that someone had run scripts/ingest_booklet.py by hand first; it no longer
    does, and NoRegionsError is now a real failure rather than the normal path.
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
            # THE limit that actually protects the shared Groq daily quota —
            # see api/deps/quota.py's module docstring. Checked inside this
            # request's own transaction, against the count of THIS college's
            # own jobs, so it reflects exactly the backlog this request is
            # about to add one more job to.
            check_concurrent_job_cap(
                cur, college_id=user.college_id,
                cap=settings.max_queued_jobs_per_college,
            )
            queued = ingestion_service.enqueue_evaluation_pipeline(
                cur,
                college_id=user.college_id,
                upload_id=body.upload_id,
                exam_id=body.exam_id,
                student_id=body.student_id,
                paper_id=body.paper_id,
                stub=body.stub,
                stub_llm=body.stub_llm,
                method=body.method,
                # So the worker's log lines for this booklet's job(s) can be
                # tied back to the request that queued them — see
                # api/logging_config.py.
                request_id=getattr(request.state, "request_id", None),
            )
    except evaluation_service.UploadNotFoundError:
        # Same 404 as a nonexistent upload — see api/routers/jobs.py on why
        # this is not a 403.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No upload {body.upload_id} for this college.",
        )
    except ingestion_service.PaperNotFoundError as exc:
        # NOT a tenant leak: the paper tables carry no college_id and are not
        # under RLS (§11 — the bank is shared), so "no such paper" is the
        # whole truth here and there is no existence oracle to protect.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    job = queued["job"]
    return EvaluateResponse(
        job_id=job["job_id"],
        job_type=job["job_type"],
        ingest_job_id=queued["ingest_job_id"],
        status=job["status"],
        upload_id=body.upload_id,
        exam_id=body.exam_id,
        student_id=body.student_id,
        paper_id=body.paper_id,
        created_at=job["created_at"],
    )


@router.get(
    "/results",
    response_model=Page[ResultSummary],
    summary="List and filter this college's answer results",
)
def list_results(
    exam_id: uuid.UUID | None = Query(default=None),
    student_id: uuid.UUID | None = Query(default=None),
    status_filter: AnswerStatus | None = Query(
        default=None, alias="status", description="Filter by answer_status.",
    ),
    needs_review: bool | None = Query(
        default=None,
        description="true = at least one region flagged for review; false = "
                    "none are. Null does not filter.",
    ),
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[ResultSummary]:
    """One row per answer — score at a glance, not the full report.

    `GET /results/{answer_id}` is the full per-answer report (ledger history,
    every review, every region component); this is the review-queue /
    dashboard view over many answers at once. Tenant-scoped like every other
    answer-schema read — see api/services/evaluation.py::list_results and
    core/results.py for the query and why `needs_review` is computed rather
    than stored.
    """
    with conn.cursor() as cur:
        rows, total = evaluation_service.list_results(
            cur, college_id=user.college_id, exam_id=exam_id, student_id=student_id,
            status=status_filter, needs_review=needs_review,
            limit=pagination.limit, offset=pagination.offset,
        )

    return Page(
        items=[result_to_summary(r) for r in rows],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )


@router.get(
    "/results/{answer_id}",
    response_model=ResultResponse,
    summary="Full evaluation report for one answer",
    responses={404: {"description": "No such answer for this college."}},
)
def get_result(
    answer_id: uuid.UUID,
    user: CurrentUser = Depends(require_college_user),
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


@router.get(
    "/results/{answer_id}/pages/{page_number}/image",
    response_model=PageImageResponse,
    summary="A short-lived URL for one scanned page of this answer",
    responses={
        404: {"description": "No such answer for this college, no region on "
                             "that page, or no image stored for it — one "
                             "response for all four."},
        503: {"description": "The page image is a dummy-storage reference; "
                             "there is no object storage behind it."},
    },
)
def get_page_image(
    answer_id: uuid.UUID,
    page_number: int,
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
) -> PageImageResponse:
    """The scanned page a region was cut from, as a SHORT-LIVED PRESIGNED URL.

    This is the left half of the Results screen. `region_bbox` on each entry in
    GET /results/{answer_id}'s `regions` is in pixel coordinates of exactly
    this image (the DESKEWED page — migration 013), so an overlay drawn from
    one lands on the other with no transform.

    A URL RATHER THAN THE BYTES. A 200-DPI deskewed page is multi-megabyte;
    streaming it would pin an API worker and its database connection for the
    whole transfer, per page, per reviewer. The reasoning, and what that choice
    costs, is in api/services/evaluation.py::resolve_page_image. The URL
    expires in PAGE_IMAGE_URL_TTL_SECONDS (300s) and is generated per request:
    nothing writes it back, because the database stores stable "bucket/key"
    references and never presigned ones (CLAUDE_CONTEXT.md §10).

    THE PAGE IS REACHED THROUGH THE ANSWER — `answers` JOIN `answer_blocks`,
    with this college's id in the predicate. A page_image_url is never taken
    from a caller. Another college's answer is 404, byte-identical to a
    nonexistent answer id, to a page this booklet does not have, and to a page
    whose image was never stored: four causes, one response, no oracle.
    """
    try:
        with conn.cursor() as cur:
            image = evaluation_service.resolve_page_image(
                cur, answer_id=answer_id, page_number=page_number,
                college_id=user.college_id,
            )
    except evaluation_service.AnswerNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No page {page_number} image for answer {answer_id} in this college.",
        )
    except evaluation_service.PageImageUnavailableError as exc:
        # 503, not 404: the row is there and the caller is entitled to it —
        # this deployment simply has no storage behind the reference. Only
        # reachable after the tenant check has already passed on the caller's
        # OWN answer, so it is not an oracle over anyone else's ids.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc),
        )

    return PageImageResponse(answer_id=answer_id, **image)


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
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
) -> OverrideResponse:
    """Appends an answer_reviews row and moves the answer to 'sme_reviewed'.

    THE REVIEW IS ATTRIBUTED TO THE AUTHENTICATED CALLER — `reviewer_id` comes
    from the access token and is not a request field (RE-4). An audit trail
    whose subject the caller picks is not an audit trail.

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
                # RE-4: the reviewer is the authenticated caller, never a
                # field the caller chose. See OverrideRequest's docstring.
                reviewer_id=user.reviewer_id,
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
