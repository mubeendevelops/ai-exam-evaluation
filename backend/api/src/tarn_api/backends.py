"""What a request needs from the outside world, and the per-request unit of work.

``Backends`` opens the two kinds of session: the identity store (credentials) and one
college's repositories (profiles, roster, audit). Production uses PostgreSQL for both, as the
roles ``tarn_auth`` and ``tarn_app``; API tests use the in-memory adapters.

``Unit`` is one request's work: both sessions, the services built on them, and a mailer that
sends only after both sessions have committed."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Protocol

from tarn_adapters.auth.mail import DeferredMailer
from tarn_adapters.auth.secrets import SIGNING_KEY, secret_source
from tarn_adapters.auth.wiring import auth_kit
from tarn_adapters.blob.minio_client import make_client
from tarn_adapters.blob.minio_store import MinioBlobStore
from tarn_adapters.config import Settings
from tarn_adapters.identity.database import IdentityDatabase
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.jobs import JobSettings
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.identity import IdentityStore
from tarn_core.ports.jobs import JobQueue
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
from tarn_core.services.auth import AccountService, AuthKit, AuthService
from tarn_core.services.blueprints import BlueprintService
from tarn_core.services.booklets import BookletService
from tarn_core.services.content import ContentService
from tarn_core.services.diagrams.jobs import QueuedRescore
from tarn_core.services.diagrams.service import ReferenceDiagrams
from tarn_core.services.pipeline import PageDecisions
from tarn_core.services.question_bank import QuestionBankService
from tarn_core.services.registration import RegistrationService
from tarn_core.services.roster import RosterService
from tarn_core.services.subjects import SubjectService
from tarn_core.services.uploads import UploadLimits, UploadService
from tarn_core.services.workflow import (
    BookletGuard,
    LockPolicy,
    RescoreRequests,
    ReviewService,
    SegmentEdits,
    StudentGraphEdits,
    TextEditor,
)


class CollegeScope(Protocol):
    """The college-zone adapters of one transaction bound to one college."""

    @property
    def colleges(self) -> CollegeRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def students(self) -> StudentRepository: ...

    @property
    def content(self) -> ContentRepository: ...

    @property
    def booklets(self) -> BookletRepository: ...

    @property
    def scores(self) -> ScoreRepository: ...

    @property
    def sheets(self) -> ResultSheetRepository: ...

    @property
    def jobs(self) -> JobQueue: ...

    @property
    def blobs(self) -> BlobStore: ...

    @property
    def runtime(self) -> Runtime: ...


class Backends(Protocol):
    kit: AuthKit
    clock: Clock
    ids: IdGenerator
    signing_key: bytes
    secure_cookies: bool
    access_token_minutes: int
    upload_limits: UploadLimits
    lock_minutes: float

    def identity(self) -> AbstractContextManager[IdentityStore]: ...

    def college(self, college_id: CollegeId) -> AbstractContextManager[CollegeScope]: ...


@dataclass
class Unit:
    identity: IdentityStore
    scope: CollegeScope
    kit: AuthKit
    limits: UploadLimits
    lock_minutes: float = 15.0

    @property
    def auth(self) -> AuthService:
        return AuthService(
            identity=self.identity, users=self.scope.users, kit=self.kit, runtime=self.scope.runtime
        )

    @property
    def accounts(self) -> AccountService:
        return AccountService(
            identity=self.identity, users=self.scope.users, kit=self.kit, runtime=self.scope.runtime
        )

    @property
    def registration(self) -> RegistrationService:
        return RegistrationService(
            identity=self.identity,
            users=self.scope.users,
            colleges=self.scope.colleges,
            kit=self.kit,
            runtime=self.scope.runtime,
        )

    @property
    def roster(self) -> RosterService:
        return RosterService(
            students=self.scope.students, users=self.scope.users, runtime=self.scope.runtime
        )

    @property
    def subjects(self) -> SubjectService:
        return SubjectService(
            content=self.scope.content, users=self.scope.users, runtime=self.scope.runtime
        )

    @property
    def bank(self) -> QuestionBankService:
        return QuestionBankService(
            content=self.scope.content,
            users=self.scope.users,
            colleges=self.scope.colleges,
            blobs=self.scope.blobs,
            runtime=self.scope.runtime,
        )

    @property
    def content(self) -> ContentService:
        return ContentService(
            content=self.scope.content, users=self.scope.users, runtime=self.scope.runtime
        )

    @property
    def blueprints(self) -> BlueprintService:
        return BlueprintService(
            content=self.scope.content, users=self.scope.users, runtime=self.scope.runtime
        )

    @property
    def booklet_service(self) -> BookletService:
        return BookletService(
            booklets=self.scope.booklets,
            students=self.scope.students,
            users=self.scope.users,
            content=self.scope.content,
            scores=self.scope.scores,
            sheets=self.scope.sheets,
            blobs=self.scope.blobs,
            runtime=self.scope.runtime,
        )

    @property
    def uploads(self) -> UploadService:
        return UploadService(
            booklets=self.booklet_service,
            repository=self.scope.booklets,
            blobs=self.scope.blobs,
            jobs=self.scope.jobs,
            runtime=self.scope.runtime,
            limits=self.limits,
        )

    @property
    def reference_diagrams(self) -> ReferenceDiagrams:
        """Edits only: the API loads no recognizer (recognition is the worker's job)."""
        return ReferenceDiagrams(
            content=self.scope.content,
            blobs=self.scope.blobs,
            runtime=self.scope.runtime,
            recognizers={},
            labels=None,
            glossary=self.bank,
        )

    def student_diagrams(self, booklet_id: BookletId) -> StudentGraphEdits:
        """A corrected drawing's answer is re-scored by the worker (``answers.rescore``)."""
        return StudentGraphEdits(
            booklets=self.scope.booklets,
            runtime=self.scope.runtime,
            guard=self.guard,
            rescore=self.rescore(booklet_id),
        )

    # --- the review (P15) ---------------------------------------------------------------

    @property
    def guard(self) -> BookletGuard:
        return BookletGuard(
            booklets=self.scope.booklets,
            users=self.scope.users,
            runtime=self.scope.runtime,
            policy=LockPolicy(timeout=timedelta(minutes=self.lock_minutes)),
        )

    def rescore(self, booklet_id: BookletId) -> RescoreRequests:
        """New suggestions come from the worker (``answers.rescore``): the API loads no
        models."""
        return RescoreRequests(
            booklets=self.scope.booklets,
            runtime=self.scope.runtime,
            rescorer=QueuedRescore(self.scope.jobs, booklet_id),
        )

    def review(self, booklet_id: BookletId) -> ReviewService:
        return ReviewService(
            booklets=self.scope.booklets,
            scores=self.scope.scores,
            content=self.scope.content,
            sheets=self.scope.sheets,
            users=self.scope.users,
            runtime=self.scope.runtime,
            guard=self.guard,
            rescore=self.rescore(booklet_id),
        )

    def text_editor(self, booklet_id: BookletId) -> TextEditor:
        return TextEditor(
            booklets=self.scope.booklets,
            runtime=self.scope.runtime,
            guard=self.guard,
            rescore=self.rescore(booklet_id),
        )

    def segment_edits(self, booklet_id: BookletId) -> SegmentEdits:
        return SegmentEdits(
            booklets=self.scope.booklets,
            scores=self.scope.scores,
            content=self.scope.content,
            runtime=self.scope.runtime,
            guard=self.guard,
            rescore=self.rescore(booklet_id),
        )

    @property
    def page_decisions(self) -> PageDecisions:
        return PageDecisions(
            booklets=self.scope.booklets, runtime=self.scope.runtime, jobs=self.scope.jobs
        )


@contextmanager
def unit_of_work(backends: Backends, college_id: CollegeId) -> Iterator[Unit]:
    """Both sessions for one college. The college session commits first, then the identity
    session; mail goes out only after both. Any exception rolls both back and sends nothing."""
    mailer = DeferredMailer(backends.kit.mailer)
    with backends.identity() as identity, backends.college(college_id) as scope:
        yield Unit(
            identity=identity,
            scope=scope,
            kit=replace(backends.kit, mailer=mailer),
            limits=backends.upload_limits,
            lock_minutes=backends.lock_minutes,
        )
    mailer.flush()


class PostgresBackends:
    """``tarn_app`` on the application database, ``tarn_auth`` on the identity database."""

    def __init__(self, settings: Settings) -> None:
        secrets = secret_source(settings)
        self.kit = auth_kit(settings, secrets=secrets)
        self.clock: Clock = SystemClock()
        self.ids: IdGenerator = UuidGenerator()
        self.signing_key = secrets.get(SIGNING_KEY)
        self.secure_cookies = settings.env == "production"
        self.access_token_minutes = settings.access_token_minutes
        self.upload_limits = UploadLimits(
            max_waiting=settings.max_queued_booklets_per_teacher,
            max_total_bytes=settings.upload_max_bytes,
            max_files=settings.upload_max_pages,
        )
        self.lock_minutes = settings.booklet_lock_minutes
        self._app = PostgresDatabase(
            settings.app_database_url,
            job_settings=JobSettings(
                max_attempts=settings.job_max_attempts,
                lease_seconds=settings.job_lease_seconds,
                backoff_seconds=settings.job_backoff_seconds,
            ),
        )
        self._identity = IdentityDatabase(settings.identity_app_database_url)
        # The client connects on first use, so building the app needs no MinIO.
        self._blobs = MinioBlobStore(make_client(settings), settings.blob_bucket)

    def identity(self) -> AbstractContextManager[IdentityStore]:
        return self._identity.session()

    def college(self, college_id: CollegeId) -> AbstractContextManager[CollegeScope]:
        return self._app.session(college_id, ids=self.ids, clock=self.clock, blobs=self._blobs)

    def dispose(self) -> None:
        self._app.dispose()
        self._identity.dispose()
