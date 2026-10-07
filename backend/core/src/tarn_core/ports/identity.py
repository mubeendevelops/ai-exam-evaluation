"""Ports for credentials, keys, hashing, randomness and email (P4).

The identity store is separate from the college repositories so that credential storage can
move (another database, a hosted identity service) without touching anything else (R1).
Like the college repositories, every per-tenant method takes ``college_id`` first and never
reaches another college; only the tenant lookups used before sign-in span tenants."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tarn_core.domain.common import JsonValue
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
from tarn_core.ids import AuthSessionId, CollegeId, UserId


class IdentityStore(Protocol):
    # --- tenant registry ------------------------------------------------------------------
    def find_tenant(self, institution_id: str) -> TenantRecord | None:
        """By Institution ID; needs no college (sign-in, availability check)."""
        ...

    def list_tenants(self, status: TenantStatus | None = None) -> Sequence[TenantRecord]:
        """Operator commands and backups only."""
        ...

    def get_tenant(self, college_id: CollegeId) -> TenantRecord: ...

    def save_tenant(self, tenant: TenantRecord) -> None:
        """Insert or update. Raises AlreadyExistsError if the Institution ID is taken."""
        ...

    # --- identities -----------------------------------------------------------------------
    def get_identity(self, college_id: CollegeId, user_id: UserId) -> IdentityRecord: ...

    def find_identity(
        self, college_id: CollegeId, email_canonical: str
    ) -> IdentityRecord | None: ...

    def list_identities(self, college_id: CollegeId) -> Sequence[IdentityRecord]: ...

    def save_identity(self, college_id: CollegeId, identity: IdentityRecord) -> None:
        """Insert or update the credential record; never touches the counters. Raises
        AlreadyExistsError if another identity of the college has the email."""
        ...

    # --- lockout counters (atomic, apart from the encrypted record) ----------------------
    def counters(self, college_id: CollegeId, user_id: UserId) -> LoginCounters: ...

    def record_failed_login(
        self, college_id: CollegeId, user_id: UserId, now: datetime, policy: AuthPolicy
    ) -> LoginCounters:
        """One atomic increment. Reaching ``policy.max_failed_logins`` sets ``lockout_until``
        to ``now + policy.lockout`` and starts the count again."""
        ...

    def set_counters(self, college_id: CollegeId, user_id: UserId, counters: LoginCounters) -> None:
        """Reset after a good sign-in, unlock, or restore from a backup."""
        ...

    # --- recovery codes -------------------------------------------------------------------
    def recovery_codes(self, college_id: CollegeId, user_id: UserId) -> Sequence[RecoveryCode]: ...

    def replace_recovery_codes(
        self, college_id: CollegeId, user_id: UserId, codes: Sequence[RecoveryCode]
    ) -> None: ...

    def use_recovery_code(
        self, college_id: CollegeId, user_id: UserId, ordinal: int, now: datetime
    ) -> bool:
        """Mark one code used, atomically; False if it was already used."""
        ...

    # --- single-use links -----------------------------------------------------------------
    def add_action_token(self, college_id: CollegeId, token: ActionToken) -> None:
        """Earlier unused tokens of the same user and purpose stop working."""
        ...

    def find_action_token(
        self, college_id: CollegeId, token_hash: str, purpose: TokenPurpose
    ) -> ActionToken | None: ...

    def use_action_token(
        self, college_id: CollegeId, token_hash: str, purpose: TokenPurpose, now: datetime
    ) -> ActionToken | None:
        """Mark the token used, atomically, if it is unused and unexpired; else None."""
        ...

    # --- sessions -------------------------------------------------------------------------
    def add_session(self, college_id: CollegeId, session: AuthSession) -> None: ...

    def get_session(self, college_id: CollegeId, session_id: AuthSessionId) -> AuthSession: ...

    def rotate_session(
        self, college_id: CollegeId, session_id: AuthSessionId, old_hash: str, new_hash: str
    ) -> bool:
        """Compare-and-swap of the refresh hash; False if ``old_hash`` is not current."""
        ...

    def revoke_session(
        self, college_id: CollegeId, session_id: AuthSessionId, now: datetime
    ) -> None: ...

    def revoke_user_sessions(
        self, college_id: CollegeId, user_id: UserId, now: datetime
    ) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class DataKey:
    plaintext: bytes
    wrapped: bytes


class KeyManager(Protocol):
    """Envelope encryption: KMS keys wrap per-tenant data keys. ``context`` (the tenant id)
    is bound to the wrapped key as additional authenticated data."""

    def generate_data_key(self, key_ref: str, context: bytes) -> DataKey: ...

    def unwrap(self, key_ref: str, wrapped: bytes, context: bytes) -> bytes:
        """Raises ValueError if the key, the wrapped bytes or the context do not match."""
        ...


class Cipher(Protocol):
    """Authenticated encryption with a data key (AES-256-GCM in the adapter)."""

    def seal(self, key: bytes, plaintext: bytes, aad: bytes) -> bytes: ...

    def open(self, key: bytes, sealed: bytes, aad: bytes) -> bytes:
        """Raises ValueError if the data or ``aad`` was altered or the key is wrong."""
        ...


class PasswordHasher(Protocol):
    """argon2id with a server-side pepper. Used for passwords and recovery codes."""

    def hash(self, secret: str, params: HashParams) -> str: ...

    def verify(self, encoded: str, secret: str) -> bool: ...

    def needs_rehash(self, encoded: str, params: HashParams) -> bool: ...

    def pepper_is_current(self, encoded: str, secret: str) -> bool:
        """For a hash ``verify`` accepted: was it made with the current pepper? False while a
        rotation is under way and the hash still uses an earlier one (O13): sign-in then hashes
        the password again with the current pepper."""
        ...


class RandomSource(Protocol):
    def token_bytes(self, n: int) -> bytes: ...


class CommonPasswords(Protocol):
    """Common and breached passwords (compared lower-cased)."""

    def __contains__(self, password: object) -> bool: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class EmailMessage:
    to: str
    subject: str
    text: str


class Mailer(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class RecordValidator(Protocol):
    """Checks a JSON record against the published schemas (``docs/auth/*.schema.json``).
    Raises InvariantError with a message that holds no field values."""

    def validate_tenant(self, record: JsonValue) -> None: ...

    def validate_identity(self, record: JsonValue) -> None: ...
