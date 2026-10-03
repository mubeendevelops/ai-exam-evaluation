"""In-memory adapters for every port, for tests only. Never wire these into production."""

from dataclasses import dataclass, field

from tarn_core.services._support import Runtime
from tarn_core.testing.fake_engines import (
    FixedCreditScorer,
    HashEmbedder,
    ScriptedDiagramRecognizer,
    ScriptedLayoutDetector,
    ScriptedOcrEngine,
)
from tarn_core.testing.memory_repositories import (
    MemoryBookletRepository,
    MemoryCollegeRepository,
    MemoryContentRepository,
    MemoryResultSheetRepository,
    MemoryScoreRepository,
    MemoryStudentRepository,
    MemoryUserRepository,
    TenantLog,
)
from tarn_core.testing.memory_runtime import (
    FixedClock,
    MemoryAuditSink,
    MemoryBlobStore,
    MemoryJobQueue,
    MemoryPageSource,
    QueuedJob,
    SequentialIds,
)


@dataclass
class InMemory:
    """One of each in-memory adapter, sharing one tenant log."""

    log: TenantLog = field(default_factory=TenantLog)
    clock: FixedClock = field(default_factory=FixedClock)
    ids: SequentialIds = field(default_factory=SequentialIds)
    audit: MemoryAuditSink = field(default_factory=MemoryAuditSink)
    jobs: MemoryJobQueue = field(default_factory=MemoryJobQueue)
    blobs: MemoryBlobStore = field(default_factory=MemoryBlobStore)
    page_source: MemoryPageSource = field(default_factory=MemoryPageSource)
    content: MemoryContentRepository = field(default_factory=MemoryContentRepository)

    def __post_init__(self) -> None:
        self.colleges = MemoryCollegeRepository(self.log)
        self.users = MemoryUserRepository(self.log)
        self.students = MemoryStudentRepository(self.log)
        self.booklets = MemoryBookletRepository(self.log)
        self.scores = MemoryScoreRepository(self.log)
        self.sheets = MemoryResultSheetRepository(self.log)

    @property
    def runtime(self) -> Runtime:
        return Runtime(clock=self.clock, ids=self.ids, audit=self.audit)


__all__ = [
    "FixedClock",
    "FixedCreditScorer",
    "HashEmbedder",
    "InMemory",
    "MemoryAuditSink",
    "MemoryBlobStore",
    "MemoryBookletRepository",
    "MemoryCollegeRepository",
    "MemoryContentRepository",
    "MemoryJobQueue",
    "MemoryPageSource",
    "MemoryResultSheetRepository",
    "MemoryScoreRepository",
    "MemoryStudentRepository",
    "MemoryUserRepository",
    "QueuedJob",
    "ScriptedDiagramRecognizer",
    "ScriptedLayoutDetector",
    "ScriptedOcrEngine",
    "SequentialIds",
    "TenantLog",
]
