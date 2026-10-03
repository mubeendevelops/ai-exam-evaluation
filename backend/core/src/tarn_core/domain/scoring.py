"""Scores. Every score records the content versions and scorer versions it used (rule 11)."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from tarn_core.domain.booklet import check_aware
from tarn_core.domain.common import (
    ContentKind,
    ContentRef,
    EngineRef,
    check_marks,
    check_unit_interval,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import AnswerId, AnswerScoreId, CollegeId


@dataclass(frozen=True, slots=True, kw_only=True)
class CriterionScore:
    """Credit in [0, 1] for one criterion version, from one scorer version."""

    criterion: ContentRef
    weight: Decimal
    credit: Decimal
    scorer: EngineRef
    evidence: str = ""
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.criterion.kind is not ContentKind.RUBRIC_CRITERION:
            raise InvariantError("a criterion score must point at a rubric criterion")
        check_marks("criterion weight", self.weight, allow_zero=False)
        if not isinstance(self.credit, Decimal):
            raise InvariantError("credit must be a Decimal")
        check_unit_interval("credit", self.credit)

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
    every criterion scored; it may also name the key, glossary, diagrams and blueprint."""

    id: AnswerScoreId
    college_id: CollegeId
    answer_id: AnswerId
    question: ContentRef
    criterion_scores: tuple[CriterionScore, ...]
    mark_step: Decimal
    mark: Decimal
    content_versions: frozenset[ContentRef]
    created_at: datetime

    def __post_init__(self) -> None:
        if self.question.kind is not ContentKind.QUESTION:
            raise InvariantError("an answer score must point at a question")
        if not self.criterion_scores:
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
        if self.mark != answer_mark(self.criterion_scores, self.mark_step):
            raise InvariantError("mark must equal the rounded sum of criterion marks")
        check_aware("created_at", self.created_at)

    @property
    def scorer_versions(self) -> frozenset[EngineRef]:
        return frozenset(c.scorer for c in self.criterion_scores)
