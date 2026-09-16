"""api/routers/content.py — upload teacher content, browse it, and pick a
paragraph to generate questions from.

FINISHES QUESTION GENERATION'S MISSING HALF. POST /api/v1/questions/generate
has always taken a paragraph_id and refused to run without one, but nothing
in this codebase ever created a `paragraphs` row — see core/paragraphs.py's
module docstring for the full account, and
frontend/src/features/questions/GenerateQuestionsForm.tsx's own former
placeholder, which said as much: "There's no paragraph picker yet — this API
has no endpoint to list uploaded content, so paste the id." These six
endpoints are that endpoint.

SPLIT AND CREATE ARE TWO CALLS. Splitting is a suggestion; saving is a
commitment. `POST /content/split` opens no database connection at all and
writes nothing — it is a pure text transform (core/paragraphs.py's
`split_into_paragraphs`/`split_into_sentences`), so a teacher can see how
their pasted text would be cut into paragraphs before any of it is
persisted. `POST /content` takes the (possibly hand-edited) final list and
is the only endpoint here that writes. There is no server-side draft
connecting the two calls — the browser holds the candidates in between,
exactly the way POST /upload and POST /evaluate are two calls for a
booklet (api/routers/upload.py's header makes the identical argument).

TENANCY: none, deliberately, same as /questions and /papers.
`Depends(get_current_user)` + `Depends(get_tenant_conn)` — not
`require_college_user` — because uploaded content lives in the same shared,
untenanted schema the question bank does (migration 003 excludes
`paragraphs`/`sentences` by name; migration 020 kept that decision rather
than reopening it). `require_college_user` would 403 a platform_admin on a
table that has no college_id to scope by, exactly the mechanical problem
api/deps/identity.py's docstring describes for /questions and /papers. So a
paragraph uploaded by one college IS visible to, and generatable by, every
other college — on purpose, and
`tests/test_api/test_content.py::test_uploaded_content_is_visible_across_colleges`
pins it the same way test_questions.py pins the shared bank.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

import core.paragraphs as paragraphs_lib
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, get_current_user
from api.deps.pagination import Pagination, get_pagination
from api.deps.quota import rate_limit
from api.schemas.content import (
    CreateContentRequest,
    CreateContentResponse,
    DocumentSummary,
    ParagraphDetail,
    ParagraphStatus,
    ParagraphSummary,
    SplitRequest,
    SplitResponse,
    candidates_from_contents,
    document_to_summary,
    paragraph_to_detail,
    paragraph_to_summary,
)
from api.schemas.pagination import Page

router = APIRouter(prefix="/api/v1/content", tags=["content"])


# ──────────────────────────── split (preview only) ───────────────────────────

@router.post(
    "/split",
    response_model=SplitResponse,
    summary="Preview how pasted text would be split into paragraphs",
)
def split_content(
    body: SplitRequest,
    user: CurrentUser = Depends(get_current_user),
) -> SplitResponse:
    """Pure text transform — a token is required (every endpoint requires
    one; see api/main.py's docstring), but no `conn` dependency at all,
    because nothing here reads or writes a row. Same shape as
    GET /auth/me: authenticated, but no database connection opened. Call
    this first, let the teacher edit the result (merge two candidates, drop
    one, fix a boundary), then POST the final list to `/content`.
    """
    candidates = paragraphs_lib.split_into_paragraphs(body.text)
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="text has no non-blank content to split into paragraphs.",
        )
    return SplitResponse(
        source_document=body.source_document,
        candidates=candidates_from_contents(candidates),
    )


# ─────────────────────────────────── create ──────────────────────────────────

@router.post(
    "",
    response_model=CreateContentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save the (edited) paragraph list — the only endpoint here that writes",
    responses={
        429: {
            "description": (
                "This caller's request rate for this endpoint is at its "
                "configured cap (CONTENT_CREATE_RATE_LIMIT_*). `Retry-After` "
                "says how long to wait — see api/deps/quota.py."
            )
        },
    },
    dependencies=[Depends(rate_limit("content_create"))],
)
def create_content(
    body: CreateContentRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> CreateContentResponse:
    """Inserts one `paragraphs` row (plus its `sentences`) per string in
    `body.paragraphs`, in order, and returns them.

    Every created paragraph starts at status='active', version=1 — ready to
    generate from immediately.
    """
    try:
        with conn.cursor() as cur:
            created = paragraphs_lib.create_paragraphs(
                cur, source_document=body.source_document,
                contents=body.paragraphs,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return CreateContentResponse(
        source_document=body.source_document,
        created=len(created),
        paragraphs=[paragraph_to_summary(p) for p in created],
    )


# ──────────────────────────────────── reads ──────────────────────────────────

@router.get(
    "/paragraphs",
    response_model=Page[ParagraphSummary],
    summary="The picker: list and filter uploaded content",
)
def list_paragraphs(
    status_filter: ParagraphStatus | None = Query(
        default=None, alias="status",
        description="Filter by lifecycle status. 'active' is what "
                    "questions can be generated from.",
    ),
    source_document: str | None = Query(
        default=None, description="Exact match on the upload this paragraph "
                                   "came from. See GET /content/documents "
                                   "for the list of values."
    ),
    q: str | None = Query(
        default=None, description="Case-insensitive substring search over "
                                   "the paragraph's content."
    ),
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[ParagraphSummary]:
    """A filtered page of the shared content corpus, newest first."""
    try:
        with conn.cursor() as cur:
            rows = paragraphs_lib.list_paragraphs(
                cur, status=status_filter, source_document=source_document,
                search=q, limit=pagination.limit, offset=pagination.offset,
            )
            total = paragraphs_lib.count_paragraphs(
                cur, status=status_filter, source_document=source_document,
                search=q,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return Page(
        items=[paragraph_to_summary(r) for r in rows],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )


@router.get(
    "/documents",
    response_model=list[DocumentSummary],
    summary="Distinct uploads, for the picker's document filter",
)
def list_documents(
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> list[DocumentSummary]:
    """Not paginated — see core/paragraphs.py::list_documents's docstring:
    this is one row per upload batch, not per paragraph, and small enough
    that a `Page` wrapper would only be a limit/offset nobody uses."""
    with conn.cursor() as cur:
        rows = paragraphs_lib.list_documents(cur)
    return [document_to_summary(r) for r in rows]


@router.get(
    "/paragraphs/{paragraph_id}",
    response_model=ParagraphDetail,
    summary="One paragraph, its sentences, and the questions generated from it",
    responses={404: {"description": "No such paragraph."}},
)
def get_paragraph(
    paragraph_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> ParagraphDetail:
    with conn.cursor() as cur:
        detail = paragraphs_lib.get_paragraph(cur, paragraph_id=paragraph_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No paragraph {paragraph_id}.",
        )
    return paragraph_to_detail(detail)


# ─────────────────────────────── supersede ───────────────────────────────────

@router.post(
    "/paragraphs/{paragraph_id}/supersede",
    response_model=ParagraphSummary,
    summary="Retire a paragraph — active -> superseded",
    responses={
        404: {"description": "No such paragraph."},
        409: {"description": "The paragraph is already superseded."},
    },
)
def supersede_paragraph(
    paragraph_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_tenant_conn),
) -> ParagraphSummary:
    """Stops the paragraph being offered for FUTURE generation — it does not
    touch any question already generated from it. See
    core/paragraphs.py::supersede_paragraph's docstring."""
    try:
        with conn.cursor() as cur:
            result = paragraphs_lib.supersede_paragraph(cur, paragraph_id=paragraph_id)
    except paragraphs_lib.ParagraphNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except paragraphs_lib.ParagraphAlreadySupersededError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    # supersede_paragraph's UPDATE ... RETURNING does not carry the sentence/
    # question counts list_paragraphs computes — re-derive them here rather
    # than have the core function do a second, only-sometimes-needed query.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM sentences WHERE paragraph_id = %s",
            (str(paragraph_id),),
        )
        (sentence_count,) = cur.fetchone()
        cur.execute(
            """
            SELECT COUNT(*) FROM questions
            WHERE source_type = 'paragraph' AND source_id = %s
            """,
            (str(paragraph_id),),
        )
        (question_count,) = cur.fetchone()

    result["sentence_count"] = sentence_count
    result["question_count"] = question_count
    return paragraph_to_summary(result)
