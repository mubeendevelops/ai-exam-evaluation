"""api/routers/papers.py — POST /api/v1/papers/generate: fill a pattern with
live questions.

Paper generation is where the two gates in api/routers/questions.py pay off.
core/paper_generator.py's matching queries both begin `WHERE status = 'live'`,
so the only questions that can reach a student's exam paper are the ones a
reviewer confirmed and someone then published. This endpoint adds no question
selection of its own — that is why it cannot accidentally admit a draft.

WHY GENERATION IS INLINE (201) AND NOT A JOB (202), unlike /api/v1/evaluate:
filling a pattern is a handful of indexed SELECTs and INSERTs against the
question bank. No OCR, no LLM, no minutes-long work. Queueing it would add a
polling round-trip to an operation that finishes inside the request.

TRANSACTIONALITY IS THE INTERESTING PART. generate_paper builds the paper as
it goes — generated_papers, then paper_sections, then a paper_questions row
per filled slot — and only at the very end checks whether any MANDATORY slot
went unfilled, raising if so. By then rows have been written. They are never
seen by anyone: they were written in this request's transaction, and
api/deps/db.py rolls that transaction back on any exception, including the
HTTPException raised below. So a 409 leaves nothing behind. Do not "improve"
this by committing early, catching the error, and cleaning up afterwards —
that swaps an atomic failure for a compensating delete that can itself fail.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

import api.services.papers as papers_service
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, get_current_user
from api.deps.pagination import Pagination, get_pagination
from api.schemas.pagination import Page
from api.schemas.papers import (
    PaperGenerateRequest,
    PaperGenerateResponse,
    PaperStatus,
    PaperSummary,
    paper_to_response,
    paper_to_summary,
)

router = APIRouter(prefix="/api/v1/papers", tags=["papers"])


@router.get(
    "",
    response_model=Page[PaperSummary],
    summary="List and filter the shared bank of generated papers",
)
def list_papers(
    status_filter: PaperStatus | None = Query(default=None, alias="status"),
    pattern_id: uuid.UUID | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[PaperSummary]:
    """generated_papers is SHARED across colleges, exactly like /questions —
    see api/services/papers.py's header. get_tenant_conn authenticates the
    caller; it does not scope this query."""
    with conn.cursor() as cur:
        rows, total = papers_service.list_papers(
            cur, status=status_filter, pattern_id=pattern_id,
            limit=pagination.limit, offset=pagination.offset,
        )

    return Page(
        items=[paper_to_summary(r) for r in rows],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )


@router.post(
    "/generate",
    response_model=PaperGenerateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate an exam paper from a pattern using live questions",
    responses={
        400: {"description": "The caller's reviewer_id names no reviewers "
                             "row. Only reachable if the account's reviewer "
                             "was deleted out from under its login, which "
                             "migration 017's ON DELETE RESTRICT prevents."},
        404: {"description": "No such paper pattern."},
        409: {
            "description": (
                "The pattern is retired or empty, or a mandatory slot had no "
                "matching live question. Nothing is persisted."
            )
        },
    },
)
def generate_paper(
    body: PaperGenerateRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> PaperGenerateResponse:
    """Assigns a live question to every leaf slot the bank can fill.

    The paper is attributed to the authenticated caller (`generated_by`), not
    to a reviewer named in the body — see PaperGenerateRequest (RE-4).

    A successful response can still carry `warnings`: an OPTIONAL slot with no
    match leaves a real hole in a valid paper, and the caller is told which
    one rather than being handed a quietly shorter paper. A MANDATORY slot
    with no match is a 409 — there is no valid paper to return, and the
    message names each slot so someone knows which questions must be written,
    reviewed and promoted before this pattern can be used.
    """
    try:
        with conn.cursor() as cur:
            result = papers_service.generate(
                cur,
                pattern_id=body.pattern_id,
                name=body.name,
                # RE-4: the paper is attributed to the authenticated caller.
                generated_by=user.reviewer_id,
                choose_count=body.choose_count,
                marks_tolerance=body.marks_tolerance,
            )
    except papers_service.PatternNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except papers_service.ReviewerNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except (papers_service.PatternNotUsableError,
            papers_service.UnfillableSlotsError) as exc:
        # Raised AFTER generate_paper wrote its rows — see the module
        # docstring. The dependency's rollback is what makes this a clean
        # failure; nothing here needs to undo anything.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return paper_to_response(result)
