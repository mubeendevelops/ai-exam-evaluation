"""In-memory backends for API tests: the whole API without a database. Tests only."""

from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field

from tarn_adapters.sheets import PyMuPdfSheetRenderer
from tarn_api.backends import CollegeScope
from tarn_core.ids import CollegeId
from tarn_core.ports.identity import IdentityStore
from tarn_core.ports.rendering import SheetRenderer
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.services.auth import AuthKit
from tarn_core.services.uploads import UploadLimits
from tarn_core.testing import InMemory


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
        return nullcontext(self.mem)
