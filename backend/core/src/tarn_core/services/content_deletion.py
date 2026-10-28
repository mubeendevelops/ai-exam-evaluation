"""Deleting a question or a paper for good (P28, D166; overrides D35 for organisation content).

An SME entitled to the subject may delete, but only once no unfrozen work uses the item:
- a **paper** is refused while a unit that is not frozen uses it, or a booklet of such a unit
  is pinned to any of its versions;
- a **question** is refused while the latest version of any paper links it (the paper would
  point at nothing: unlink it or delete the paper first), or a unit that is not frozen uses a
  paper version linking it (its own paper, or the version a booklet is pinned to), or a
  booklet of such a unit has a score for it.

Every version goes, with the question's parts (reference answers, rubric criteria, glossaries,
reference diagrams, key files) and their files. What stays: the frozen records and the issued
result sheet PDFs (they live under the booklet), the score rows (they name content by id and
version without a foreign key, D171), and a content-free ``content.deleted`` audit event. The
database refuses the same deletions (``tarn_delete_question`` / ``tarn_delete_paper``, 0016)."""

from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.common import BlobKey
from tarn_core.domain.content import KeyFile, Question, ReferenceDiagram
from tarn_core.domain.unit import Unit, UnitState
from tarn_core.errors import IllegalTransitionError
from tarn_core.ids import BlueprintId, CollegeId, QuestionId, UserId
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    EntitlementRepository,
    ScoreRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.content import ensure_author


@dataclass(frozen=True, slots=True, kw_only=True)
class DeletionCheck:
    blockers: tuple[str, ...]
    """Why the item cannot be deleted yet, one sentence each; empty = it can."""

    @property
    def allowed(self) -> bool:
        return not self.blockers


def _links(paper: ExamBlueprint, question_id: QuestionId) -> bool:
    return any(qid == question_id for slot in paper.slots() for _, qid, _ in leaves(slot))


def _unit_words(unit: Unit) -> str:
    state = "open" if unit.state is UnitState.OPEN else "submitted, not yet frozen"
    return f"Unit {unit.code} ({state}) uses it."


class ContentDeletion:
    def __init__(
        self,
        *,
        content: ContentRepository,
        units: UnitRepository,
        booklets: BookletRepository,
        scores: ScoreRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        blobs: BlobStore,
        runtime: Runtime,
    ) -> None:
        self._content = content
        self._units = units
        self._booklets = booklets
        self._scores = scores
        self._users = users
        self._entitlements = entitlements
        self._blobs = blobs
        self._rt = runtime

    def _unfrozen(self, college_id: CollegeId) -> Sequence[Unit]:
        return [u for u in self._units.list(college_id) if u.state is not UnitState.FROZEN]

    # --- papers ------------------------------------------------------------------------------

    def check_paper(
        self, college_id: CollegeId, actor_id: UserId, paper_id: BlueprintId
    ) -> DeletionCheck:
        paper = self._content.get(college_id, ExamBlueprint, paper_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, paper.subject_id)
        blockers = []
        for unit in self._unfrozen(college_id):
            pinned = any(
                b.blueprint.id == paper_id for b in self._booklets.in_unit(college_id, unit.id)
            )
            if unit.paper_id == paper_id or pinned:
                blockers.append(_unit_words(unit))
        return DeletionCheck(blockers=tuple(blockers))

    def delete_paper(self, college_id: CollegeId, actor_id: UserId, paper_id: BlueprintId) -> int:
        """Raises ``IllegalTransitionError`` (409) with the blockers; the rows removed."""
        check = self.check_paper(college_id, actor_id, paper_id)
        if not check.allowed:
            raise IllegalTransitionError(" ".join(check.blockers))
        rows = self._content.delete_blueprint(college_id, paper_id)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_DELETED,
            before={"kind": "blueprint", "id": str(paper_id), "versions": rows},
            after=None,
        )
        return rows

    # --- questions ---------------------------------------------------------------------------

    def check_question(
        self, college_id: CollegeId, actor_id: UserId, question_id: QuestionId
    ) -> DeletionCheck:
        question = self._content.get(college_id, Question, question_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, question.subject_id)
        blockers = [
            f"The paper {p.title!r} links it: unlink it there, or delete the paper first."
            for p in sorted(
                self._content.latest(college_id, ExamBlueprint), key=lambda p: p.title.casefold()
            )
            if _links(p, question_id)
        ]
        for unit in self._unfrozen(college_id):
            if self._unit_uses(college_id, unit, question_id):
                blockers.append(_unit_words(unit))
        return DeletionCheck(blockers=tuple(blockers))

    def _unit_uses(self, college_id: CollegeId, unit: Unit, question_id: QuestionId) -> bool:
        booklets = self._booklets.in_unit(college_id, unit.id)
        pinned = {(b.blueprint.id, b.blueprint.version) for b in booklets}
        papers = [self._content.get(college_id, ExamBlueprint, unit.paper_id)]
        papers += [self._content.get(college_id, ExamBlueprint, pid, v) for pid, v in pinned]
        if any(_links(p, question_id) for p in papers):
            return True
        return any(
            score.question.id == question_id
            for booklet in booklets
            for answer in self._booklets.answers(college_id, booklet.id)
            for score in self._scores.scores(college_id, answer.id)
        )

    def delete_question(
        self, college_id: CollegeId, actor_id: UserId, question_id: QuestionId
    ) -> int:
        """Raises ``IllegalTransitionError`` (409) with the blockers; the rows removed. The
        files of its key files and reference diagrams are deleted too (legacy ``global/``
        files cannot be deleted from a college session and are left, D173)."""
        check = self.check_question(college_id, actor_id, question_id)
        if not check.allowed:
            raise IllegalTransitionError(" ".join(check.blockers))
        files: dict[str, BlobKey] = {}
        for part in self._content.question_parts(college_id, question_id):
            if isinstance(part, KeyFile):
                files[part.blob.value] = part.blob
            elif isinstance(part, ReferenceDiagram):
                files[part.png.value] = part.png
        rows = self._content.delete_question(college_id, question_id)
        removed = 0
        for key in files.values():
            if key.college_id == college_id:
                self._blobs.delete(key)
                removed += 1
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_DELETED,
            before={"kind": "question", "id": str(question_id), "rows": rows, "files": removed},
            after=None,
        )
        return rows


__all__ = ["ContentDeletion", "DeletionCheck"]
