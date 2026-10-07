"""Runs queued booklet jobs: ``booklet.prepare`` (split, clean, gate; P9), ``booklet.read``
(best-of-N OCR, one page per step; P10), ``booklet.segment`` (answers per question, one
step; P12), ``booklet.diagrams`` (the drawings in answers to diagram questions, one per step;
P14) and ``booklet.score`` (a suggested mark per answer, one step; P13). One booklet at a
time, one transaction per step, so a crash resumes from the last finished step (design.md
"Reliability"). Two jobs are not a booklet's stage: ``diagram.reference`` reads an uploaded
reference diagram, ``answers.rescore`` re-scores answers after a teacher's edit in the API
(which loads no models), ``booklet.resegment`` segments a reviewed booklet again on the teacher's
request; each runs in one transaction.

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
from uuid import UUID

import structlog

from tarn_adapters.postgres.database import PostgresDatabase, PostgresSession
from tarn_adapters.stages import OcrKit, OcrUnavailableError, Stages, Stepper
from tarn_core.domain.diagram import DiagramKind
from tarn_core.errors import NotFoundError
from tarn_core.ids import AnswerId, BookletId, ReferenceDiagramId, UserId
from tarn_core.ports.engines import (
    DiagramRecognizer,
    Embedder,
    SecondOpinionScorer,
    WordList,
)
from tarn_core.ports.jobs import (
    JOB_RECOGNIZE_REFERENCE,
    JOB_RESCORE_ANSWERS,
    JOB_RESEGMENT_BOOKLET,
    Job,
)
from tarn_core.ports.pages import PageCleaner, PageSplitter
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services.diagrams.service import ReferenceDiagrams
from tarn_core.services.ocr.reader import abandon_reading  # noqa: F401  (re-exported for tests)
from tarn_core.services.pipeline import QualityPolicy
from tarn_core.services.scoring import BookletScorer
from tarn_core.services.segmentation.resegment import BookletResegmenter
from tarn_core.services.segmentation.segmenter import SegmentationPolicy
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.services.workflow import clear_pending

__all__ = ["BookletJobRunner", "OcrKit", "OcrUnavailableError"]


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
    """Local only (``build_scoring_embedder``): the embedder sends no text off the machine."""
    llm: SecondOpinionScorer | None = None
    """The LLM scorer (P19), None unless switched on in the settings. It is asked only for the
    colleges whose ``llm_scoring`` flag is on; for them answer text goes to the provider."""
    word_list: WordList | None = None
    recognizers: Mapping[DiagramKind, DiagramRecognizer] = field(default_factory=dict)
    """Diagram recognizers per kind (empty: drawings are stored without a graph and their
    criteria go to the teacher)."""

    def stages(self) -> Stages:
        """The stage wiring shared with ``tarn evaluate`` (``tarn_adapters.stages``)."""
        return Stages(
            blobs=self.blobs,
            splitter=self.splitter,
            cleaner=self.cleaner,
            policy=self.policy,
            max_pages=self.max_pages,
            ocr=self.ocr,
            embedder=self.embedder,
            segmentation=self.segmentation,
            scoring_embedder=self.scoring_embedder,
            llm=self.llm,
            word_list=self.word_list,
            recognizers=self.recognizers,
        )

    def _scorer(self, session: PostgresSession) -> BookletScorer:
        return self.stages().scorer(session)

    def _resegmenter(self, session: PostgresSession) -> BookletResegmenter:
        return self.stages().resegmenter(session)

    def _references(self, session: PostgresSession) -> ReferenceDiagrams:
        return self.stages().references(session)

    def _single(self, job: Job) -> None:
        """A job that is not a booklet stage: one transaction, retried with backoff."""
        log = structlog.get_logger("tarn_worker")
        started = time.perf_counter()
        try:
            with (
                self._keep_alive(job),
                self.db.session(
                    job.college_id, ids=self.ids, clock=self.clock, blobs=self.blobs
                ) as session,
            ):
                if job.kind == JOB_RESEGMENT_BOOKLET:
                    self._resegmenter(session).run(
                        job.college_id,
                        UserId(UUID(str(job.payload["actor_id"]))),
                        BookletId(UUID(str(job.payload["booklet_id"]))),
                        expected_version=int(str(job.payload["expected_version"])),
                    )
                elif job.kind == JOB_RECOGNIZE_REFERENCE:
                    self._references(session).recognize(
                        job.college_id,
                        UserId(UUID(str(job.payload["actor_id"]))),
                        ReferenceDiagramId(UUID(str(job.payload["reference_diagram_id"]))),
                    )
                else:
                    answers = job.payload["answer_ids"]
                    actor = job.payload.get("actor_id")
                    self._scorer(session).rescore(
                        job.college_id,
                        None if actor is None else UserId(UUID(str(actor))),
                        [AnswerId(UUID(str(a))) for a in answers]
                        if isinstance(answers, list)
                        else [],
                    )
        except NotFoundError:
            with self.db.scheduler() as queue:
                queue.succeed(job.id)
            log.info("job.skipped", job=str(job.id), kind=job.kind, reason="gone")
            return
        except Exception as error:
            with self.db.scheduler() as queue:
                will_retry = queue.fail(job.id, type(error).__name__, retry=True)
            if not will_retry and job.kind == JOB_RESCORE_ANSWERS:
                self._give_up_rescore(job)
            log.error(
                "job.failed",
                job=str(job.id),
                kind=job.kind,
                college=str(job.college_id),
                error=type(error).__name__,
                will_retry=will_retry,
            )
            return
        with self.db.scheduler() as queue:
            queue.succeed(job.id)
        log.info(
            "job.done",
            job=str(job.id),
            kind=job.kind,
            college=str(job.college_id),
            seconds=round(time.perf_counter() - started, 2),
        )

    def _give_up_rescore(self, job: Job) -> None:
        """The answers keep their last suggestion and can be decided again (P15)."""
        answers = job.payload.get("answer_ids")
        if not isinstance(answers, list):
            return
        with self.db.session(
            job.college_id, ids=self.ids, clock=self.clock, blobs=self.blobs
        ) as session:
            clear_pending(
                session.booklets, job.college_id, [AnswerId(UUID(str(a))) for a in answers]
            )

    def _stepper(self, kind: str) -> Callable[[PostgresSession], Stepper] | None:
        return self.stages().stepper(kind)

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
        if job.kind in (JOB_RECOGNIZE_REFERENCE, JOB_RESCORE_ANSWERS, JOB_RESEGMENT_BOOKLET):
            self._single(job)
            return True
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

    def _run(self, job: Job, stepper: Callable[[PostgresSession], Stepper]) -> None:
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

    def _abandon(self, job: Job, stepper: Callable[[PostgresSession], Stepper]) -> None:
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
