"""What a request needs from the outside world, and the per-request unit of work.

``Backends`` opens the two kinds of session: the identity store (credentials) and one
college's repositories (profiles, roster, audit). Production uses PostgreSQL for both, as the
roles ``tarn_auth`` and ``tarn_app``; API tests use the in-memory adapters.

``Unit`` is one request's work: both sessions, the services built on them, and a mailer that
sends only after both sessions have committed."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from typing import Protocol

from tarn_adapters.auth.mail import DeferredMailer
from tarn_adapters.auth.secrets import SIGNING_KEY, secret_source
from tarn_adapters.auth.wiring import auth_kit
from tarn_adapters.blob.unavailable import NoBlobStore
from tarn_adapters.config import Settings
from tarn_adapters.identity.database import IdentityDatabase
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_core.ids import CollegeId
from tarn_core.ports.identity import IdentityStore
from tarn_core.ports.repositories import CollegeRepository, StudentRepository, UserRepository
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.services._support import Runtime
from tarn_core.services.auth import AccountService, AuthKit, AuthService
from tarn_core.services.registration import RegistrationService
from tarn_core.services.roster import RosterService


class CollegeScope(Protocol):
    """The college-zone adapters of one transaction bound to one college."""

    @property
    def colleges(self) -> CollegeRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def students(self) -> StudentRepository: ...

    @property
    def runtime(self) -> Runtime: ...


class Backends(Protocol):
    kit: AuthKit
    clock: Clock
    ids: IdGenerator
    signing_key: bytes
    secure_cookies: bool
    access_token_minutes: int

    def identity(self) -> AbstractContextManager[IdentityStore]: ...

    def college(self, college_id: CollegeId) -> AbstractContextManager[CollegeScope]: ...


@dataclass
class Unit:
    identity: IdentityStore
    scope: CollegeScope
    kit: AuthKit

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


@contextmanager
def unit_of_work(backends: Backends, college_id: CollegeId) -> Iterator[Unit]:
    """Both sessions for one college. The college session commits first, then the identity
    session; mail goes out only after both. Any exception rolls both back and sends nothing."""
    mailer = DeferredMailer(backends.kit.mailer)
    with backends.identity() as identity, backends.college(college_id) as scope:
        yield Unit(identity=identity, scope=scope, kit=replace(backends.kit, mailer=mailer))
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
        self._app = PostgresDatabase(settings.app_database_url)
        self._identity = IdentityDatabase(settings.identity_app_database_url)
        self._blobs = NoBlobStore()  # no endpoint touches blobs yet (P9 adds uploads)

    def identity(self) -> AbstractContextManager[IdentityStore]:
        return self._identity.session()

    def college(self, college_id: CollegeId) -> AbstractContextManager[CollegeScope]:
        return self._app.session(college_id, ids=self.ids, clock=self.clock, blobs=self._blobs)

    def dispose(self) -> None:
        self._app.dispose()
        self._identity.dispose()
