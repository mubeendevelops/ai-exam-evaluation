"""The behaviour every ``JobQueue`` must have, as plain functions over a harness, so the
in-memory queue and the PostgreSQL queue are held to the same rules. Tests only."""

from typing import Protocol

from tarn_core.ids import CollegeId, JobId
from tarn_core.ports.jobs import JobQueue

KIND = "booklet.prepare"


class QueueHarness(Protocol):
    """A queue configured with ``max_attempts=3`` and ``max_running=1``, plus the means to move
    time for one job."""

    @property
    def queue(self) -> JobQueue: ...

    @property
    def a(self) -> CollegeId: ...

    @property
    def b(self) -> CollegeId: ...

    def expire_lease(self, job_id: JobId) -> None:
        """The job's worker has stopped heartbeating: its lease has run out."""
        ...

    def make_due(self, job_id: JobId) -> None:
        """The job's retry backoff is over."""
        ...


def enqueue(h: QueueHarness, college: CollegeId, name: str, key: str | None = None) -> JobId:
    return h.queue.enqueue(college, KIND, {"booklet_id": name}, key=key)


def check_idempotent_key(h: QueueHarness) -> None:
    first = enqueue(h, h.a, "x", key="k1")
    assert enqueue(h, h.a, "x", key="k1") == first
    assert enqueue(h, h.b, "x", key="k1") != first  # keys are per college
    assert enqueue(h, h.a, "y", key="k2") != first
    claimed = h.queue.claim("w1")
    assert claimed is not None and claimed.id == first
    # Still live (running): the key still returns the same job.
    assert enqueue(h, h.a, "x", key="k1") == first
    h.queue.succeed(first)
    # Finished: the same key may be queued again as a new job.
    assert enqueue(h, h.a, "x", key="k1") != first


def check_one_at_a_time_and_fair(h: QueueHarness) -> None:
    a1, a2, a3 = (enqueue(h, h.a, f"a{n}") for n in (1, 2, 3))
    b1 = enqueue(h, h.b, "b1")
    order = []
    for _ in range(4):
        job = h.queue.claim("w1")
        assert job is not None
        assert h.queue.claim("w2") is None  # one at a time: the first is still running
        order.append(job.id)
        h.queue.succeed(job.id)
    assert h.queue.claim("w1") is None
    # College A's backlog does not starve B: h.a, then h.b, then A's remaining jobs in order.
    assert order == [a1, b1, a2, a3]


def check_lease_and_heartbeat(h: QueueHarness) -> None:
    job_id = enqueue(h, h.a, "x")
    first = h.queue.claim("w1")
    assert first is not None and first.attempts == 1 and first.max_attempts == 3
    assert h.queue.claim("w2") is None
    h.queue.heartbeat(job_id)
    assert h.queue.claim("w2") is None
    h.expire_lease(job_id)  # the worker died
    again = h.queue.claim("w2")
    assert again is not None and again.id == job_id and again.attempts == 2


def check_retry_with_backoff(h: QueueHarness) -> None:
    job_id = enqueue(h, h.a, "x")
    job = h.queue.claim("w1")
    assert job is not None
    assert h.queue.fail(job_id, "Boom", retry=True) is True
    assert h.queue.claim("w1") is None  # backing off
    h.make_due(job_id)
    job = h.queue.claim("w1")
    assert job is not None and job.attempts == 2 and not job.last_attempt
    assert h.queue.fail(job_id, "Boom", retry=True) is True
    h.make_due(job_id)
    job = h.queue.claim("w1")
    assert job is not None and job.attempts == 3 and job.last_attempt
    assert h.queue.fail(job_id, "Boom", retry=True) is False  # attempts used up
    h.make_due(job_id)
    assert h.queue.claim("w1") is None


def check_no_retry_when_not_asked(h: QueueHarness) -> None:
    job_id = enqueue(h, h.a, "x")
    assert h.queue.claim("w1") is not None
    assert h.queue.fail(job_id, "Bad", retry=False) is False
    h.make_due(job_id)
    assert h.queue.claim("w1") is None


def check_reap_exhausted(h: QueueHarness) -> None:
    job_id = enqueue(h, h.a, "x")
    for attempt in (1, 2, 3):
        job = h.queue.claim("w1")
        assert job is not None and job.attempts == attempt
        assert h.queue.reap() == []  # lease still alive
        h.expire_lease(job_id)
        if attempt < 3:
            assert h.queue.reap() == []  # attempts left: it will be handed out again
    reaped = h.queue.reap()
    assert [j.id for j in reaped] == [job_id]
    assert h.queue.reap() == []  # reported once
    assert h.queue.claim("w2") is None


ALL = (
    check_idempotent_key,
    check_one_at_a_time_and_fair,
    check_lease_and_heartbeat,
    check_retry_with_backoff,
    check_no_retry_when_not_asked,
    check_reap_exhausted,
)
