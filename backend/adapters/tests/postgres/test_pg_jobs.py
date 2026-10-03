"""The job queue on PostgreSQL: the shared queue contract, and what row-level security lets a
college and the worker see of ``jobs``. A throwaway database per test: the queue is global (a
claim takes any college's job), so a database shared with other tests would hand out theirs."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.testing import (
    Opener,
    TestDatabase,
    new_college,
)
from tarn_core.ids import CollegeId, JobId
from tarn_core.ports.jobs import Job
from tarn_core.testing import SequentialIds
from tarn_core.testing.queue_contract import ALL

pytestmark = pytest.mark.integration


class _Autocommit:
    """The ``JobQueue`` port over PostgreSQL, one transaction per call, as the API and the
    worker use it (enqueue in a college transaction; the rest as the scheduler)."""

    def __init__(self, open_: Opener, db: PostgresDatabase) -> None:
        self._open, self._db = open_, db

    def enqueue(
        self,
        college_id: CollegeId,
        kind: str,
        payload: dict[str, object],
        *,
        key: str | None = None,
    ) -> JobId:
        with self._open(college_id) as s:
            return s.jobs.enqueue(college_id, kind, payload, key=key)  # type: ignore[arg-type]

    def claim(self, worker: str) -> Job | None:
        with self._db.scheduler() as q:
            return q.claim(worker)

    def reap(self) -> list[Job]:
        with self._db.scheduler() as q:
            return q.reap()

    def heartbeat(self, job_id: JobId) -> None:
        with self._db.scheduler() as q:
            q.heartbeat(job_id)

    def succeed(self, job_id: JobId) -> None:
        with self._db.scheduler() as q:
            q.succeed(job_id)

    def fail(self, job_id: JobId, error: str, *, retry: bool) -> bool:
        with self._db.scheduler() as q:
            return q.fail(job_id, error, retry=retry)


@dataclass
class PgQueueHarness:
    test_db: TestDatabase
    db: PostgresDatabase
    a: CollegeId = field(init=False)
    b: CollegeId = field(init=False)
    queue: _Autocommit = field(init=False)

    def __post_init__(self) -> None:
        open_ = Opener(self.db, ids=SequentialIds())  # type: ignore[arg-type]
        self.a = new_college(open_, "A").id
        self.b = new_college(open_, "B").id
        self.queue = _Autocommit(open_, self.db)

    def expire_lease(self, job_id: JobId) -> None:
        with self.test_db.owner() as conn:
            conn.execute(
                "UPDATE jobs SET locked_until = now() - interval '1 second' WHERE id = %s",
                (job_id,),
            )

    def make_due(self, job_id: JobId) -> None:
        with self.test_db.owner() as conn:
            conn.execute("UPDATE jobs SET run_after = now() WHERE id = %s", (job_id,))


@pytest.mark.parametrize("check", ALL, ids=[c.__name__ for c in ALL])
def test_postgres_queue_contract(check, fresh) -> None:  # type: ignore[no-untyped-def]
    test_db, db = fresh
    check(PgQueueHarness(test_db, db))


# --- row-level security on jobs ---------------------------------------------------------------


def test_a_college_sees_and_changes_only_its_own_jobs(fresh) -> None:  # type: ignore[no-untyped-def]
    test_db, db = fresh
    h = PgQueueHarness(test_db, db)
    mine = h.queue.enqueue(h.a, "booklet.prepare", {"booklet_id": "x"})
    theirs = h.queue.enqueue(h.b, "booklet.prepare", {"booklet_id": "y"})
    with test_db.app(h.a) as conn:
        assert [r[0] for r in conn.execute("SELECT id FROM jobs").fetchall()] == [mine]
        # A job of the other college: not updatable, and not insertable for it.
        assert (
            conn.execute("UPDATE jobs SET status = 'failed' WHERE id = %s", (theirs,)).rowcount == 0
        )
        with pytest.raises(Exception, match="row-level security"):
            conn.execute(
                "INSERT INTO jobs (id, college_id, kind, status, max_attempts) "
                "VALUES (%s, %s, 'booklet.prepare', 'queued', 3)",
                (uuid4(), h.b),
            )
    with test_db.app(h.a) as conn, pytest.raises(Exception, match="permission denied"):
        conn.execute("DELETE FROM jobs")  # the application never deletes jobs
    with test_db.app(None) as conn:  # no college, not the scheduler: nothing
        assert conn.execute("SELECT count(*) FROM jobs").fetchone() == (0,)


def test_the_scheduler_sees_every_job_but_nothing_else(fresh) -> None:  # type: ignore[no-untyped-def]
    test_db, db = fresh
    h = PgQueueHarness(test_db, db)
    h.queue.enqueue(h.a, "booklet.prepare", {"booklet_id": "x"})
    h.queue.enqueue(h.b, "booklet.prepare", {"booklet_id": "y"})
    with db.scheduler() as q:
        assert q._conn.execute(text("SELECT count(*) FROM jobs")).scalar_one() == 2
        # The scheduler setting opens no other table.
        for table in ("colleges", "booklets", "users", "pages", "audit_events"):
            statement = text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
            assert q._conn.execute(statement).scalar_one() == 0, table


def test_a_job_row_holds_ids_and_bookkeeping_only(fresh) -> None:  # type: ignore[no-untyped-def]
    test_db, db = fresh
    h = PgQueueHarness(test_db, db)
    job_id = h.queue.enqueue(h.a, "booklet.prepare", {"booklet_id": str(UUID(int=7))}, key="k")
    claimed = h.queue.claim("worker-1")
    assert claimed is not None and claimed.id == job_id
    with test_db.owner() as conn:
        row = conn.execute(
            "SELECT payload::text, dedupe_key, status, attempts, locked_by FROM jobs WHERE id = %s",
            (job_id,),
        ).fetchone()
    assert row == (f'{{"booklet_id": "{UUID(int=7)}"}}', "k", "running", 1, "worker-1")


def test_only_one_live_job_per_key_even_when_racing(fresh) -> None:  # type: ignore[no-untyped-def]
    from concurrent.futures import ThreadPoolExecutor

    test_db, db = fresh
    h = PgQueueHarness(test_db, db)
    with ThreadPoolExecutor(8) as pool:
        ids = list(
            pool.map(
                lambda _: h.queue.enqueue(h.a, "booklet.prepare", {"booklet_id": "x"}, key="same"),
                range(8),
            )
        )
    assert len(set(ids)) == 1
    with test_db.owner() as conn:
        assert conn.execute("SELECT count(*) FROM jobs").fetchone() == (1,)


def test_racing_claimers_never_run_two_jobs_at_once(fresh) -> None:  # type: ignore[no-untyped-def]
    from concurrent.futures import ThreadPoolExecutor

    test_db, db = fresh
    h = PgQueueHarness(test_db, db)
    for n in range(6):
        h.queue.enqueue(h.a, "booklet.prepare", {"booklet_id": str(n)})
    with ThreadPoolExecutor(6) as pool:
        claimed = [j for j in pool.map(lambda n: h.queue.claim(f"w{n}"), range(6)) if j is not None]
    assert len(claimed) == 1  # the others found a job running
    with test_db.owner() as conn:
        assert conn.execute("SELECT count(*) FROM jobs WHERE status = 'running'").fetchone() == (1,)
