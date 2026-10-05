"""Totals for one booklet: the teacher's mark where approved, else the latest AI mark,
through negative marking and the blueprint's choice rules. An answer without segments (emptied
by a segment edit, kept for its score history) counts as not attempted."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import AnswerStatus
from tarn_core.ids import AnswerId, BookletId, CollegeId
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services.marking import ExamResult, apply_choice_rules, slot_mark


@dataclass(frozen=True, slots=True, kw_only=True)
class TotalsPreview:
    result: ExamResult
    unmarked: tuple[AnswerId, ...]
    """Answers with neither an approval nor an AI mark (e.g. "mark manually": guidance-only
    keys)."""


class TotalsService:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
    ):
        self._booklets = booklets
        self._scores = scores
        self._content = content

    def preview(self, college_id: CollegeId, booklet_id: BookletId) -> TotalsPreview:
        booklet = self._booklets.get(college_id, booklet_id)
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )

        leaf_marks: dict[str, Decimal] = {}
        unmarked: list[AnswerId] = []
        for answer in self._booklets.answers(college_id, booklet_id):
            if not answer.segment_ids:
                continue  # emptied by a teacher's segment edit: not attempted
            reviews = self._scores.reviews(college_id, answer.id)
            scores = self._scores.scores(college_id, answer.id)
            if answer.status is AnswerStatus.APPROVED and reviews:
                leaf_marks[answer.slot_label] = reviews[-1].teacher_mark
            elif scores and scores[-1].mark is not None:
                leaf_marks[answer.slot_label] = scores[-1].mark
            else:
                unmarked.append(answer.id)

        slot_marks: dict[str, Decimal] = {}
        attempted: defaultdict[str, Decimal] = defaultdict(Decimal)
        for slot in blueprint.slots():
            for label, _, _ in leaves(slot):
                if label in leaf_marks:
                    attempted[slot.label] += leaf_marks[label]
            if slot.label in attempted:
                slot_marks[slot.label] = slot_mark(slot, attempted[slot.label])

        return TotalsPreview(
            result=apply_choice_rules(blueprint, slot_marks), unmarked=tuple(unmarked)
        )
