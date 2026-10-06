"""Asking for new suggestions, and finding answers whose content changed (D110, D111).

A teacher's edit (OCR text, segments, a drawing's graph) re-scores only the answers it
touched; a changed key, rubric, glossary or reference diagram re-scores the unapproved answers
that were scored against the old version. Approved answers are never re-scored: they change
only through an amendment.

``RescoreRequests`` marks the answers ``rescore_pending`` (one version on, so a screen showing
the old suggestion is stale and approval waits for the new one) and hands them to a
``Rescorer``: the worker's queue in the API, the scorer itself in tests and the worker."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import Answer, AnswerStatus, Booklet
from tarn_core.errors import DomainError
from tarn_core.ids import AnswerId, CollegeId, UserId
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services._support import Runtime
from tarn_core.services.diagrams.service import Rescorer
from tarn_core.services.scoring.booklet import AnswerApprovedError
from tarn_core.services.scoring.changes import content_changes
from tarn_core.services.scoring.service import ScoringContent, scoring_content

TEACHER_EDIT = "teacher_edit"
CONTENT_CHANGED = "content_changed"


class RescoreRequests:
    def __init__(self, *, booklets: BookletRepository, runtime: Runtime, rescorer: Rescorer):
        self._booklets = booklets
        self._rt = runtime
        self._rescorer = rescorer

    def request(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        answer_ids: Sequence[AnswerId],
        *,
        reason: str = TEACHER_EDIT,
    ) -> tuple[Answer, ...]:
        """Re-score these answers (those without segments are not attempted and are left
        out). Raises ``AnswerApprovedError`` if one is approved. Returns the answers as they
        stand afterwards (already re-scored when the rescorer works synchronously)."""
        answers = [self._booklets.get_answer(college_id, a) for a in dict.fromkeys(answer_ids)]
        if any(a.status is AnswerStatus.APPROVED for a in answers):
            raise AnswerApprovedError("an approved answer changes only through an amendment")
        wanted = [a for a in answers if a.segment_ids]
        for answer in wanted:
            pending = replace(answer, rescore_pending=True, version=answer.version + 1)
            self._booklets.save_answer(college_id, pending)
            self._rt.record(
                college_id,
                actor_id,
                AuditAction.ANSWER_RESCORE_REQUESTED,
                booklet_id=answer.booklet_id,
                answer_id=answer.id,
                before={"version": answer.version, "rescore_pending": answer.rescore_pending},
                after={"version": pending.version, "rescore_pending": True, "reason": reason},
            )
        if wanted:
            self._rescorer.rescore(college_id, actor_id, [a.id for a in wanted])
        return tuple(self._booklets.get_answer(college_id, a.id) for a in wanted)

    def rescore(
        self, college_id: CollegeId, actor_id: UserId | None, answer_ids: Sequence[AnswerId]
    ) -> tuple[Answer, ...]:
        """The ``Rescorer`` protocol (a corrected student drawing)."""
        if actor_id is None:
            raise DomainError("a teacher's edit names the teacher")
        return self.request(college_id, actor_id, answer_ids)


def clear_pending(
    booklets: BookletRepository, college_id: CollegeId, answer_ids: Sequence[AnswerId]
) -> None:
    """The re-score was given up (the job ran out of attempts): the answers keep their last
    suggestion and can be decided again."""
    for answer_id in answer_ids:
        try:
            answer = booklets.get_answer(college_id, answer_id)
        except DomainError:
            continue
        if answer.rescore_pending:
            booklets.save_answer(
                college_id, replace(answer, rescore_pending=False, version=answer.version + 1)
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class StaleAnswer:
    answer_id: AnswerId
    changes: tuple[str, ...]
    """What changed, e.g. ``("rubric criterion v2 → v3",)``."""


def stale_answers(
    booklets: BookletRepository,
    scores: ScoreRepository,
    content: ContentRepository,
    booklet: Booklet,
) -> list[StaleAnswer]:
    """Unapproved, scored answers whose latest score used content that has changed since.
    Answers already waiting for a re-score are left out."""
    college_id = booklet.college_id
    loaded: dict[str, ScoringContent | None] = {}
    found: list[StaleAnswer] = []
    for answer in booklets.answers(college_id, booklet.id):
        if (
            answer.status is AnswerStatus.APPROVED
            or answer.rescore_pending
            or not answer.segment_ids
        ):
            continue
        history = scores.scores(college_id, answer.id)
        if not history:
            continue
        if answer.slot_label not in loaded:
            try:
                loaded[answer.slot_label] = scoring_content(
                    content, booklet.blueprint, answer.slot_label
                )
            except DomainError:
                loaded[answer.slot_label] = None  # the scorer would fail the same way
        now = loaded[answer.slot_label]
        if now is None:
            continue
        changes = content_changes(history[-1].content_versions, now.used)
        if changes:
            found.append(StaleAnswer(answer_id=answer.id, changes=changes))
    return found
