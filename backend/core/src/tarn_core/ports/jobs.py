"""The job queue port: run work later, retry with backoff, one college's batch never starving
another's. Jobs belong to one college and carry ids only, never content.

Workers ``claim`` a job, keep it alive with ``heartbeat`` while they work, and report ``succeed``
or ``fail``. A job whose worker stops heartbeating (a crash) is handed out again once its lease
has run out, until its attempts are used up."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from tarn_core.domain.common import JsonValue
from tarn_core.ids import CollegeId, JobId

JOB_PREPARE_BOOKLET = "booklet.prepare"
"""Split the upload into pages, clean them, run the quality gate (P9)."""

JOB_READ_BOOKLET = "booklet.read"
"""Read the cleaned pages with every OCR engine and keep the best reading per line (P10)."""

JOB_SEGMENT_BOOKLET = "booklet.segment"
"""Split the read booklet into answers per question (P12)."""

JOB_DIAGRAMS_BOOKLET = "booklet.diagrams"
"""Recognise the diagrams drawn in answers to questions with diagram criteria (P14); queues
scoring when done."""

JOB_SCORE_BOOKLET = "booklet.score"
"""Suggest a mark for every answer of the segmented booklet (P13)."""

JOB_RESCORE_ANSWERS = "answers.rescore"
"""Re-score some answers of a booklet after a teacher's edit made in the API, which loads no
models (P14: a corrected student diagram). Payload: booklet, answers, the teacher."""

JOB_RECOGNIZE_REFERENCE = "diagram.reference"
"""Read an uploaded reference diagram PNG into its graph (P14). Payload: the diagram's id and
the uploading teacher; queued in the uploader's college."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Job:
    id: JobId
    college_id: CollegeId
    kind: str
    payload: Mapping[str, JsonValue]
    attempts: int
    """Runs started so far, including this one."""
    max_attempts: int

    @property
    def last_attempt(self) -> bool:
        return self.attempts >= self.max_attempts


class JobQueue(Protocol):
    def enqueue(
        self,
        college_id: CollegeId,
        kind: str,
        payload: Mapping[str, JsonValue],
        *,
        key: str | None = None,
    ) -> JobId:
        """Queue a job for one college. With a ``key``, a job of that college with the same key
        that is still queued or running is returned instead of a second one (idempotent)."""
        ...

    def claim(self, worker: str) -> Job | None:
        """The next job to run, or None. Colleges are served in turn (the one served least
        recently first), and at most the queue's concurrency limit run at once (one: booklets
        are processed one at a time, C30)."""
        ...

    def reap(self) -> list[Job]:
        """Jobs whose worker stopped heartbeating on their last attempt: they are finished as
        failed and returned, so the caller can end whatever they were working on."""
        ...

    def heartbeat(self, job_id: JobId) -> None:
        """Extend the lease of a running job."""
        ...

    def succeed(self, job_id: JobId) -> None: ...

    def fail(self, job_id: JobId, error: str, *, retry: bool) -> bool:
        """End this run. With ``retry`` and attempts left the job is queued again after a
        backoff and True is returned; otherwise it is finished as failed and False is returned.
        ``error`` is a short code or class name, never student data."""
        ...
