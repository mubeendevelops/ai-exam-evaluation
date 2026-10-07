"""The booklet stages wired from the adapters: one place that knows how a session's repositories
and the engines become the core's page pipeline, reader, segmenter, diagram step and scorer.

The queue worker (PostgreSQL sessions) and the ``tarn evaluate`` command (in-memory sessions)
both build their steps here, so a booklet is processed by the same composition on every
platform (R1, R2). A *session* is anything with the repositories listed in ``StageSession``:
a ``PostgresSession``, or the in-memory adapters of ``tarn_core.testing``."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from tarn_core.domain.diagram import DiagramKind
from tarn_core.domain.ocr import SelectorSettings
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.engines import (
    CalibrationStore,
    DiagramRecognizer,
    Embedder,
    LayoutDetector,
    OcrEngine,
    PageTransform,
    ScoringCalibrationStore,
    SecondOpinionScorer,
    WordList,
)
from tarn_core.ports.jobs import (
    JOB_DIAGRAMS_BOOKLET,
    JOB_PREPARE_BOOKLET,
    JOB_READ_BOOKLET,
    JOB_SCORE_BOOKLET,
    JOB_SEGMENT_BOOKLET,
    JobQueue,
)
from tarn_core.ports.pages import PageCleaner, PageSplitter
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    ScoreRepository,
    UserRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.diagrams.scorer import DiagramScorer
from tarn_core.services.diagrams.service import BookletDiagrams, ReferenceDiagrams
from tarn_core.services.ocr.reader import (
    BookletReader,
    OrientationPolicy,
    PageOcr,
    abandon_reading,
)
from tarn_core.services.pipeline import PagePipeline, QualityPolicy
from tarn_core.services.question_bank import QuestionBankService
from tarn_core.services.scoring import BookletScorer, ScoringService
from tarn_core.services.segmentation.resegment import BookletResegmenter
from tarn_core.services.segmentation.segmenter import SegmentationPolicy
from tarn_core.services.segmentation.service import BookletSegmenter
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.services.workflow import RescoreRequests


@dataclass(frozen=True)
class OcrKit:
    """What the reading job needs besides the session: built once per process."""

    layout: LayoutDetector
    engines: Mapping[str, OcrEngine]
    transform: PageTransform
    word_list: WordList | None
    settings: SelectorSettings
    orientation: OrientationPolicy


class StageSession(Protocol):
    """The repositories one transaction (or one in-memory world) offers the stages."""

    @property
    def booklets(self) -> BookletRepository: ...
    @property
    def scores(self) -> ScoreRepository: ...
    @property
    def content(self) -> ContentRepository: ...
    @property
    def colleges(self) -> CollegeRepository: ...
    @property
    def users(self) -> UserRepository: ...
    @property
    def jobs(self) -> JobQueue: ...
    @property
    def calibrations(self) -> CalibrationStore: ...
    @property
    def scoring_calibrations(self) -> ScoringCalibrationStore: ...
    @property
    def runtime(self) -> Runtime: ...


class Stepper(Protocol):
    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool: ...

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None: ...


class OcrUnavailableError(RuntimeError):
    """This process could not set up OCR (see the ``ocr.unavailable`` log line)."""


@dataclass
class NoOcr:
    """A reading job in a process without OCR: every attempt fails (and is retried with
    backoff, so a fixed worker can still take it); out of attempts, the booklet fails."""

    session: StageSession

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        raise OcrUnavailableError("OCR is not available on this worker")

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        abandon_reading(self.session.booklets, self.session.runtime, college_id, booklet_id)


@dataclass
class Stages:
    blobs: BlobStore
    splitter: PageSplitter
    cleaner: PageCleaner
    policy: QualityPolicy
    max_pages: int
    ocr: OcrKit | None = None
    """None: OCR could not be set up; reading jobs fail (with retries) until a process with OCR
    takes them, and in the end their booklets fail as ``reading_failed``."""
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

    def pipeline(self, session: StageSession) -> PagePipeline:
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

    def reader(self, session: StageSession) -> BookletReader:
        if self.ocr is None:  # pragma: no cover  (stepper hands out NoOcr instead)
            raise OcrUnavailableError("this process has no OCR engines")
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

    def segmenter(self, session: StageSession) -> BookletSegmenter:
        return BookletSegmenter(
            booklets=session.booklets,
            content=session.content,
            embedder=self.embedder,
            runtime=session.runtime,
            jobs=session.jobs,
            policy=self.segmentation,
        )

    def resegmenter(self, session: StageSession) -> BookletResegmenter:
        return BookletResegmenter(
            booklets=session.booklets,
            scores=session.scores,
            content=session.content,
            embedder=self.embedder,
            runtime=session.runtime,
            rescore=RescoreRequests(
                booklets=session.booklets, runtime=session.runtime, rescorer=self.scorer(session)
            ),
            policy=self.segmentation,
        )

    def scorer(self, session: StageSession) -> BookletScorer:
        scoring = ScoringService.standard(
            booklets=session.booklets,
            scores=session.scores,
            content=session.content,
            runtime=session.runtime,
            embedder=self.scoring_embedder,
            calibrations=session.scoring_calibrations,
            word_list=self.word_list,
            extra=[DiagramScorer()],
            llm=self.llm,
            colleges=session.colleges,
        )
        return BookletScorer(booklets=session.booklets, scoring=scoring, runtime=session.runtime)

    def diagrams(self, session: StageSession) -> BookletDiagrams:
        return BookletDiagrams(
            booklets=session.booklets,
            content=session.content,
            blobs=self.blobs,
            runtime=session.runtime,
            jobs=session.jobs,
            recognizers=self.recognizers,
        )

    def references(self, session: StageSession) -> ReferenceDiagrams:
        labels = None
        if self.ocr is not None:
            labels = PageOcr(
                layout=self.ocr.layout,
                engines=self.ocr.engines,
                transform=self.ocr.transform,
                settings=self.ocr.settings,
                calibrations={
                    (c.engine, c.content_class): c for c in session.calibrations.latest()
                },
                orientation=self.ocr.orientation,
            )
        bank = QuestionBankService(
            content=session.content,
            users=session.users,
            colleges=session.colleges,
            blobs=self.blobs,
            runtime=session.runtime,
        )
        return ReferenceDiagrams(
            content=session.content,
            blobs=self.blobs,
            runtime=session.runtime,
            recognizers=self.recognizers,
            labels=labels,
            glossary=bank,
        )

    def stepper(self, kind: str) -> Callable[[StageSession], Stepper] | None:
        """The stage a booklet job of this kind runs, or None for a kind that is not a
        booklet stage."""
        if kind == JOB_PREPARE_BOOKLET:
            return self.pipeline
        if kind == JOB_READ_BOOKLET:
            return self.reader if self.ocr is not None else NoOcr
        if kind == JOB_SEGMENT_BOOKLET:
            return self.segmenter
        if kind == JOB_DIAGRAMS_BOOKLET:
            return self.diagrams
        if kind == JOB_SCORE_BOOKLET:
            return self.scorer
        return None


class LocalStageRunner:
    """The worker's loop for one process and one queue: claim a job, run its stage step by step
    until it is done, mark it done, until the queue is empty. No retries and no leases: a
    stage that raises ends its booklet as failed (the stage's ``abandon``) and the run goes on
    to whatever else is queued. Used by ``tarn evaluate`` on the in-memory adapters."""

    def __init__(
        self,
        stages: Stages,
        session: StageSession,
        *,
        worker: str = "local",
        on_step: Callable[[str], None] | None = None,
    ) -> None:
        self._stages = stages
        self._session = session
        self._worker = worker
        self._on_step = on_step

    def run(self, college_id: CollegeId, booklet_id: BookletId) -> list[str]:
        errors: list[str] = []
        queue = self._session.jobs
        while (job := queue.claim(self._worker)) is not None:
            build = self._stages.stepper(job.kind)
            if build is None:
                queue.fail(job.id, "unknown job kind", retry=False)
                continue
            target = BookletId(UUID(str(job.payload["booklet_id"])))
            try:
                while not build(self._session).step(job.college_id, target):
                    if self._on_step is not None:
                        self._on_step(job.kind)
            except Exception as error:  # the stage failed: end its booklet, keep the run alive
                errors.append(type(error).__name__)
                queue.fail(job.id, type(error).__name__, retry=False)
                build(self._session).abandon(job.college_id, target)
                continue
            if self._on_step is not None:
                self._on_step(job.kind)
            queue.succeed(job.id)
        return errors
