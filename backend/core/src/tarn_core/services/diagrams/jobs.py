"""Queueing the diagram jobs (kept apart so the pipeline stages can import it without the
diagram services)."""

from collections.abc import Sequence

from tarn_core.domain.content import ReferenceDiagram
from tarn_core.ids import AnswerId, BookletId, CollegeId, UserId
from tarn_core.ports.jobs import (
    JOB_DIAGRAMS_BOOKLET,
    JOB_RECOGNIZE_REFERENCE,
    JOB_RESCORE_ANSWERS,
    JobQueue,
)


def queue_reference_recognition(
    jobs: JobQueue, college_id: CollegeId, actor_id: UserId, diagram: ReferenceDiagram
) -> None:
    """Queue reading an uploaded reference PNG (idempotent per diagram)."""
    jobs.enqueue(
        college_id,
        JOB_RECOGNIZE_REFERENCE,
        {"reference_diagram_id": str(diagram.id), "actor_id": str(actor_id)},
        key=f"{JOB_RECOGNIZE_REFERENCE}:{diagram.id}",
    )


def queue_diagrams(jobs: JobQueue, college_id: CollegeId, booklet_id: BookletId) -> None:
    """Queue the diagram stage of a segmented booklet (idempotent per booklet)."""
    jobs.enqueue(
        college_id,
        JOB_DIAGRAMS_BOOKLET,
        {"booklet_id": str(booklet_id)},
        key=f"{JOB_DIAGRAMS_BOOKLET}:{booklet_id}",
    )


class QueuedRescore:
    """Re-scores later, in the worker (where the scoring models are loaded): the API's
    ``Rescorer``."""

    def __init__(self, jobs: JobQueue, booklet_id: BookletId) -> None:
        self._jobs = jobs
        self._booklet_id = booklet_id

    def rescore(
        self, college_id: CollegeId, actor_id: UserId | None, answer_ids: Sequence[AnswerId]
    ) -> None:
        if not answer_ids:
            return
        self._jobs.enqueue(
            college_id,
            JOB_RESCORE_ANSWERS,
            {
                "booklet_id": str(self._booklet_id),
                "answer_ids": [str(a) for a in answer_ids],
                "actor_id": None if actor_id is None else str(actor_id),
            },
        )
