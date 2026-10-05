"""Runs queued booklet jobs: ``booklet.prepare`` (split, clean, gate; P9), ``booklet.read``
(best-of-N OCR, one page per step; P10), ``booklet.segment`` (answers per question, one
step; P12) and ``booklet.score`` (a suggested mark per answer, one step; P13). One booklet
at a time, one transaction per step, so a crash resumes from the last finished step
(design.md "Reliability").

Per tick: jobs whose worker vanished on their last attempt are reaped (their booklets end as
FAILED); then the next job is claimed, fairly across colleges. While the booklet is processed
the job's lease is extended after every step, and by a heartbeat thread while a slow step
(OCR on the CPU) runs. A failure that is not the file's fault is retried
with backoff; when the attempts run out the booklet ends as FAILED. Logs carry ids and counts
only, never names or file contents."""

import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

import structlog

from tarn_adapters.postgres.database import PostgresDatabase, PostgresSession
from tarn_core.domain.ocr import SelectorSettings
from tarn_core.errors import NotFoundError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.engines import Embedder, LayoutDetector, OcrEngine, PageTransform, WordList
from tarn_core.ports.jobs import (
    JOB_PREPARE_BOOKLET,
    JOB_READ_BOOKLET,
    JOB_SCORE_BOOKLET,
    JOB_SEGMENT_BOOKLET,
    Job,
)
from tarn_core.ports.pages import PageCleaner, PageSplitter
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services.ocr.reader import BookletReader, OrientationPolicy, abandon_reading
from tarn_core.services.pipeline import PagePipeline, QualityPolicy
from tarn_core.services.scoring import BookletScorer, ScoringService
from tarn_core.services.segmentation.segmenter import SegmentationPolicy
from tarn_core.services.segmentation.service import BookletSegmenter
from tarn_core.services.segmentation.similarity import TrigramEmbedder


@dataclass(frozen=True)
class OcrKit:
    """What the reading job needs besides the session: built once per worker process."""

    layout: LayoutDetector
    engines: Mapping[str, OcrEngine]
    transform: PageTransform
    word_list: WordList | None
    settings: SelectorSettings
    orientation: OrientationPolicy


class _Stepper(Protocol):
    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool: ...

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None: ...


class OcrUnavailableError(RuntimeError):
    """This worker could not set up OCR (see the ``ocr.unavailable`` log line)."""


@dataclass
class _NoOcr:
    """A reading job on a worker without OCR: every attempt fails (and is retried with
    backoff, so a fixed worker can still take it); out of attempts, the booklet fails."""

    session: PostgresSession

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        raise OcrUnavailableError("OCR is not available on this worker")

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        abandon_reading(self.session.booklets, self.session.runtime, college_id, booklet_id)


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
    ocr: OcrKit | None = None
    """None: OCR could not be set up; reading jobs fail (with retries) until a worker with OCR
    takes them, and in the end their booklets fail as ``reading_failed``."""
    heartbeat_seconds: float = 40.0
    embedder: Embedder = field(default_factory=TrigramEmbedder)
    segmentation: SegmentationPolicy = field(default_factory=SegmentationPolicy)
    scoring_embedder: Embedder = field(default_factory=TrigramEmbedder)
    """Local only (``build_scoring_embedder``): scoring sends no text off the machine."""
    word_list: WordList | None = None

    def _pipeline(self, session: PostgresSession) -> PagePipeline:
        return PagePipeline(
            booklets=session.booklets,
            blobs=self.blobs,
            splitter=self.splitter,
            cleaner=self.cleaner,
            runtime=session.runtime,
            jobs=session.jobs,
            policy=self.policy,
            max_pages=self.max_pages,
        )

    def _reader(self, session: PostgresSession) -> BookletReader:
        if self.ocr is None:  # pragma: no cover  (_stepper hands out _NoOcr instead)
            raise OcrUnavailableError("this worker has no OCR engines")
        return BookletReader(
            booklets=session.booklets,
            content=session.content,
            blobs=self.blobs,
            layout=self.ocr.layout,
            engines=self.ocr.engines,
            transform=self.ocr.transform,
            calibrations=session.calibrations,
            word_list=self.ocr.word_list,
            runtime=session.runtime,
            settings=self.ocr.settings,
            orientation=self.ocr.orientation,
            jobs=session.jobs,
        )

    def _segmenter(self, session: PostgresSession) -> BookletSegmenter:
        return BookletSegmenter(
            booklets=session.booklets,
            content=session.content,
            embedder=self.embedder,
            runtime=session.runtime,
            jobs=session.jobs,
            policy=self.segmentation,
        )

    def _scorer(self, session: PostgresSession) -> BookletScorer:
        scoring = ScoringService.standard(
            booklets=session.booklets,
            scores=session.scores,
            content=session.content,
            runtime=session.runtime,
            embedder=self.scoring_embedder,
            calibrations=session.scoring_calibrations,
            word_list=self.word_list,
        )
        return BookletScorer(booklets=session.booklets, scoring=scoring, runtime=session.runtime)

    def _stepper(self, kind: str) -> Callable[[PostgresSession], _Stepper] | None:
        if kind == JOB_PREPARE_BOOKLET:
            return self._pipeline
        if kind == JOB_READ_BOOKLET:
            return self._reader if self.ocr is not None else _NoOcr
        if kind == JOB_SEGMENT_BOOKLET:
            return self._segmenter
        if kind == JOB_SCORE_BOOKLET:
            return self._scorer
        return None

    def run_one(self) -> bool:
        """Process at most one job; True when there was something to do."""
        with self.db.scheduler() as queue:
            reaped = queue.reap()
            job = queue.claim(self.worker)
        for lost in reaped:
            lost_stepper = self._stepper(lost.kind)
            if lost_stepper is not None:
                self._abandon(lost, lost_stepper)
        if job is None:
            return bool(reaped)
        stepper = self._stepper(job.kind)
        if stepper is None:
            with self.db.scheduler() as queue:
                queue.fail(job.id, "unknown job kind", retry=False)
            return True
        self._run(job, stepper)
        return True

    @contextmanager
    def _keep_alive(self, job: Job) -> Iterator[None]:
        """Extend the lease while a long step runs (a page of OCR on the CPU can take
        longer than the lease)."""
        stop = threading.Event()

        def beat() -> None:
            while not stop.wait(self.heartbeat_seconds):
                try:
                    with self.db.scheduler() as queue:
                        queue.heartbeat(job.id)
                except Exception as error:  # the step's own error handling decides
                    structlog.get_logger("tarn_worker").warning(
                        "booklet.heartbeat_failed", job=str(job.id), error=type(error).__name__
                    )

        thread = threading.Thread(target=beat, name=f"heartbeat-{job.id}", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join()

    def _run(self, job: Job, stepper: Callable[[PostgresSession], _Stepper]) -> None:
        log = structlog.get_logger("tarn_worker")
        booklet_id = BookletId(UUID(str(job.payload["booklet_id"])))
        started = time.perf_counter()
        steps = 0
        try:
            while True:
                with (
                    self._keep_alive(job),
                    self.db.session(
                        job.college_id, ids=self.ids, clock=self.clock, blobs=self.blobs
                    ) as session,
                ):
                    done = stepper(session).step(job.college_id, booklet_id)
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
                self._abandon(job, stepper)
            return
        with self.db.scheduler() as queue:
            queue.succeed(job.id)
        log.info(
            "booklet.job_done",
            job=str(job.id),
            kind=job.kind,
            college=str(job.college_id),
            booklet=str(booklet_id),
            steps=steps,
            seconds=round(time.perf_counter() - started, 2),
        )

    def _abandon(self, job: Job, stepper: Callable[[PostgresSession], _Stepper]) -> None:
        booklet_id = BookletId(UUID(str(job.payload["booklet_id"])))
        try:
            with self.db.session(
                job.college_id, ids=self.ids, clock=self.clock, blobs=self.blobs
            ) as session:
                stepper(session).abandon(job.college_id, booklet_id)
        except NotFoundError:
            pass
        structlog.get_logger("tarn_worker").error(
            "booklet.abandoned", job=str(job.id), college=str(job.college_id)
        )
