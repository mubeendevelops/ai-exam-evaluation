"""In-memory backends for API tests: the whole API without a database. Tests only."""

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field

from tarn_adapters.sheets import PyMuPdfSheetRenderer
from tarn_api.backends import CollegeScope
from tarn_core.ids import CollegeId
from tarn_core.ports.identity import IdentityStore
from tarn_core.ports.jobs import JobQueue
from tarn_core.ports.rendering import SheetRenderer
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    ResultSheetRepository,
    ScoreRepository,
    StudentRepository,
    UserRepository,
)
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.auth import AuthKit
from tarn_core.services.blob_scope import scoped_blobs
from tarn_core.services.uploads import UploadLimits
from tarn_core.testing import InMemory


@dataclass
class MemoryScope:
    """The in-memory adapters seen from one college: the blob store is bound to it, as in
    ``PostgresSession`` (the repositories check ``college_id`` themselves)."""

    mem: InMemory
    college_id: CollegeId

    @property
    def colleges(self) -> CollegeRepository:
        return self.mem.colleges

    @property
    def users(self) -> UserRepository:
        return self.mem.users

    @property
    def students(self) -> StudentRepository:
        return self.mem.students

    @property
    def content(self) -> ContentRepository:
        return self.mem.content

    @property
    def booklets(self) -> BookletRepository:
        return self.mem.booklets

    @property
    def scores(self) -> ScoreRepository:
        return self.mem.scores

    @property
    def sheets(self) -> ResultSheetRepository:
        return self.mem.sheets

    @property
    def jobs(self) -> JobQueue:
        return self.mem.jobs

    @property
    def blobs(self) -> BlobStore:
        return scoped_blobs(self.mem.blobs, self.college_id)

    @property
    def runtime(self) -> Runtime:
        return self.mem.runtime


@dataclass
class MemoryBackends:
    mem: InMemory = field(default_factory=InMemory)
    signing_key: bytes = b"api-test-signing-key-0123456789abcdef"
    secure_cookies: bool = False
    access_token_minutes: int = 15
    upload_limits: UploadLimits = field(default_factory=UploadLimits)
    lock_minutes: float = 15.0
    sheet_renderer: SheetRenderer = field(default_factory=PyMuPdfSheetRenderer)
    """The real PDF renderer: API tests read the sheets they download."""

    def __post_init__(self) -> None:
        self.kit: AuthKit = self.mem.auth_kit
        self.clock: Clock = self.mem.clock  # advance it through ``mem.clock``
        self.ids: IdGenerator = self.mem.ids

    def identity(self) -> AbstractContextManager[IdentityStore]:
        return nullcontext(self.mem.identity)

    def college(self, college_id: CollegeId) -> AbstractContextManager[CollegeScope]:
        return nullcontext(MemoryScope(self.mem, college_id))
