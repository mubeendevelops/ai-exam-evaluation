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
    check_unit_interval,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import AnswerId, AnswerScoreId, CollegeId, ScoringCalibrationId

CHECK = "check"
"""Criterion flag: the similarity sits near a band edge (borderline credit)."""


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
        return frozenset(c.scorer for c in self.criterion_scores)


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
