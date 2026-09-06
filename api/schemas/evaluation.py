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


class RegionDetail(BaseModel):
    """One persisted region (`answer_blocks` row) of this answer, in reading
    order, with what the evaluation did with it.

    THIS IS THE SHAPE THE RESULTS SCREEN OVERLAYS ON A PAGE IMAGE: `bbox` is
    in pixel coordinates of the DESKEWED page the image endpoint serves (not
    of the original PDF page — migration 013's COMMENT on region_bbox says
    why the deskewed page is the one that is stored), so a rectangle drawn
    from it lands where the region actually is.

    `extra="allow"` unlike most models here: the region row gains fields as
    the pipeline records more per region (019's extraction provenance is next),
    and a client should not 500 on a field the server added.
    """

    model_config = ConfigDict(extra="allow")

    block_id: uuid.UUID
    block_type: str = Field(
        description="answer_block_type — what a plugin dispatches on. Lossy by "
                    "design: 20 layout labels collapse into 4 types, which is "
                    "why classification_label is kept beside it.",
    )
    page_number: int | None = Field(
        default=None,
        description="1-based page of the source booklet. Null for a block "
                    "attached from a single image rather than segmented out of "
                    "a booklet — every row predating booklet ingestion.",
    )
    region_bbox: list[float] | None = Field(
        default=None,
        description="[x, y, w, h] in pixels of the deskewed page image. Feed "
                    "GET /results/{answer_id}/pages/{page_number}/image to draw "
                    "it, and use it as the zoom-to-region target.",
    )
    sequence_order: int
    reading_order: int = Field(
        description="0-based position among this answer's regions, page then "
                    "sequence. THE ORDER §7D MERGED THE TEXT IN — the same sort "
                    "key core/booklet_evaluator.build_components uses, so "
                    "`merged_text.parts` and this agree by construction.",
    )

    classification_label: str | None = Field(
        default=None,
        description="Raw layout-model label before it was mapped onto "
                    "block_type ('paragraph_title', 'chart'). The label to "
                    "debug a mis-route with.",
    )
    classification_confidence: float | None = Field(
        default=None,
        description="How sure the layout model was of classification_label, in "
                    "[0,1]. A DIFFERENT axis from ocr_confidence: a region can "
                    "be confidently a table and still be read badly.",
    )
    needs_review: bool = Field(
        description="Set at INGESTION (migration 013) — low classification "
                    "confidence, a block_type no plugin supports, or the "
                    "heuristic fallback path.",
    )
    ingestion_flags: list[str] = Field(
        default_factory=list,
        description="Why ingestion flagged this region, in the same vocabulary "
                    "core/booklet_evaluator.py uses in its own report.",
    )

    text: str | None = Field(
        default=None,
        description=(
            "What this region says — READ `text_source` BEFORE TRUSTING A NULL. "
            "Null does NOT mean the region is empty: the text a plugin OCRs out "
            "of a scanned region is not persisted anywhere today (ingestion "
            "writes answer_blocks.content as NULL and the extraction is dropped "
            "after scoring), so a scanned region has no text to return. Only a "
            "block whose text was already digital carries one. See "
            "core/answer_regions.py's docstring and "
            "migrations/019_answer_block_extractions.sql.proposed."
        ),
    )
    text_source: str | None = Field(
        default=None,
        description="Where `text` came from — 'answer_blocks.content' today. "
                    "Null means no text is persisted for this region, NOT that "
                    "the region is blank.",
    )
    ocr_confidence: float | None = Field(
        default=None,
        description="Extraction confidence for THIS region, when one is "
                    "attributable. Null for a region merged with others: the "
                    "merged component's confidence is the MINIMUM across its "
                    "parts (§7D decision 4), and attributing that minimum to "
                    "each part would misreport every part but the worst.",
    )
    ocr_confidence_source: str | None = None

    question: str | None = Field(
        default=None,
        description="The question label ingestion assigned this region ('Q2a'), "
                    "from the paper's slot labels. Not derivable from the answer "
                    "row alone — there is no FK from exams to generated_papers "
                    "(§7C).",
    )
    scored: bool = Field(
        description="Whether this region ended up inside a component that was "
                    "actually scored. False with `failure` set is a region that "
                    "failed; false with no failure is one nothing routed.",
    )
    component_index: int | None = Field(
        default=None, description="Index into `components` of the component this "
                                  "region belongs to.",
    )
    order_in_component: int | None = None
    merged_with: int = Field(
        default=0,
        description="How many regions were merged into this region's component, "
                    "including itself. >1 is the page-break case §7D decision 3 "
                    "exists for: several regions, ONE scored answer.",
    )
    component_score: float | None = None
    component_confidence: float | None = None
    component_is_primary: bool = Field(
        default=False,
        description="True for the component whose score became the question's "
                    "score (core/booklet_evaluator._pick_primary).",
    )
    failure: dict[str, Any] | None = Field(
        default=None,
        description="This region's own failure record (stage, error_type, "
                    "message) when a stage failed on it. Partial failure is the "
                    "normal case (§7D), so a region that was not scored says why "
                    "rather than silently missing from the components.",
    )

    has_page_image: bool = Field(
        description="Whether a full-page image is stored for this region's page "
                    "— i.e. whether the image endpoint can serve it.",
    )
    has_region_image: bool = Field(
        description="Whether the CROPPED region itself is stored (blob_url). "
                    "Separate from has_page_image: the page is what a reviewer "
                    "sees the region in context on.",
    )


class MergedTextPart(BaseModel):
    """Where one region's text sits inside the merged answer — THE SEAM."""

    model_config = ConfigDict(extra="forbid")

    block_id: uuid.UUID
    page_number: int | None = None
    reading_order: int
    offset: int = Field(description="Start index of this region's text in "
                                    "`merged_text.text`.")
    length: int


class MergedText(BaseModel):
    """The text that was actually scored, and which region each part came from.

    §7D decision 3: a question's text regions are ONE answer, concatenated in
    reading order and evaluated once — scoring each half of a page-spanning
    answer against the whole reference would mark a complete answer twice as
    incomplete. So the merged string is what the score is about, and the
    per-part offsets are what let a teacher see the seam.

    NEVER PARTIAL. `available` is false with a `reason` when any part's text is
    missing, rather than returning a merge with a hole in it — an answer
    missing its second page would otherwise read as a complete answer that
    simply says less, which is the exact failure the merge exists to prevent.
    """

    model_config = ConfigDict(extra="forbid")

    text: str | None = None
    available: bool
    reason: str | None = Field(
        default=None, description="Why `text` is null. Null when available.",
    )
    separator: str = Field(
        description="What the parts were joined with — the evaluator's own "
                    "TEXT_MERGE_SEPARATOR, not a value this layer chose.",
    )
    block_ids: list[uuid.UUID] = Field(default_factory=list)
    parts: list[MergedTextPart] = Field(default_factory=list)


class PageSummary(BaseModel):
    """One page of this answer's booklet, for the page strip."""

    model_config = ConfigDict(extra="forbid")

    page_number: int
    regions: int
    has_image: bool = Field(
        description="Whether GET /results/{answer_id}/pages/{page_number}/image "
                    "has anything to serve — so the client does not have to "
                    "probe it per page to find out.",
    )
    needs_review: bool = Field(
        description="True when at least one region on this page is flagged.",
    )


class PageImageResponse(BaseModel):
    """GET /api/v1/results/{answer_id}/pages/{page_number}/image.

    A SHORT-LIVED PRESIGNED URL, not the bytes — see
    api/services/evaluation.py::resolve_page_image for the argument. The URL is
    generated per request and is stored NOWHERE: the database keeps the stable
    "bucket/key" reference and never a presigned one (CLAUDE_CONTEXT.md §10).
    """

    model_config = ConfigDict(extra="forbid")

    answer_id: uuid.UUID
    page_number: int
    url: str = Field(
        description="Fetch it directly from the browser. Treat it as a "
                    "credential: it grants read access to this one object until "
                    "it expires, so do not log it or put it in a shareable link.",
    )
    expires_in: int = Field(
        description="Seconds this URL stays valid — short by design, and "
                    "shorter than the access token's own lifetime.",
    )
    expires_at: dt.datetime


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
    regions: CappedList[RegionDetail] = Field(
        description="Every persisted region backing this answer, in reading "
                    "order, with page/bbox for the overlay, classification, "
                    "flags, and which component scored it. Capped at NESTED_MAX "
                    "like the other nested lists — a re-ingest DUPLICATES a "
                    "booklet's regions rather than replacing them (§7C), so this "
                    "list has no inherent bound either.",
    )
    pages: list[PageSummary] = Field(
        default_factory=list,
        description="The pages this answer has regions on, and whether each has "
                    "a stored image to fetch.",
    )
    merged_text: MergedText = Field(
        description="The text as it was actually scored PLUS the seam between "
                    "the regions it came from — both, never only the merged "
                    "string (§7D decision 3).",
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
