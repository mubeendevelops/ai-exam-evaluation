"""api/schemas/content.py — Pydantic v2 models for the content-upload +
paragraph-picker endpoints (api/routers/content.py).

SPLIT AND CREATE ARE TWO CALLS, DELIBERATELY NOT COLLAPSED. `SplitRequest` /
`SplitResponse` are a preview: they never touch the database, and a teacher
is expected to edit the candidates (merge two, drop one, fix a boundary) in
the browser before anything is saved. `CreateContentRequest` therefore takes
the EDITED list of paragraph strings, not the original pasted text — there
is no server-side draft in between, so the browser is what carries the
candidates from one call to the other. This mirrors why POST /upload and
POST /evaluate are two calls (api/routers/upload.py's header): a request that
only stores something and a request that commits it are different enough in
consequence to deserve different endpoints.

WHY THERE IS NO `college_id` ANYWHERE IN THIS FILE: uploaded content lives in
the same shared, untenanted schema as the question bank it feeds — migration
003 excludes `paragraphs`/`sentences` from RLS by name, and migration 020
(which added this feature's indexes) keeps that decision rather than
reopening it. A college_id field here would be a fabrication, and a future
endpoint could be tempted to accept and filter on it, which would read as
tenant isolation while providing none. See api/schemas/questions.py's
docstring for the identical reasoning on the question bank itself.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.questions import QuestionSummary, question_to_summary

#: paragraph_status enum (migration 002 line 22).
ParagraphStatus = Literal["active", "superseded"]

#: Preview length for a picker row. Long enough to recognise a paragraph at
#: a glance, short enough that a page of 50 rows renders as a scannable
#: list rather than 50 walls of text — the full body is one click away via
#: GET /content/paragraphs/{id}.
_PREVIEW_LENGTH = 220


def _preview(content: str) -> str:
    if len(content) <= _PREVIEW_LENGTH:
        return content
    return content[:_PREVIEW_LENGTH].rstrip() + "…"


# ─────────────────────────────── splitting ───────────────────────────────────

class SplitRequest(BaseModel):
    """POST /api/v1/content/split — the preview step. Writes nothing."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        min_length=1, max_length=200_000,
        description="The pasted content, as one block of text.",
    )
    source_document: str = Field(
        min_length=1, max_length=500,
        description="A human-chosen name for this upload, e.g. 'Unit 3 notes'. "
                    "Every paragraph created from this text will carry it, and "
                    "the picker's document filter groups on it.",
    )


class ParagraphCandidate(BaseModel):
    """One suggested paragraph boundary — not yet saved."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Position in the candidate list, for "
                                          "the client to key its edit UI on.")
    content: str
    char_count: int = Field(ge=0)
    sentence_count: int = Field(ge=0)


class SplitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document: str
    candidates: list[ParagraphCandidate]


def candidates_from_contents(contents: list[str]) -> list[ParagraphCandidate]:
    """core.paragraphs.split_into_sentences drives the sentence_count shown
    here — the same count `create_paragraphs` will actually persist, so the
    preview is not lying about what saving will produce."""
    import core.paragraphs as paragraphs_lib

    return [
        ParagraphCandidate(
            index=i, content=content, char_count=len(content),
            sentence_count=len(paragraphs_lib.split_into_sentences(content)),
        )
        for i, content in enumerate(contents)
    ]


# ────────────────────────────────── create ────────────────────────────────────

class CreateContentRequest(BaseModel):
    """POST /api/v1/content — commits the (possibly hand-edited) paragraph
    list. This is what actually writes rows."""

    model_config = ConfigDict(extra="forbid")

    source_document: str = Field(min_length=1, max_length=500)
    paragraphs: list[str] = Field(
        min_length=1, max_length=200,
        description="The final paragraph texts to save, in order — typically "
                    "POST /content/split's candidates after the teacher has "
                    "reviewed, merged, edited, or removed some of them. "
                    "Capped at 200: a single upload this large is almost "
                    "certainly a splitting problem, not real content.",
    )


class ParagraphSummary(BaseModel):
    """One row of GET /api/v1/content/paragraphs."""

    model_config = ConfigDict(extra="forbid")

    paragraph_id: uuid.UUID
    preview: str = Field(description=f"content, truncated to {_PREVIEW_LENGTH} "
                                      f"characters. Fetch the detail endpoint "
                                      f"for the full text.")
    source_document: str
    status: ParagraphStatus
    version: int
    uploaded_at: dt.datetime
    sentence_count: int = Field(ge=0)
    question_count: int = Field(
        ge=0,
        description="Questions already generated from this paragraph "
                    "(source_type='paragraph', any status) — the single most "
                    "useful column for deciding what to pick next.",
    )


class CreateContentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document: str
    created: int = Field(ge=0)
    paragraphs: list[ParagraphSummary]


class ParagraphDetail(BaseModel):
    """GET /api/v1/content/paragraphs/{id} — the picker's detail rail."""

    model_config = ConfigDict(extra="forbid")

    paragraph_id: uuid.UUID
    content: str
    source_document: str
    status: ParagraphStatus
    version: int
    uploaded_at: dt.datetime
    sentences: list[str]
    questions: list[QuestionSummary]


class DocumentSummary(BaseModel):
    """One row of GET /api/v1/content/documents."""

    model_config = ConfigDict(extra="forbid")

    source_document: str
    paragraph_count: int = Field(ge=0)
    last_uploaded_at: dt.datetime


def paragraph_to_summary(row: dict[str, Any]) -> ParagraphSummary:
    """core/paragraphs.py row dict -> response model. One mapping point, so
    a column added to `paragraphs` by a future migration cannot leak into an
    API response merely by existing — same convention as
    api/schemas/questions.py::question_to_summary."""
    return ParagraphSummary(
        paragraph_id=row["paragraph_id"],
        preview=_preview(row["content"]),
        source_document=row["source_document"],
        status=row["status"],
        version=row["version"],
        uploaded_at=row["uploaded_at"],
        sentence_count=row["sentence_count"],
        question_count=row["question_count"],
    )


def paragraph_to_detail(row: dict[str, Any]) -> ParagraphDetail:
    """core.paragraphs.get_paragraph's row dict -> response model."""
    return ParagraphDetail(
        paragraph_id=row["paragraph_id"],
        content=row["content"],
        source_document=row["source_document"],
        status=row["status"],
        version=row["version"],
        uploaded_at=row["uploaded_at"],
        sentences=row["sentences"],
        questions=[question_to_summary(q) for q in row["questions"]],
    )


def document_to_summary(row: dict[str, Any]) -> DocumentSummary:
    return DocumentSummary(
        source_document=row["source_document"],
        paragraph_count=row["paragraph_count"],
        last_uploaded_at=row["last_uploaded_at"],
    )
