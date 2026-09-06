"""api/schemas/evaluation.py — Pydantic v2 models for evaluate / results /
override."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.jobs import JobStatus
from api.schemas.pagination import CappedList

#: answer_reviews.action — the answer_review_action enum from migration 001.
ReviewAction = Literal["confirmed", "overridden", "flagged"]

#: answer_status enum (migration 001 line 34) — core/results.py::VALID_STATUSES.
AnswerStatus = Literal["pending_evaluation", "ai_scored", "sme_reviewed", "finalized", "flagged"]


class EvaluateRequest(BaseModel):
    """POST /api/v1/evaluate.

    The first three ids are what identifies a booklet in this schema. There is
    no booklets table and no exams.paper_id column (§7C's open schema gap), so
    core/booklet_evaluator.load_booklet_tasks addresses a booklet as "the
    answers one student wrote for one exam", optionally narrowed to a single
    ingestion. upload_id supplies that narrowing by resolving to the stored
    scan's blob_url — which is why all three are required rather than
    exam+student alone.

    paper_id is the fourth, and it is required for the same missing FK: the
    question markers this booklet's regions carry ('Q1', 'Q2a') are resolved
    against pattern_slots.slot_label through one specific generated paper,
    labels repeat across papers, and the exam cannot name its paper. See
    api/services/ingestion.py's docstring — this field is what replaces the
    CLI's --paper-id, and it is what would become optional if
    `exams.paper_id` were ever added (§10, a product decision).
    """

    model_config = ConfigDict(extra="forbid")

    upload_id: uuid.UUID = Field(description="From POST /api/v1/upload.")
    exam_id: uuid.UUID
    student_id: uuid.UUID
    paper_id: uuid.UUID = Field(
        description=(
            "generated_papers.paper_id whose slot labels the booklet's "
            "question markers resolve against. Required because there is no FK "
            "from exams to generated_papers (§7C). Still required when the "
            "booklet is already ingested — it is validated either way, so a "
            "wrong paper is a 404 now rather than a mis-associated re-ingest "
            "later."
        )
    )

    stub: bool = Field(
        default=False,
        description=(
            "DEVELOPMENT ONLY — score with the plugins' stub paths instead of "
            "the real models and Groq. Rejected with 403 unless debug "
            "endpoints are enabled, because a stub score is a FABRICATED score "
            "and it lands in the append-only ledger like any other."
        ),
    )
    stub_llm: bool = Field(
        default=False,
        description="DEVELOPMENT ONLY — skip only the LLM call. Same 403 gate as `stub`.",
    )
    method: str | None = Field(
        default=None,
        description=(
            "Override the text plugin's scoring method (e.g. 'embeddings', "
            "'llm'). Null uses the plugin's blended default."
        ),
    )


class EvaluateResponse(BaseModel):
    """202 Accepted — the work is queued, not done.

    202 rather than 201 on purpose: booklet evaluation takes minutes (§7D), so
    what was created is a promise to do the work, and the client's next move is
    to poll `job_id`. 200 would imply a finished result the response does not
    contain.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID = Field(description="Poll GET /api/v1/jobs/{job_id}.")
    job_type: str = Field(
        description=(
            "What `job_id` actually is: 'booklet_ingest' when this booklet had "
            "to be segmented first, 'booklet_eval' when it was already "
            "ingested and is being re-scored. The client polls the same "
            "endpoint either way, but an ingest job's `result` carries the "
            "`evaluation_job_id` to poll next."
        )
    )
    ingest_job_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "The ingestion job queued for this booklet, or null when its "
            "regions already existed. Equal to job_id when it is not null — "
            "named separately so a client can tell 'ingest then evaluate' from "
            "'evaluate' without parsing job_type."
        ),
    )
    status: JobStatus = Field(description="Always 'queued' at this point.")
    upload_id: uuid.UUID
    exam_id: uuid.UUID
    student_id: uuid.UUID
    paper_id: uuid.UUID
    created_at: dt.datetime


class SignalDetail(BaseModel):
    """One scoring signal, in the shape core/booklet_evaluator._signal_summary
    actually emits — a score PLUS what produced it and what it counted for.

    `score` and `available` are separate fields rather than one nullable
    number because a signal that did not run and a signal that ran and scored
    zero are different facts about an answer. Collapsing them would read as
    "scored, and scored zero" and would quietly drag any average a client
    computed over the signals.
    """

    model_config = ConfigDict(extra="allow")

    score: float | None = None
    model: str | None = Field(
        default=None, description="Which model or rule set produced this signal."
    )
    weight: float | None = Field(
        default=None,
        description="Effective weight after renormalizing over the signals that "
                    "actually ran — not the configured weight.",
    )
    available: bool = Field(
        default=False,
        description="False when this signal did not run at all (no keywords on "
                    "the question, an embeddings-only method, a failed LLM call).",
    )
    explanation: str | None = None


class SignalBreakdown(BaseModel):
    """The text plugin's per-signal breakdown: semantic / keyword / llm /
    rubric, each a SignalDetail.

    Modelled as an open mapping rather than four fixed fields — the plugin owns
    which signals exist, and pinning them here would mean this file had to
    change every time core/plugins/text_extraction.py grew one. Only the
    signals that the plugin reported are present.
    """

    model_config = ConfigDict(extra="allow")

    semantic: SignalDetail | None = None
    keyword: SignalDetail | None = None
    llm: SignalDetail | None = None
    rubric: SignalDetail | None = None


class ConfidenceBlock(BaseModel):
    """Two DIFFERENT axes of confidence, kept apart deliberately.

    `question`/`booklet` are evaluation confidence — how sure the scorer is of
    the mark. Each region's `classification_confidence` is how sure the layout
    model was about what KIND of region it is. A region can be confidently a
    table and still be read badly (migration 013 spells this out), and the two
    failures need different fixes, so they are never merged into one number.
    """

    model_config = ConfigDict(extra="forbid")

    question: float | None = None
    booklet: float | None = None
    region_counts: dict[str, int] = Field(
        default_factory=dict,
        description="{total, evaluated, failed, flagged} for this question. "
                    "`total - evaluated` is how much of the answer was never "
                    "scored — the gap §7D keeps visible rather than folding "
                    "into a zero.",
    )
    regions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-region identity (block_id, question, block_type, page, "
                    "bbox), flattened out of the components so a reviewer can "
                    "find each region on its original page.",
    )


class EvaluationBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_id: uuid.UUID
    score: float
    explanation: str | None = None
    evaluator_type: str
    evaluator_model: str | None = None
    evaluated_at: dt.datetime
    plugin: str | None = None
    plugin_version: str | None = None
    reference_answer_variant_id: uuid.UUID | None = None
    reference_asset_id: uuid.UUID | None = Field(
        default=None,
        description="Exactly one of the two reference ids is set — "
                    "evaluation_results is polymorphic over them (migration 012).",
    )


class LedgerEntry(BaseModel):
    """One row of the append-only score history.

    The whole history is returned, not just the current row, because
    evaluation_results is append-only (rule 2) — a re-scored answer keeps
    every previous score, and `is_current` moves rather than rows being
    replaced. That audit trail is the point of the rule, so the API surfaces
    it instead of hiding everything but the latest.
    """

    model_config = ConfigDict(extra="forbid")

    evaluation_id: uuid.UUID
    score: float
    evaluator_type: str
    evaluator_model: str | None = None
    is_current: bool
    evaluated_at: dt.datetime


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: uuid.UUID
    reviewer_id: uuid.UUID
    action: ReviewAction
    final_marks: float | None = None
    comment: str | None = None
    reviewed_at: dt.datetime


class FinalMarks(BaseModel):
    """The mark that actually stands, and where it came from.

    Computed at READ time from the ledger plus the review log, never stored.
    Storing it would mean UPDATEing a row whenever a review arrives, and both
    source tables are append-only by design.
    """

    model_config = ConfigDict(extra="forbid")

    marks: float | None = None
    source: Literal["ai", "sme_override"] | None = None
    review_id: uuid.UUID | None = None
    ai_score: float | None = Field(
        default=None,
        description="The AI's score, kept alongside an override so the "
                    "disagreement stays visible rather than being overwritten.",
    )


class ResultResponse(BaseModel):
    """GET /api/v1/results/{answer_id} — the full report for one answer."""

    model_config = ConfigDict(extra="forbid")

    answer_id: uuid.UUID
    question_id: uuid.UUID
    question_text: str | None = None
    student_id: uuid.UUID
    exam_id: uuid.UUID
    status: str
    source_scan_url: str | None = None
    submitted_at: dt.datetime
    marks_max: float | None = None

    evaluation: EvaluationBlock | None = Field(
        default=None,
        description="The current ledger row. Null when the answer has not been "
                    "scored yet — which is NOT the same as scoring zero.",
    )
    signals: SignalBreakdown | None = Field(
        default=None,
        description="Text plugin per-signal breakdown. Null for table/diagram "
                    "answers, which have no signals — not an all-zero object.",
    )
    components: CappedList[dict[str, Any]] = Field(
        description="Per-region scores that were aggregated into the single "
                    "question result — one ledger row per QUESTION is decision 1 "
                    "in core/booklet_evaluator.py, and this is what it preserved. "
                    "Capped at core/pagination.py::NESTED_MAX — see that "
                    "module's docstring on why an unbounded nested list still "
                    "needs a cap even though it isn't a list endpoint.",
    )
    flags: list[str] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-stage failures recorded during evaluation. Partial "
                    "failure is the normal case (§7D) — a booklet is not "
                    "abandoned because one region's OCR failed.",
    )
    confidence: ConfidenceBlock
    history: CappedList[LedgerEntry] = Field(
        description="The append-only ledger, newest first, capped at "
                    "NESTED_MAX rows with the true total alongside — a booklet "
                    "re-scored often enough must not make this response grow "
                    "without bound.",
    )
    reviews: CappedList[ReviewEntry] = Field(
        description="Every answer_reviews row, newest first, same cap-and-flag "
                    "treatment as `history`.",
    )
    final_marks: FinalMarks


class ResultSummary(BaseModel):
    """One row of GET /api/v1/results — NOT the full report.

    GET /results/{answer_id} (ResultResponse above) carries the whole ledger
    history, every review and every region component; a list of dozens of
    answers must not repeat that per row. This is the "which answers match
    these filters, and what is their state right now" view — a client that
    needs one row's full report still calls GET /results/{answer_id}.
    """

    model_config = ConfigDict(extra="forbid")

    answer_id: uuid.UUID
    question_id: uuid.UUID
    student_id: uuid.UUID
    exam_id: uuid.UUID
    status: AnswerStatus
    submitted_at: dt.datetime
    score: float | None = Field(
        default=None,
        description="The CURRENT ledger score, or null if this answer has "
                    "not been evaluated yet — not the same as scoring zero.",
    )
    evaluated_at: dt.datetime | None = None
    needs_review: bool = Field(
        description="True when at least one of this answer's answer_blocks "
                    "carries needs_review — a region-level flag (migration "
                    "013), rolled up to the answer for this filter/summary. "
                    "See core/results.py's docstring.",
    )


def result_to_summary(row: dict[str, Any]) -> ResultSummary:
    return ResultSummary(
        answer_id=row["answer_id"], question_id=row["question_id"],
        student_id=row["student_id"], exam_id=row["exam_id"], status=row["status"],
        submitted_at=row["submitted_at"],
        score=float(row["score"]) if row["score"] is not None else None,
        evaluated_at=row["evaluated_at"], needs_review=row["needs_review"],
    )


class OverrideRequest(BaseModel):
    """POST /api/v1/results/{answer_id}/override.

    THERE IS NO `reviewer_id` FIELD, and there must never be one again. It was
    here while identity was a stub that knew a college but not a person, and
    it meant a client could attribute a review to any reviewer in its own
    college — migration 003's trg_answer_reviews_derive_and_check_college
    refuses only reviewers from OTHER colleges, so the whole of one college's
    staff was impersonable by any of them. The reviewer is now
    `CurrentUser.reviewer_id`, out of the signed token
    (api/deps/identity.py, RE-4).

    `extra="forbid"` is what makes that stick: a client still sending the old
    field gets a 422 naming it, rather than having it silently ignored while
    the review is attributed to someone else than they asked for.
    """

    model_config = ConfigDict(extra="forbid")

    action: ReviewAction = Field(
        default="overridden",
        description="'overridden' changes the mark, 'confirmed' agrees with the "
                    "AI, 'flagged' escalates without setting one.",
    )
    final_marks: float | None = Field(
        default=None,
        ge=0,
        description="The mark that should stand. Required for 'overridden'; must "
                    "be omitted for 'flagged', which by definition has not "
                    "decided on a mark.",
    )
    comment: str | None = Field(default=None, max_length=4000)


class OverrideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: uuid.UUID
    answer_id: uuid.UUID
    reviewer_id: uuid.UUID
    action: ReviewAction
    final_marks: float | None = None
    comment: str | None = None
    reviewed_at: dt.datetime
    previous_status: str
    status: str
    status_changed: bool = Field(
        description="False when the answer was already 'sme_reviewed' (a second "
                    "override) or is 'finalized' (no path back out — an open "
                    "product decision, not settled here). The review row is "
                    "written either way."
    )
    evaluation_results_untouched: Literal[True] = Field(
        default=True,
        description=(
            "Always true, and stated in the response on purpose: an override "
            "NEVER writes to evaluation_results. That ledger records what the AI "
            "computed and is append-only (rule 2); a teacher's disagreement is a "
            "new fact in answer_reviews, not a correction to the model's output. "
            "GET /results/{answer_id} reconciles the two at read time."
        ),
    )
