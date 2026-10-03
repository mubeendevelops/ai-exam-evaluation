"""Runs queued ``booklet.prepare`` jobs: one booklet at a time, one transaction per pipeline
step, so a crash resumes from the last finished step (design.md "Reliability").

Per tick: jobs whose worker vanished on their last attempt are reaped (their booklets end as
FAILED); then the next job is claimed, fairly across colleges. While the booklet is processed
the job's lease is extended after every step. A failure that is not the file's fault is retried
with backoff; when the attempts run out the booklet ends as FAILED. Logs carry ids and counts
only, never names or file contents."""

import time
from dataclasses import dataclass
from uuid import UUID

import structlog

from tarn_adapters.postgres.database import PostgresDatabase, PostgresSession
from tarn_core.errors import NotFoundError
from tarn_core.ids import BookletId
from tarn_core.ports.jobs import JOB_PREPARE_BOOKLET, Job
from tarn_core.ports.pages import PageCleaner, PageSplitter
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services.pipeline import PagePipeline, QualityPolicy


@dataclass
class BookletJobRunner:
    db: PostgresDatabase
    blobs: BlobStore
    splitter: PageSplitter
    cleaner: PageCleaner
    clock: Clock
    ids: IdGenerator
    policy: QualityPolicy
    max_pages: int
    worker: str = "worker"

    def _pipeline(self, session: PostgresSession) -> PagePipeline:
        return PagePipeline(
            booklets=session.booklets,
            blobs=self.blobs,
            splitter=self.splitter,
            cleaner=self.cleaner,
            runtime=session.runtime,
            policy=self.policy,
            max_pages=self.max_pages,
        )

    def run_one(self) -> bool:
        """Process at most one job; True when there was something to do."""
        with self.db.scheduler() as queue:
            reaped = queue.reap()
            job = queue.claim(self.worker)
        for lost in reaped:
            self._abandon(lost)
        if job is None:
            return bool(reaped)
        if job.kind != JOB_PREPARE_BOOKLET:
            with self.db.scheduler() as queue:
                queue.fail(job.id, "unknown job kind", retry=False)
            return True
        self._run(job)
        return True

    def _run(self, job: Job) -> None:
        log = structlog.get_logger("tarn_worker")
        booklet_id = BookletId(UUID(str(job.payload["booklet_id"])))
        started = time.perf_counter()
        steps = 0
        try:
            while True:
                with self.db.session(
                    job.college_id, ids=self.ids, clock=self.clock, blobs=self.blobs
                ) as session:
                    done = self._pipeline(session).step(job.college_id, booklet_id)
                steps += 1
                with self.db.scheduler() as queue:
                    queue.heartbeat(job.id)
                if done:
                    break
        except NotFoundError:
            # The booklet was deleted while it waited: nothing left to do.
            with self.db.scheduler() as queue:
                queue.succeed(job.id)
            log.info("booklet.job_skipped", job=str(job.id), reason="booklet gone")
            return
        except Exception as error:
            with self.db.scheduler() as queue:
                will_retry = queue.fail(job.id, type(error).__name__, retry=True)
            log.error(
                "booklet.job_failed",
                job=str(job.id),
                college=str(job.college_id),
                booklet=str(booklet_id),
                error=type(error).__name__,
                attempt=job.attempts,
                will_retry=will_retry,
            )
            if not will_retry:
                self._abandon(job)
            return
        with self.db.scheduler() as queue:
            queue.succeed(job.id)
        log.info(
            "booklet.job_done",
            job=str(job.id),
            college=str(job.college_id),
            booklet=str(booklet_id),
            steps=steps,
            seconds=round(time.perf_counter() - started, 2),
        )

    def _abandon(self, job: Job) -> None:
        booklet_id = BookletId(UUID(str(job.payload["booklet_id"])))
        try:
            with self.db.session(
                job.college_id, ids=self.ids, clock=self.clock, blobs=self.blobs
            ) as session:
                self._pipeline(session).abandon(job.college_id, booklet_id)
        except NotFoundError:
            pass
        structlog.get_logger("tarn_worker").error(
            "booklet.abandoned", job=str(job.id), college=str(job.college_id)
        )
