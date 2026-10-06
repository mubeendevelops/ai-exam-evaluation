"""Scoring a segmented booklet (``segmented`` → ``scored``) in one step: a booklet has a few
dozen answers and the embedder runs on the CPU, so it fits one transaction. Safe to repeat: a
booklet past ``segmented`` is left alone.

Each answer with segments gets a score (an answer emptied by a teacher's edit is not attempted);
best N, OR and negative marking are applied over the scores at booklet level by
``TotalsService``. Audit: ``answer.scored`` per answer and ``booklet.scored`` with counts only.
``rescore`` re-scores the answers a teacher's segment edit touched (D92)."""

from collections.abc import Sequence

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import AnswerStatus, BookletStatus
from tarn_core.domain.common import JsonValue
from tarn_core.domain.scoring import AnswerFlag, AnswerScore
from tarn_core.errors import DomainError
from tarn_core.ids import AnswerId, BookletId, CollegeId, UserId
from tarn_core.ports.jobs import JOB_SCORE_BOOKLET, JobQueue
from tarn_core.ports.repositories import BookletRepository
from tarn_core.services._support import Runtime
from tarn_core.services.scoring.service import ScoringService

FAILED_SCORING = "scoring_failed"


class AnswerApprovedError(DomainError):
    """An approved answer is not re-scored (amendments arrive in P15)."""


def queue_scoring(jobs: JobQueue, college_id: CollegeId, booklet_id: BookletId) -> None:
    """Queue the scoring job of a segmented booklet (idempotent per booklet)."""
    jobs.enqueue(
        college_id,
        JOB_SCORE_BOOKLET,
        {"booklet_id": str(booklet_id)},
        key=f"{JOB_SCORE_BOOKLET}:{booklet_id}",
    )


class BookletScorer:
    def __init__(
        self, *, booklets: BookletRepository, scoring: ScoringService, runtime: Runtime
    ) -> None:
        self._booklets = booklets
        self._scoring = scoring
        self._rt = runtime

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        """Score every attempted answer; True (nothing is left to do afterwards)."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status is not BookletStatus.SEGMENTED:
            return True
        scores = [
            self._scoring.score_answer(college_id, None, answer.id)
            for answer in self._booklets.answers(college_id, booklet_id)
            if answer.segment_ids
        ]
        self._booklets.save(
            college_id,
            booklet.moved_to(BookletStatus.SCORED),
        )
        self._rt.record(
            college_id,
            None,
            AuditAction.BOOKLET_SCORED,
            booklet_id=booklet_id,
            after=_counts(scores),
        )
        return True

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """The job ran out of attempts: end the booklet as FAILED so it stops waiting."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status is not BookletStatus.SEGMENTED:
            return
        self._booklets.save(
            college_id,
            booklet.moved_to(BookletStatus.FAILED, failure_reason=FAILED_SCORING),
        )
        self._rt.record(
            college_id,
            None,
            AuditAction.BOOKLET_FAILED,
            booklet_id=booklet_id,
            after={"reason": FAILED_SCORING},
        )

    def rescore(
        self, college_id: CollegeId, actor_id: UserId | None, answer_ids: Sequence[AnswerId]
    ) -> list[AnswerScore]:
        """New suggestions for these answers only; an answer left without segments is not
        attempted and gets none. Approved answers are refused."""
        answers = [self._booklets.get_answer(college_id, a) for a in answer_ids]
        if any(a.status is AnswerStatus.APPROVED for a in answers):
            raise AnswerApprovedError("an approved answer changes only through an amendment")
        return [
            self._scoring.score_answer(college_id, actor_id, a.id) for a in answers if a.segment_ids
        ]


def _counts(scores: Sequence[AnswerScore]) -> dict[str, JsonValue]:
    counts: dict[str, JsonValue] = {"answers": len(scores)}
    for flag in AnswerFlag:
        counts[flag.value] = sum(1 for s in scores if flag in s.flags)
    counts["borderline"] = sum(1 for s in scores for c in s.criterion_scores if "check" in c.flags)
    embedders = {s.embedder for s in scores if s.embedder is not None}
    counts["embedder"] = (
        None if not embedders else ", ".join(sorted(f"{e.name} {e.version}" for e in embedders))
    )
    return counts
