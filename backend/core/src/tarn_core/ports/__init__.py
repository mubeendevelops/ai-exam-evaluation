"""Ports: everything the core needs from the outside world, as ``typing.Protocol``s."""

from tarn_core.ports.engines import (
    DetectedRegion,
    DiagramRecognizer,
    Embedder,
    LayoutDetector,
    OcrEngine,
    Scorer,
    ScoringInput,
)
from tarn_core.ports.identity import (
    Cipher,
    CommonPasswords,
    DataKey,
    EmailMessage,
    IdentityStore,
    KeyManager,
    Mailer,
    PasswordHasher,
    RandomSource,
    RecordValidator,
)
from tarn_core.ports.jobs import JOB_PREPARE_BOOKLET, Job, JobQueue
from tarn_core.ports.pages import CleanedPage, PageCleaner, PageSplitter
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    GlobalItem,
    QuestionPart,
    ResultSheetRepository,
    ScoreRepository,
    StudentRepository,
    UserRepository,
)
from tarn_core.ports.runtime import AuditSink, Clock, IdGenerator
from tarn_core.ports.storage import BlobStore, PageImage, PageSource

__all__ = [
    "JOB_PREPARE_BOOKLET",
    "AuditSink",
    "BlobStore",
    "BookletRepository",
    "Cipher",
    "CleanedPage",
    "Clock",
    "CollegeRepository",
    "CommonPasswords",
    "ContentRepository",
    "DataKey",
    "DetectedRegion",
    "DiagramRecognizer",
    "EmailMessage",
    "Embedder",
    "GlobalItem",
    "IdGenerator",
    "IdentityStore",
    "Job",
    "JobQueue",
    "KeyManager",
    "LayoutDetector",
    "Mailer",
    "OcrEngine",
    "PageCleaner",
    "PageImage",
    "PageSource",
    "PageSplitter",
    "PasswordHasher",
    "QuestionPart",
    "RandomSource",
    "RecordValidator",
    "ResultSheetRepository",
    "ScoreRepository",
    "Scorer",
    "ScoringInput",
    "StudentRepository",
    "UserRepository",
]
