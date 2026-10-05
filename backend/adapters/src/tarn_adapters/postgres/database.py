"""Connections and the per-college unit of work.

``PostgresDatabase.session(college_id)`` opens one transaction, sets ``app.college_id`` for
that transaction only (``set_config(..., true)``) and yields a ``PostgresSession`` whose
repositories and audit sink share the connection. Row-level security therefore scopes
every statement to that college, and a service call commits atomically with its audit
events. Connect with the application role (``TARN_APP_DATABASE_URL``), never the owner."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import make_url

from tarn_adapters.postgres.audit import PgAuditSink
from tarn_adapters.postgres.calibrations import PgCalibrationStore
from tarn_adapters.postgres.jobs import JobSettings, PgJobQueue
from tarn_adapters.postgres.repositories import (
    PgBookletRepository,
    PgCollegeRepository,
    PgContentRepository,
    PgResultSheetRepository,
    PgScoreRepository,
    PgStudentRepository,
    PgUserRepository,
)
from tarn_adapters.postgres.scoring_calibrations import PgScoringCalibrationStore
from tarn_core.ids import CollegeId
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime


def sqlalchemy_url(url: str) -> str:
    """``postgresql://`` (libpq form, used in settings) → SQLAlchemy with psycopg 3."""
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        raise ValueError("expected a postgresql:// URL")
    return parsed.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def libpq_url(url: str) -> str:
    """The reverse of ``sqlalchemy_url``: a URL psycopg accepts directly."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


@dataclass
class PostgresSession:
    """The adapters of one transaction bound to one college (or to none: global reads)."""

    conn: Connection
    college_id: CollegeId | None
    ids: IdGenerator
    clock: Clock
    blobs: BlobStore
    colleges: PgCollegeRepository = field(init=False)
    users: PgUserRepository = field(init=False)
    students: PgStudentRepository = field(init=False)
    content: PgContentRepository = field(init=False)
    booklets: PgBookletRepository = field(init=False)
    scores: PgScoreRepository = field(init=False)
    sheets: PgResultSheetRepository = field(init=False)
    audit: PgAuditSink = field(init=False)
    jobs: PgJobQueue = field(init=False)
    calibrations: PgCalibrationStore = field(init=False)
    job_settings: JobSettings = field(default_factory=JobSettings)

    def __post_init__(self) -> None:
        self.colleges = PgCollegeRepository(self.conn)
        self.users = PgUserRepository(self.conn)
        self.students = PgStudentRepository(self.conn)
        self.content = PgContentRepository(self.conn)
        self.booklets = PgBookletRepository(self.conn)
        self.scores = PgScoreRepository(self.conn)
        self.sheets = PgResultSheetRepository(self.conn)
        self.audit = PgAuditSink(self.conn)
        self.jobs = PgJobQueue(self.conn, self.job_settings)
        self.calibrations = PgCalibrationStore(self.conn)
        self.scoring_calibrations = PgScoringCalibrationStore(self.conn)

    @property
    def runtime(self) -> Runtime:
        return Runtime(clock=self.clock, ids=self.ids, audit=self.audit)


class PostgresDatabase:
    def __init__(
        self, url: str, *, pool_size: int = 5, job_settings: JobSettings | None = None
    ) -> None:
        self.engine: Engine = create_engine(
            sqlalchemy_url(url), pool_size=pool_size, pool_pre_ping=True
        )
        self.job_settings = job_settings or JobSettings()

    @contextmanager
    def session(
        self,
        college_id: CollegeId | None,
        *,
        ids: IdGenerator,
        clock: Clock,
        blobs: BlobStore,
    ) -> Iterator[PostgresSession]:
        """Commit on success, roll back on any exception."""
        with self.engine.begin() as conn:
            if college_id is not None:
                conn.execute(
                    text("SELECT set_config('app.college_id', :college, true)"),
                    {"college": str(college_id)},
                )
            yield PostgresSession(
                conn, college_id, ids=ids, clock=clock, blobs=blobs, job_settings=self.job_settings
            )

    @contextmanager
    def scheduler(self) -> Iterator[PgJobQueue]:
        """A transaction for the worker's queue bookkeeping: no college, but allowed to see every
        college's job rows (and only those: no other table has a scheduler policy)."""
        with self.engine.begin() as conn:
            conn.execute(text("SELECT set_config('app.scheduler', 'on', true)"))
            yield PgJobQueue(conn, self.job_settings)

    def dispose(self) -> None:
        self.engine.dispose()
