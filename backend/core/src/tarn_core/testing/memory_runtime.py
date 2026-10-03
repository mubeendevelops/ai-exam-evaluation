"""In-memory clock, ids, job queue, audit sink, blob store and page source."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from tarn_core.domain.audit import AuditEvent
from tarn_core.domain.common import BlobKey, JsonValue
from tarn_core.errors import NotFoundError
from tarn_core.ids import BookletId, CollegeId, JobId
from tarn_core.ports.jobs import Job
from tarn_core.ports.runtime import Clock
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


@dataclass(kw_only=True)
class QueuedJob:
    id: JobId
    college_id: CollegeId
    kind: str
    payload: Mapping[str, JsonValue]
    key: str | None = None
    status: str = "queued"  # queued | running | succeeded | failed
    attempts: int = 0
    run_after: datetime | None = None
    locked_until: datetime | None = None
    started_at: datetime | None = None
    error: str | None = None


class MemoryJobQueue:
    """The queue's rules in memory: idempotent keys, colleges served in turn, one job running
    at a time, a lease that a heartbeat extends, retry with backoff."""

    def __init__(
        self,
        clock: Clock | None = None,
        *,
        max_attempts: int = 3,
        lease_seconds: float = 120.0,
        backoff_seconds: float = 5.0,
        max_running: int = 1,
    ) -> None:
        self.jobs: list[QueuedJob] = []
        self._clock: Clock = clock or FixedClock()
        self._max_attempts = max_attempts
        self._lease = timedelta(seconds=lease_seconds)
        self._backoff = backoff_seconds
        self._max_running = max_running
        self._last_served: dict[CollegeId, int] = {}
        self._turn = 0

    def enqueue(
        self,
        college_id: CollegeId,
        kind: str,
        payload: Mapping[str, JsonValue],
        *,
        key: str | None = None,
    ) -> JobId:
        if key is not None:
            for job in self.jobs:
                if (
                    job.college_id == college_id
                    and job.key == key
                    and job.status in ("queued", "running")
                ):
                    return job.id
        job = QueuedJob(
            id=JobId(UUID(int=len(self.jobs) + 1)),
            college_id=college_id,
            kind=kind,
            payload=dict(payload),
            key=key,
            run_after=self._clock.now(),
        )
        self.jobs.append(job)
        return job.id

    def _find(self, job_id: JobId) -> QueuedJob:
        return next(j for j in self.jobs if j.id == job_id)

    def claim(self, worker: str) -> Job | None:
        now = self._clock.now()
        running = [j for j in self.jobs if j.status == "running" and (j.locked_until or now) > now]
        if len(running) >= self._max_running:
            return None
        due = [
            j
            for j in self.jobs
            if (j.status == "queued" and (j.run_after or now) <= now)
            or (
                j.status == "running"
                and (j.locked_until or now) <= now
                and j.attempts < self._max_attempts
            )
        ]
        if not due:
            return None
        # The college served least recently first, then the oldest job.
        job = min(due, key=lambda j: (self._last_served.get(j.college_id, -1), self.jobs.index(j)))
        self._turn += 1
        self._last_served[job.college_id] = self._turn
        job.status, job.attempts = "running", job.attempts + 1
        job.locked_until, job.started_at = now + self._lease, now
        return Job(
            id=job.id,
            college_id=job.college_id,
            kind=job.kind,
            payload=job.payload,
            attempts=job.attempts,
            max_attempts=self._max_attempts,
        )

    def reap(self) -> list[Job]:
        now = self._clock.now()
        reaped = []
        for job in self.jobs:
            if (
                job.status == "running"
                and (job.locked_until or now) <= now
                and job.attempts >= self._max_attempts
            ):
                job.status, job.error = "failed", "lease expired"
                reaped.append(
                    Job(
                        id=job.id,
                        college_id=job.college_id,
                        kind=job.kind,
                        payload=job.payload,
                        attempts=job.attempts,
                        max_attempts=self._max_attempts,
                    )
                )
        return reaped

    def heartbeat(self, job_id: JobId) -> None:
        self._find(job_id).locked_until = self._clock.now() + self._lease

    def succeed(self, job_id: JobId) -> None:
        self._find(job_id).status = "succeeded"

    def fail(self, job_id: JobId, error: str, *, retry: bool) -> bool:
        job = self._find(job_id)
        job.error = error
        if retry and job.attempts < self._max_attempts:
            job.status = "queued"
            job.run_after = self._clock.now() + timedelta(
                seconds=self._backoff * 2 ** (job.attempts - 1)
            )
            return True
        job.status = "failed"
        return False


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

    def delete_prefix(self, prefix: BlobKey) -> int:
        doomed = [k for k in self._blobs if k.value.startswith(prefix.value + "/")]
        for key in doomed:
            del self._blobs[key]
        return len(doomed)

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


class MemoryQueueHarness:
    """The queue contract's harness over ``MemoryJobQueue`` and a ``FixedClock``."""

    def __init__(self) -> None:
        self.clock = FixedClock()
        self.queue = MemoryJobQueue(self.clock, max_attempts=3, max_running=1)
        self.a = CollegeId(UUID(int=0xA))
        self.b = CollegeId(UUID(int=0xB))

    def _job(self, job_id: JobId) -> QueuedJob:
        return next(j for j in self.queue.jobs if j.id == job_id)

    def expire_lease(self, job_id: JobId) -> None:
        self._job(job_id).locked_until = self.clock.now() - timedelta(seconds=1)

    def make_due(self, job_id: JobId) -> None:
        self._job(job_id).run_after = self.clock.now()
