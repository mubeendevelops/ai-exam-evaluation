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
from tarn_core.ports.runtime import AuditSink, Clock, IdGenerator, JobQueue
from tarn_core.ports.storage import BlobStore, PageImage, PageSource

__all__ = [
    "AuditSink",
    "BlobStore",
    "BookletRepository",
    "Clock",
    "CollegeRepository",
    "ContentRepository",
    "DetectedRegion",
    "DiagramRecognizer",
    "Embedder",
    "GlobalItem",
    "IdGenerator",
    "JobQueue",
    "LayoutDetector",
    "OcrEngine",
    "PageImage",
    "PageSource",
    "QuestionPart",
    "ResultSheetRepository",
    "ScoreRepository",
    "Scorer",
    "ScoringInput",
    "StudentRepository",
    "UserRepository",
]
