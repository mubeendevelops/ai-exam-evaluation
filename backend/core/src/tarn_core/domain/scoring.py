"""Scores. Every score records the content versions and scorer versions it used (rule 11).

A score is the AI's suggestion: weighted criterion credits, the rounded mark, and the flags
that tell the teacher where to look first (design.md "Flags the teacher sees")."""

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, uuid5

from tarn_core.domain.booklet import check_aware
from tarn_core.domain.common import (
    ContentKind,
    ContentRef,
    EngineRef,
    JsonValue,
    check_marks,
    check_text,
    check_unit_interval,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import AnswerId, AnswerScoreId, CollegeId, ScoringCalibrationId

CHECK = "check"
"""Criterion flag: the similarity sits near a band edge (borderline credit)."""

DISAGREE = "disagree"
"""Criterion flag: the LLM's credit differs from the non-LLM credit by more than one band."""

BAND = Decimal("0.5")
"""One credit band: credits are 0, 1/2 or 1 (design.md "Text scoring"). Two credits further
apart than this sit in bands more than one apart (design.md "Flags the teacher sees")."""


def scorers_disagree(a: Decimal, b: Decimal) -> bool:
    """LLM and non-LLM credit differ by more than one band."""
    return abs(a - b) > BAND


class AnswerFlag(StrEnum):
    LOW_OCR = "low_ocr"
    """Some of the lines scored were below the OCR line threshold (highlighted lines)."""
    OFF_TARGET = "off_target"
    """Low relevance to the question and key, or names that contradict the key (C13)."""
    BLANK = "blank"
    """Little or no text: 0 marks suggested for the teacher to confirm."""
    MARK_MANUALLY = "mark_manually"
    """The key is guidance only: no AI score (C8)."""
    DUPLICATE = "duplicate"
    """The question was answered twice: each copy was scored, the higher one is suggested."""
    SCORER_DISAGREEMENT = "scorer_disagreement"
    """The LLM and the non-LLM scorer differ by more than one band on a criterion; Panel B
    shows both (P19)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CriterionReason:
    """Why a criterion got its credit, for Panel B. Key-side wording (``matched``,
    ``missing``, ``expected``), positions in the answer (``sentences``, from 0) and, for
    numeric steps, the value found."""

    summary: str
    matched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    sentences: tuple[int, ...] = ()
    found: str | None = None
    expected: str | None = None

    def __post_init__(self) -> None:
        if any(i < 0 for i in self.sentences):
            raise InvariantError("sentence positions count from 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecondOpinion:
    """The LLM's judgement of a criterion that a non-LLM scorer scored (P19). A suggestion like
    any other: it never changes the mark, the teacher sees both. ``reason`` is the model's own
    words (kept short, college data like every score)."""

    scorer: EngineRef
    credit: Decimal
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.credit, Decimal):
            raise InvariantError("credit must be a Decimal")
        check_unit_interval("credit", self.credit)
        check_text("second opinion reason", self.reason)


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """What the LLM provider was asked and charged for, in counts (token accounting). Never
    holds text. ``calls`` counts every request, retries included."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.calls, self.input_tokens, self.output_tokens) < 0:
            raise InvariantError("usage counts must not be negative")

    def __add__(self, other: "LlmUsage") -> "LlmUsage":
        return LlmUsage(
            self.calls + other.calls,
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )

    def __bool__(self) -> bool:
        return self.calls > 0


@dataclass(frozen=True, slots=True, kw_only=True)
class CriterionScore:
    """Credit in [0, 1] for one criterion version, from one scorer version."""

    criterion: ContentRef
    weight: Decimal
    credit: Decimal
    scorer: EngineRef
    evidence: str = ""
    flags: tuple[str, ...] = ()
    similarity: float | None = None
    """The best similarity found (semantic criteria; the graph similarity for diagrams)."""
    reason: CriterionReason | None = None
    second_opinion: SecondOpinion | None = None
    """The LLM's credit beside the scorer's, when the college has the LLM scorer on (P19)."""
    detail: JsonValue = None
    """The R6 comparison document of a diagram criterion
    (``docs/api/diagram-comparison.schema.json``); None for other criteria. It holds the
    student's diagram labels, so it stays in college data and never reaches the audit log."""

    def __post_init__(self) -> None:
        if self.criterion.kind is not ContentKind.RUBRIC_CRITERION:
            raise InvariantError("a criterion score must point at a rubric criterion")
        check_marks("criterion weight", self.weight, allow_zero=False)
        if not isinstance(self.credit, Decimal):
            raise InvariantError("credit must be a Decimal")
        check_unit_interval("credit", self.credit)
        if self.similarity is not None and not math.isfinite(self.similarity):
            raise InvariantError("similarity must be a finite number")
        if self.second_opinion is not None and (
            scorers_disagree(self.credit, self.second_opinion.credit) != (DISAGREE in self.flags)
        ):
            raise InvariantError("the 'disagree' flag marks exactly the criteria that disagree")

    @property
    def marks(self) -> Decimal:
        return self.weight * self.credit


def round_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round to the paper's smallest step (usually half a mark), halves rounding up."""
    return (value / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step


def answer_mark(scores: tuple[CriterionScore, ...], mark_step: Decimal) -> Decimal:
    """Answer mark = sum(weight x credit), rounded to the step (design.md "Text scoring")."""
    return round_to_step(sum((s.marks for s in scores), Decimal(0)), mark_step)


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerScore:
    """The AI's suggestion for one answer. ``content_versions`` must name the question and
    every criterion scored; it may also name the key, glossary, diagrams, blueprint and the
    scoring calibration.

    A guidance-only key gives a score flagged ``mark_manually`` with no criteria and no mark:
    the teacher marks it. ``reasons`` are answer-level notes (off-target, duplicate)."""

    id: AnswerScoreId
    college_id: CollegeId
    answer_id: AnswerId
    question: ContentRef
    criterion_scores: tuple[CriterionScore, ...]
    mark_step: Decimal
    mark: Decimal | None
    content_versions: frozenset[ContentRef]
    created_at: datetime
    flags: tuple[AnswerFlag, ...] = ()
    relevance: float | None = None
    """Relevance of the whole answer to the question and key (the off-target guard)."""
    embedder: EngineRef | None = None
    """The sentence-embedding model the semantic criteria and the guard used."""
    reasons: tuple[str, ...] = ()
    llm_usage: LlmUsage | None = None
    """What the LLM scorer cost for this score (None: it was not used)."""

    def __post_init__(self) -> None:
        if self.question.kind is not ContentKind.QUESTION:
            raise InvariantError("an answer score must point at a question")
        if len(set(self.flags)) != len(self.flags):
            raise InvariantError("an answer flag appears twice")
        manual = AnswerFlag.MARK_MANUALLY in self.flags
        if manual:
            if self.criterion_scores or self.mark is not None:
                raise InvariantError("a 'mark manually' score has no criteria and no mark")
        elif not self.criterion_scores:
            raise InvariantError("an answer score needs at least one criterion score")
        missing = {self.question, *(c.criterion for c in self.criterion_scores)}
        missing -= self.content_versions
        if missing:
            raise InvariantError(
                f"content versions used but not recorded: {sorted(map(str, missing))}"
            )
        criteria = [c.criterion.id for c in self.criterion_scores]
        if len(set(criteria)) != len(criteria):
            raise InvariantError("each criterion is scored once per answer score")
        if not manual and self.mark != answer_mark(self.criterion_scores, self.mark_step):
            raise InvariantError("mark must equal the rounded sum of criterion marks")
        if self.relevance is not None:
            check_unit_interval("relevance", self.relevance)
        check_aware("created_at", self.created_at)

    @property
    def scorer_versions(self) -> frozenset[EngineRef]:
        """Every scorer that produced a credit or a second opinion (rule 11)."""
        return frozenset(
            ref
            for c in self.criterion_scores
            for ref in (c.scorer, *(() if c.second_opinion is None else (c.second_opinion.scorer,)))
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SentenceVector:
    """One sentence of an answer as embedded by one model (pgvector, college data).
    ``text_sha256`` lets a re-score reuse the vector when the sentence did not change."""

    index: int
    text_sha256: str
    vector: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.index < 0:
            raise InvariantError("sentence index counts from 0")
        if len(self.text_sha256) != 64:
            raise InvariantError("text_sha256 must be 64 hex characters")
        if not self.vector or not all(math.isfinite(x) for x in self.vector):
            raise InvariantError("a sentence vector is a non-empty list of finite numbers")


def scoring_calibration_id(embedder: str) -> ScoringCalibrationId:
    """One calibration item per embedding model: a refit is its next version."""
    return ScoringCalibrationId(uuid5(NAMESPACE_URL, f"tarn:scoring-calibration:{embedder}"))


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringCalibration:
    """Credit bands and flag thresholds for one embedding model, fitted on teacher-marked
    answers by the Tarn operator (``tarn score calibrate``). Global, numbers only (like OCR
    calibrations, D72). Similarities are cosines in [0, 1].

    Semantic credit: >= ``full`` -> 1, >= ``half`` -> 1/2, else 0; ``margin`` around either
    edge flags the criterion "check". Off-target: relevance < ``relevance_min``, or names
    unknown to the subject's content with relevance < ``relevance_soft``."""

    embedder: str
    version: int = 1
    half: float
    full: float
    margin: float
    relevance_min: float
    relevance_soft: float
    samples: int = 0
    mean_abs_diff: float | None = None
    """Mean absolute difference from the teacher (marks) on the fitting answers."""
    fitted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.embedder.strip():
            raise InvariantError("a scoring calibration names its embedder")
        if self.version < 1:
            raise InvariantError("calibration version starts at 1")
        for name in ("half", "full", "margin", "relevance_min", "relevance_soft"):
            check_unit_interval(name, getattr(self, name))
        if not self.half < self.full:
            raise InvariantError("the half-credit edge must be below the full-credit edge")
        if not self.relevance_min <= self.relevance_soft:
            raise InvariantError("relevance_min must not exceed relevance_soft")
        if self.samples < 0:
            raise InvariantError("sample count must not be negative")
        if self.mean_abs_diff is not None and not (
            math.isfinite(self.mean_abs_diff) and self.mean_abs_diff >= 0
        ):
            raise InvariantError("mean absolute difference must be a non-negative number")
        if self.fitted_at is not None:
            check_aware("fitted_at", self.fitted_at)

    @property
    def id(self) -> ScoringCalibrationId:
        return scoring_calibration_id(self.embedder)

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.SCORING_CALIBRATION, id=self.id, version=self.version)
