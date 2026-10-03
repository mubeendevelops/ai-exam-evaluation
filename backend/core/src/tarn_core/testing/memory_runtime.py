"""In-memory clock, ids, job queue, audit sink, blob store and page source."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tarn_core.domain.audit import AuditEvent
from tarn_core.domain.common import BlobKey, JsonValue
from tarn_core.errors import NotFoundError
from tarn_core.ids import BookletId, CollegeId, JobId
from tarn_core.ports.storage import PageImage


class FixedClock:
    """Starts at a fixed instant and moves only when told to."""

    def __init__(self, start: datetime = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


class SequentialIds:
    """UUIDs 1, 2, 3, ... so test output is reproducible."""

    def __init__(self) -> None:
        self._n = 0

    def new(self) -> UUID:
        self._n += 1
        return UUID(int=self._n)


@dataclass(frozen=True, slots=True, kw_only=True)
class QueuedJob:
    id: JobId
    college_id: CollegeId
    kind: str
    payload: Mapping[str, JsonValue]


class MemoryJobQueue:
    def __init__(self) -> None:
        self.jobs: list[QueuedJob] = []

    def enqueue(self, college_id: CollegeId, kind: str, payload: Mapping[str, JsonValue]) -> JobId:
        job = QueuedJob(
            id=JobId(UUID(int=len(self.jobs) + 1)),
            college_id=college_id,
            kind=kind,
            payload=dict(payload),
        )
        self.jobs.append(job)
        return job.id


class MemoryAuditSink:
    """Append only, like the real table; tests read ``events`` directly."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)


class MemoryBlobStore:
    def __init__(self) -> None:
        self._blobs: dict[BlobKey, tuple[bytes, str]] = {}

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None:
        self._blobs[key] = (bytes(data), media_type)

    def get(self, key: BlobKey) -> bytes:
        try:
            return self._blobs[key][0]
        except KeyError:
            raise NotFoundError(f"blob {key.value}") from None

    def exists(self, key: BlobKey) -> bool:
        return key in self._blobs

    def delete(self, key: BlobKey) -> None:
        self._blobs.pop(key, None)

    @property
    def keys(self) -> tuple[BlobKey, ...]:
        return tuple(self._blobs)


class MemoryPageSource:
    def __init__(self) -> None:
        self._pages: dict[tuple[CollegeId, BookletId], list[PageImage]] = {}

    def add(self, college_id: CollegeId, booklet_id: BookletId, page: PageImage) -> None:
        self._pages.setdefault((college_id, booklet_id), []).append(page)

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[PageImage]:
        found = self._pages.get((college_id, booklet_id))
        if found is None:
            raise NotFoundError(f"pages of booklet {booklet_id}")
        return sorted(found, key=lambda p: p.index)
