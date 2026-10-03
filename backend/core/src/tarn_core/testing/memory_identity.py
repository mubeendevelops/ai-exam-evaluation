"""In-memory identity store and deterministic fakes of the P4 ports. Tests only.

The fakes are *not* secure: ``FakeHasher`` and ``FakeCipher`` only model the behaviour the
services rely on (verify, needs-rehash, tamper detection). The real argon2id, AES-GCM and
KMS adapters are tested in ``tarn_adapters``."""

import hashlib
import hmac
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from tarn_core.domain.identity import (
    ActionToken,
    AuthPolicy,
    AuthSession,
    HashParams,
    IdentityRecord,
    LoginCounters,
    RecoveryCode,
    TenantRecord,
    TenantStatus,
    TokenPurpose,
)
from tarn_core.errors import AlreadyExistsError, NotFoundError, TenantViolationError
from tarn_core.ids import AuthSessionId, CollegeId, UserId
from tarn_core.ports.identity import DataKey, EmailMessage
from tarn_core.testing.memory_repositories import TenantLog


class MemoryIdentityStore:
    def __init__(self, log: TenantLog | None = None) -> None:
        self.log = log or TenantLog()
        self.tenants: dict[CollegeId, TenantRecord] = {}
        self.identities: dict[UserId, IdentityRecord] = {}
        self._counters: dict[UserId, LoginCounters] = {}
        self._codes: dict[UserId, list[RecoveryCode]] = {}
        self.tokens: dict[str, ActionToken] = {}
        self.sessions: dict[AuthSessionId, AuthSession] = {}

    # --- helpers ----------------------------------------------------------------------------

    def _identity(self, college_id: CollegeId, user_id: UserId) -> IdentityRecord:
        self.log.touch(college_id)
        found = self.identities.get(user_id)
        if found is None or found.college_id != college_id:
            raise NotFoundError(f"identity {user_id}")
        return found

    # --- tenants ----------------------------------------------------------------------------

    def find_tenant(self, institution_id: str) -> TenantRecord | None:
        return next((t for t in self.tenants.values() if t.institution_id == institution_id), None)

    def list_tenants(self, status: TenantStatus | None = None) -> Sequence[TenantRecord]:
        return [t for t in self.tenants.values() if status is None or t.status is status]

    def get_tenant(self, college_id: CollegeId) -> TenantRecord:
        self.log.touch(college_id)
        try:
            return self.tenants[college_id]
        except KeyError:
            raise NotFoundError(f"tenant {college_id}") from None

    def save_tenant(self, tenant: TenantRecord) -> None:
        self.log.touch(tenant.college_id)
        other = self.find_tenant(tenant.institution_id)
        if other is not None and other.college_id != tenant.college_id:
            raise AlreadyExistsError("This Institution ID is already registered.")
        self.tenants[tenant.college_id] = tenant

    # --- identities -------------------------------------------------------------------------

    def get_identity(self, college_id: CollegeId, user_id: UserId) -> IdentityRecord:
        return self._identity(college_id, user_id)

    def find_identity(self, college_id: CollegeId, email_canonical: str) -> IdentityRecord | None:
        self.log.touch(college_id)
        return next(
            (
                i
                for i in self.identities.values()
                if i.college_id == college_id and i.email_canonical == email_canonical
            ),
            None,
        )

    def list_identities(self, college_id: CollegeId) -> Sequence[IdentityRecord]:
        self.log.touch(college_id)
        return [i for i in self.identities.values() if i.college_id == college_id]

    def save_identity(self, college_id: CollegeId, identity: IdentityRecord) -> None:
        self.log.touch(college_id)
        if identity.college_id != college_id:
            raise TenantViolationError("identity of another college")
        existing = self.identities.get(identity.user_id)
        if existing is not None and existing.college_id != college_id:
            raise TenantViolationError("identity of another college")
        same_email = self.find_identity(college_id, identity.email_canonical)
        if same_email is not None and same_email.user_id != identity.user_id:
            raise AlreadyExistsError("An account with this email already exists.")
        self.identities[identity.user_id] = identity

    # --- counters ---------------------------------------------------------------------------

    def counters(self, college_id: CollegeId, user_id: UserId) -> LoginCounters:
        self._identity(college_id, user_id)
        return self._counters.get(user_id, LoginCounters())

    def record_failed_login(
        self, college_id: CollegeId, user_id: UserId, now: datetime, policy: AuthPolicy
    ) -> LoginCounters:
        current = self.counters(college_id, user_id)
        failed = current.failed_login_attempts + 1
        if failed >= policy.max_failed_logins:
            updated = LoginCounters(failed_login_attempts=0, lockout_until=now + policy.lockout)
        else:
            updated = replace(current, failed_login_attempts=failed)
        self._counters[user_id] = updated
        return updated

    def set_counters(self, college_id: CollegeId, user_id: UserId, counters: LoginCounters) -> None:
        self._identity(college_id, user_id)
        self._counters[user_id] = counters

    # --- recovery codes ---------------------------------------------------------------------

    def recovery_codes(self, college_id: CollegeId, user_id: UserId) -> Sequence[RecoveryCode]:
        self._identity(college_id, user_id)
        return list(self._codes.get(user_id, []))

    def replace_recovery_codes(
        self, college_id: CollegeId, user_id: UserId, codes: Sequence[RecoveryCode]
    ) -> None:
        self._identity(college_id, user_id)
        self._codes[user_id] = list(codes)

    def use_recovery_code(
        self, college_id: CollegeId, user_id: UserId, ordinal: int, now: datetime
    ) -> bool:
        codes = self._codes.get(user_id, [])
        self._identity(college_id, user_id)
        for i, code in enumerate(codes):
            if code.ordinal == ordinal and not code.used:
                codes[i] = replace(code, used_at=now)
                return True
        return False

    # --- tokens -----------------------------------------------------------------------------

    def add_action_token(self, college_id: CollegeId, token: ActionToken) -> None:
        self._identity(college_id, token.user_id)
        if token.college_id != college_id:
            raise TenantViolationError("token of another college")
        for key, other in list(self.tokens.items()):
            if (
                other.user_id == token.user_id
                and other.purpose is token.purpose
                and other.used_at is None
            ):
                self.tokens[key] = replace(other, used_at=token.created_at)
        self.tokens[token.token_hash] = token

    def find_action_token(
        self, college_id: CollegeId, token_hash: str, purpose: TokenPurpose
    ) -> ActionToken | None:
        self.log.touch(college_id)
        found = self.tokens.get(token_hash)
        if found is None or found.college_id != college_id or found.purpose is not purpose:
            return None
        return found

    def use_action_token(
        self, college_id: CollegeId, token_hash: str, purpose: TokenPurpose, now: datetime
    ) -> ActionToken | None:
        found = self.find_action_token(college_id, token_hash, purpose)
        if found is None or not found.usable(now):
            return None
        used = replace(found, used_at=now)
        self.tokens[token_hash] = used
        return used

    # --- sessions ---------------------------------------------------------------------------

    def add_session(self, college_id: CollegeId, session: AuthSession) -> None:
        self._identity(college_id, session.user_id)
        self.sessions[session.id] = session

    def get_session(self, college_id: CollegeId, session_id: AuthSessionId) -> AuthSession:
        self.log.touch(college_id)
        found = self.sessions.get(session_id)
        if found is None or found.college_id != college_id:
            raise NotFoundError(f"session {session_id}")
        return found

    def rotate_session(
        self, college_id: CollegeId, session_id: AuthSessionId, old_hash: str, new_hash: str
    ) -> bool:
        session = self.get_session(college_id, session_id)
        if session.refresh_hash != old_hash:
            return False
        self.sessions[session_id] = replace(
            session, refresh_hash=new_hash, previous_refresh_hash=old_hash
        )
        return True

    def revoke_session(
        self, college_id: CollegeId, session_id: AuthSessionId, now: datetime
    ) -> None:
        session = self.get_session(college_id, session_id)
        if session.revoked_at is None:
            self.sessions[session_id] = replace(session, revoked_at=now)

    def revoke_user_sessions(self, college_id: CollegeId, user_id: UserId, now: datetime) -> None:
        self.log.touch(college_id)
        for sid, session in list(self.sessions.items()):
            if (
                session.college_id == college_id
                and session.user_id == user_id
                and session.revoked_at is None
            ):
                self.sessions[sid] = replace(session, revoked_at=now)


class FakeHasher:
    """``fake$t,m,p$<sha256(secret)>``. Counts calls, so tests can see rehashing."""

    def __init__(self) -> None:
        self.hashed = 0

    def hash(self, secret: str, params: HashParams) -> str:
        self.hashed += 1
        digest = hashlib.sha256(secret.encode()).hexdigest()
        return f"fake${params.time_cost},{params.memory_cost},{params.parallelism}${digest}"

    def verify(self, encoded: str, secret: str) -> bool:
        digest = encoded.rpartition("$")[2]
        return hmac.compare_digest(digest, hashlib.sha256(secret.encode()).hexdigest())

    def needs_rehash(self, encoded: str, params: HashParams) -> bool:
        wanted = f"{params.time_cost},{params.memory_cost},{params.parallelism}"
        return encoded.split("$")[1] != wanted


class FakeCipher:
    """Tag = SHA-256(key, aad, plaintext); the plaintext is XORed with a key stream."""

    def _stream(self, key: bytes, n: int) -> bytes:
        out = b""
        counter = 0
        while len(out) < n:
            out += hashlib.sha256(key + counter.to_bytes(4, "big")).digest()
            counter += 1
        return out[:n]

    def seal(self, key: bytes, plaintext: bytes, aad: bytes) -> bytes:
        tag = hashlib.sha256(key + aad + plaintext).digest()
        body = bytes(
            a ^ b for a, b in zip(plaintext, self._stream(key, len(plaintext)), strict=True)
        )
        return tag + body

    def open(self, key: bytes, sealed: bytes, aad: bytes) -> bytes:
        tag, body = sealed[:32], sealed[32:]
        plaintext = bytes(a ^ b for a, b in zip(body, self._stream(key, len(body)), strict=True))
        if not hmac.compare_digest(tag, hashlib.sha256(key + aad + plaintext).digest()):
            raise ValueError("authentication failed")
        return plaintext


class FakeKeyManager:
    """Wraps data keys with :class:`FakeCipher` under a per-reference master key."""

    def __init__(self, seed: bytes = b"fake-kms") -> None:
        self._seed = seed
        self._cipher = FakeCipher()
        self.generated = 0

    def _master(self, key_ref: str) -> bytes:
        return hashlib.sha256(self._seed + key_ref.encode()).digest()

    def generate_data_key(self, key_ref: str, context: bytes) -> DataKey:
        self.generated += 1
        plaintext = hashlib.sha256(self._seed + context + bytes([self.generated])).digest()
        return DataKey(
            plaintext=plaintext,
            wrapped=self._cipher.seal(self._master(key_ref), plaintext, context),
        )

    def unwrap(self, key_ref: str, wrapped: bytes, context: bytes) -> bytes:
        return self._cipher.open(self._master(key_ref), wrapped, context)


class CountingRandom:
    """Deterministic, distinct bytes."""

    def __init__(self) -> None:
        self._n = 0

    def token_bytes(self, n: int) -> bytes:
        self._n += 1
        return hashlib.sha256(f"random-{self._n}".encode()).digest()[:n].ljust(n, b"\0")


@dataclass
class MemoryMailer:
    def __post_init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def last_token(self, to: str | None = None) -> str:
        """The ``token=`` value of the latest email (to ``to``, if given)."""
        for message in reversed(self.sent):
            if to is None or message.to == to:
                return message.text.split("token=", 1)[1].split()[0]
        raise LookupError("no email with a token")


class SetPasswords:
    def __init__(self, words: Sequence[str] = ("password1234", "qwertyuiop12", "123456789012")):
        self._words = {w.lower() for w in words}

    def __contains__(self, password: object) -> bool:
        return isinstance(password, str) and password.lower() in self._words
