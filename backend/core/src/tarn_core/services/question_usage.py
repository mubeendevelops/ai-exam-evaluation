"""Where a question is used (P35, D223): the papers whose newest version links it, and the open
units of those papers, so the editor can warn before a change reaches them.

Editing a question saves a new version that every paper linking it picks up; unapproved answers
re-score when their booklet is next opened (D111), approved answers and frozen records keep
their versions. Only papers of the actor's own subjects are listed (D178): another subject's
paper is never shown, and another college's content is not found."""

from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.content import Question
from tarn_core.domain.unit import UnitState
from tarn_core.ids import BlueprintId, CollegeId, QuestionId, UnitId, UserId
from tarn_core.ports.repositories import (
    ContentRepository,
    EntitlementRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services.content import ensure_author


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitUse:
    unit_id: UnitId
    code: str
    year: str


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperUse:
    paper_id: BlueprintId
    title: str
    exam_code: str
    open_units: tuple[UnitUse, ...]
    """Units of this paper that are open (marking in progress); oldest first."""


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionUsage:
    papers: tuple[PaperUse, ...]
    """Papers (newest version) that link the question, by title."""


class QuestionUsageService:
    def __init__(
        self,
        *,
        content: ContentRepository,
        units: UnitRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
    ) -> None:
        self._content = content
        self._units = units
        self._users = users
        self._entitlements = entitlements

    def usage(
        self, college_id: CollegeId, actor_id: UserId, question_id: QuestionId
    ) -> QuestionUsage:
        question = self._content.get(college_id, Question, question_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, question.subject_id)
        mine = self._entitlements.subjects_of(college_id, actor_id)
        papers = sorted(
            (
                p
                for p in self._content.latest(college_id, ExamBlueprint)
                if p.subject_id in mine
                and any(qid == question_id for slot in p.slots() for _, qid, _ in leaves(slot))
            ),
            key=lambda p: (p.title.casefold(), str(p.id)),
        )
        open_units = self._units.list(college_id, state=UnitState.OPEN)
        found: Sequence[PaperUse] = [
            PaperUse(
                paper_id=p.id,
                title=p.title,
                exam_code=p.exam_code,
                open_units=tuple(
                    UnitUse(unit_id=u.id, code=u.code, year=u.year)
                    for u in open_units
                    if u.paper_id == p.id
                ),
            )
            for p in papers
        ]
        return QuestionUsage(papers=tuple(found))


__all__ = ["PaperUse", "QuestionUsage", "QuestionUsageService", "UnitUse"]
