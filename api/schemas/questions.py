"""api/schemas/questions.py — Pydantic v2 models for the question-bank endpoints.

The status vocabulary here is the DB's, unedited: `question_status` is a
Postgres enum (migration 001 line 30) and these Literals are its five values.
They are NOT a place to add an API-only status — a value FastAPI accepts that
Postgres does not would fail inside the INSERT as a 500.

WHY THERE IS NO `college_id` ANYWHERE IN THIS FILE: the question bank is
shared across colleges by design (migration 003 lines 5 and 341). A
college_id field on a question response would be a fabrication, and — worse —
a field a future endpoint would be tempted to accept and filter on, which
would read as tenant isolation while providing none.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: question_status enum (migration 001 line 30).
QuestionStatus = Literal["draft", "confirmed", "rejected", "live", "superseded"]

#: question_style enum (migration 002 line 32) == core.llm.VALID_QUESTION_STYLES.
QuestionStyle = Literal["long", "short", "one_word", "mcq"]

#: question_source_type enum (migration 002 line 28).
QuestionSourceType = Literal["sentence", "paragraph", "diagram", "table", "formula", "manual"]

#: The reviewer's decision at the QUALITY gate. These are the CLI's
#: --action values (scripts/review_question.py::ACTION_TO_STATUS), not the
#: resulting statuses: a reviewer performs "confirm", which produces
#: "confirmed". Keeping the API's verb identical to the CLI's means one
#: vocabulary for the same operation across both front doors.
ReviewAction = Literal["confirm", "reject"]


# ────────────────────────────── read models ─────────────────────────────────

class QuestionSummary(BaseModel):
    """One row of GET /api/v1/questions."""

    model_config = ConfigDict(extra="forbid")

    question_id: uuid.UUID
    content: str
    status: QuestionStatus
    style: QuestionStyle | None = None
    marks_max: float | None = None
    source_type: QuestionSourceType | None = None
    source_id: uuid.UUID | None = Field(
        default=None,
        description="What this question was generated from — a paragraph_id "
                    "when source_type is 'paragraph'. Null for manual entries.",
    )
    is_ai_generated: bool | None = Field(
        default=None,
        description="Null for rows written before migration 005 added the "
                    "column; the tri-state is real and is not flattened to "
                    "false, which would assert human authorship this repo "
                    "cannot actually vouch for.",
    )
    question_group_id: uuid.UUID | None = None
    parent_question_id: uuid.UUID | None = None
    created_at: dt.datetime | None = None


class QuestionListResponse(BaseModel):
    """A page of the bank, plus the total that matched the same filters."""

    model_config = ConfigDict(extra="forbid")

    questions: list[QuestionSummary]
    total: int = Field(
        ge=0,
        description="Rows matching the filters, ignoring limit/offset — so a "
                    "client can page without guessing when it has reached the "
                    "end.",
    )
    limit: int
    offset: int


class PriorReview(BaseModel):
    """One row of a question's question_reviews history."""

    model_config = ConfigDict(extra="forbid")

    reviewer_id: uuid.UUID
    action: Literal["confirmed", "rejected"] = Field(
        description="question_review_action enum — the DECISION recorded, "
                    "which is the past-tense form of the request's `action`."
    )
    comment: str | None = None
    reviewed_at: dt.datetime


class QuestionDetail(QuestionSummary):
    """One question in full: GET /api/v1/questions/{id}.

    Adds the source paragraph's text and the review history, which is exactly
    what `scripts/review_question.py --show` puts in front of a human before
    they decide. The API showing less than the CLI would push reviewers back
    to the terminal to make an informed call.
    """

    model_config = ConfigDict(extra="forbid")

    source_paragraph_content: str | None = None
    prior_reviews: list[PriorReview] = Field(default_factory=list)


# ───────────────────────────── generation ───────────────────────────────────

class GenerateQuestionsRequest(BaseModel):
    """POST /api/v1/questions/generate.

    There is no `status` field and there will not be one. Generation always
    produces status='draft' (scripts/generate_questions.py hardcodes it in the
    INSERT), because every question-creation path in this codebase feeds the
    mandatory review gate. A request field that could name a starting status
    would be the skip-review path this API is explicitly not allowed to have.
    """

    model_config = ConfigDict(extra="forbid")

    paragraph_id: uuid.UUID = Field(
        description="The already-uploaded paragraph to generate from. Must be "
                    "status='active' — generating from superseded content is "
                    "refused."
    )
    count: int = Field(
        default=3, ge=1, le=20,
        description="How many questions to generate. Capped at 20: each one is "
                    "an LLM-produced draft a human then has to read.",
    )
    style: QuestionStyle | None = Field(
        default=None,
        description="Constrain every generated question to this style. Null "
                    "lets the model choose per question.",
    )
    intent_hint: str | None = Field(
        default=None,
        max_length=2000,
        description='Teacher guidance, e.g. "focus on definitions, easy '
                    'difficulty" — the intent-hinted generation approach.',
    )
    stub_llm: bool = Field(
        default=False,
        description="Development only: skip the real LLM and emit deterministic "
                    "placeholder text. Rejected with 403 unless debug endpoints "
                    "are enabled.",
    )


class GeneratedQuestion(BaseModel):
    """One freshly created draft."""

    model_config = ConfigDict(extra="forbid")

    question_id: uuid.UUID
    question_group_id: uuid.UUID
    status: Literal["draft"] = Field(
        description="Always 'draft'. Typed as a single-value Literal so that "
                    "if generation ever started returning anything else, this "
                    "response would fail validation loudly instead of quietly "
                    "reporting that review had been skipped."
    )
    style: QuestionStyle
    marks_max: float
    content: str


class GenerateQuestionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paragraph_id: uuid.UUID
    count: int = Field(ge=0)
    questions: list[GeneratedQuestion]


# ──────────────────────────── the two gates ─────────────────────────────────

class ReviewRequest(BaseModel):
    """POST /api/v1/questions/{id}/review — the QUALITY gate.

    NO `reviewer_id` FIELD (RE-4). It was required here, exactly as
    `--reviewer-id` is required on the CLI, because identity was a header
    carrying a college rather than a person. It now comes from the access
    token: `CurrentUser.reviewer_id`, which is `users.reviewer_id` for the
    account that authenticated, so the reviewer recorded in question_reviews
    and question_status_history is the human who actually clicked.

    The CLI keeps its flag, and that is not an inconsistency: `--reviewer-id`
    is how an operator at a terminal, holding database credentials, says who
    they are. A CLIENT saying who it is over HTTP is the thing that was wrong.
    """

    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    comment: str | None = Field(
        default=None,
        max_length=4000,
        description="Optional reviewer note, stored on the question_reviews row.",
    )


# THERE IS NO `PromoteRequest`. POST /questions/{id}/promote TAKES NO BODY.
#
# It used to carry exactly one field, `reviewer_id`, and RE-4 deleted that:
# the transition is recorded in question_status_history and attributed to the
# authenticated caller, not to whoever the caller named. With that field gone
# the model had no fields left, and a body model with no fields is worse than
# no body — it obliges every client to send `{}` and gives a 422 to the ones
# that reasonably send nothing. There is no `action` either: promotion has
# exactly one direction (confirmed -> live).


class TransitionResponse(BaseModel):
    """The result of either gate.

    Both gates return the same shape on purpose: a client that has just moved
    a question reads old_status/new_status and needs nothing else. review_id
    is populated only by the review gate — promotion writes no
    question_reviews row, because publishing is an operational decision and
    not a content-quality judgement (scripts/review_question.py::
    promote_question). A null review_id on a promote response is that
    distinction, not a missing value.
    """

    model_config = ConfigDict(extra="forbid")

    question_id: uuid.UUID
    old_status: QuestionStatus
    new_status: QuestionStatus
    reviewer_id: uuid.UUID
    review_id: uuid.UUID | None = None
    comment: str | None = None


def question_to_summary(row: dict[str, Any]) -> QuestionSummary:
    """core/question_bank.py row dict -> response model.

    One mapping point, so a column added to `questions` by a future migration
    cannot leak into an API response merely by existing.
    """
    return QuestionSummary(
        question_id=row["question_id"],
        content=row["content"],
        status=row["status"],
        style=row["style"],
        marks_max=row["marks_max"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        is_ai_generated=row["is_ai_generated"],
        question_group_id=row["question_group_id"],
        parent_question_id=row["parent_question_id"],
        created_at=row["created_at"],
    )


def question_to_detail(row: dict[str, Any]) -> QuestionDetail:
    """scripts/review_question.py::show_question row dict -> response model."""
    return QuestionDetail(
        question_id=row["question_id"],
        content=row["content"],
        status=row["status"],
        style=row["style"],
        marks_max=row["marks_max"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        is_ai_generated=row["is_ai_generated"],
        question_group_id=row["question_group_id"],
        parent_question_id=row["parent_question_id"],
        created_at=row["created_at"],
        source_paragraph_content=row["source_paragraph_content"],
        prior_reviews=[PriorReview(**pr) for pr in row["prior_reviews"]],
    )
