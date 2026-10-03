"""Credentials and tenants, kept in the identity store: a separate database with its own role
(P4). The college data zone holds only the profile and role of a user (``tenancy.User``).

The JSON forms are ``docs/auth/identity-record.schema.json`` and
``docs/auth/tenant-registry.schema.json`` (see ``services.identity_backup``).

Nothing here is ever logged or audited: hashes, wrapped keys and encrypted values stay in
the identity store and in encrypted backups."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from tarn_core.domain.booklet import check_aware
from tarn_core.domain.common import JsonValue, check_text
from tarn_core.domain.tenancy import canonical_email, check_email, institution_id_problem
from tarn_core.errors import InvariantError
from tarn_core.ids import AuthSessionId, CollegeId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class HashParams:
    """argon2id cost parameters (memory in KiB)."""

    time_cost: int
    memory_cost: int
    parallelism: int

    def __post_init__(self) -> None:
        if self.time_cost < 1 or self.parallelism < 1:
            raise InvariantError("argon2 time cost and parallelism must be at least 1")
        if self.memory_cost < max(1024, 8 * self.parallelism):
            raise InvariantError("argon2 memory cost must be at least 1024 KiB")


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthPolicy:
    """Per-tenant password and lockout policy (tenant registry ``auth_policy``)."""

    min_password_length: int = 12
    argon_time_cost: int = 3
    argon_memory_cost: int = 65536
    argon_parallelism: int = 4
    max_failed_logins: int = 5
    lockout_minutes: int = 15

    def __post_init__(self) -> None:
        if not 8 <= self.min_password_length <= 128:
            raise InvariantError("minimum password length must be between 8 and 128")
        if self.max_failed_logins < 1 or self.lockout_minutes < 1:
            raise InvariantError("lockout threshold and duration must be at least 1")
        HashParams(  # validates the argon2 parameters
            time_cost=self.argon_time_cost,
            memory_cost=self.argon_memory_cost,
            parallelism=self.argon_parallelism,
        )

    @property
    def hash_params(self) -> HashParams:
        return HashParams(
            time_cost=self.argon_time_cost,
            memory_cost=self.argon_memory_cost,
            parallelism=self.argon_parallelism,
        )

    @property
    def lockout(self) -> timedelta:
        return timedelta(minutes=self.lockout_minutes)


class TenantStatus(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


@dataclass(frozen=True, slots=True, kw_only=True)
class TenantRecord:
    """A college in the tenant registry. ``wrapped_data_key`` is the tenant's data key,
    encrypted by its KMS key (``kms_key_ref``); the plain key is never stored."""

    college_id: CollegeId
    institution_id: str
    name: str
    kms_key_ref: str
    wrapped_data_key: bytes
    data_key_version: int
    policy: AuthPolicy
    status: TenantStatus
    approval_required: bool
    created_at: datetime
    updated_at: datetime
    email_verified_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None

    def __post_init__(self) -> None:
        problem = institution_id_problem(self.institution_id)
        if problem:
            raise InvariantError(problem)
        check_text("tenant name", self.name)
        check_text("KMS key reference", self.kms_key_ref)
        if not self.wrapped_data_key:
            raise InvariantError("a tenant needs a wrapped data key")
        if self.data_key_version < 1:
            raise InvariantError("data key version starts at 1")
        for name in ("created_at", "updated_at", "email_verified_at", "approved_at"):
            value = getattr(self, name)
            if value is not None:
                check_aware(name, value)
        if self.status is TenantStatus.ACTIVE and self.email_verified_at is None:
            raise InvariantError("an active tenant has a verified email")
        if self.status is TenantStatus.ACTIVE and self.approval_required and not self.approved_at:
            raise InvariantError("this tenant needs operator approval before it is active")

    def next_status(self) -> TenantStatus:
        """The status implied by verification and approval (suspension is kept)."""
        if self.status is TenantStatus.SUSPENDED:
            return self.status
        if self.email_verified_at is None:
            return TenantStatus.PENDING_VERIFICATION
        if self.approval_required and self.approved_at is None:
            return TenantStatus.PENDING_APPROVAL
        return TenantStatus.ACTIVE


class IdentityStatus(StrEnum):
    PENDING = "PENDING"  # registered or invited; email not verified or no password yet
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityRecord:
    """A user's credentials. Lockout counters and recovery codes are stored apart from this
    record (``LoginCounters``, ``RecoveryCode``) so they change atomically on their own.

    ``other_recovery_factors`` keeps factors this version does not handle (Shamir secret
    shares) exactly as received, so backups round-trip them; no flow uses them yet."""

    user_id: UserId
    college_id: CollegeId
    login_handle: str
    email_canonical: str
    status: IdentityStatus
    created_at: datetime
    updated_at: datetime
    password_hash: str | None = None
    last_password_change: datetime | None = None
    force_reset: bool = False
    email_verified_at: datetime | None = None
    recovery_version: int = 0
    other_recovery_factors: tuple[JsonValue, ...] = field(default=())

    def __post_init__(self) -> None:
        check_email(self.email_canonical)
        if self.email_canonical != canonical_email(self.email_canonical):
            raise InvariantError("email must be canonical (trimmed, lower case)")
        check_text("login handle", self.login_handle)
        if self.status is IdentityStatus.ACTIVE and self.password_hash is None:
            raise InvariantError("an active identity has a password")
        if (self.password_hash is None) != (self.last_password_change is None):
            raise InvariantError("a password and its change time go together")
        if self.recovery_version < 0:
            raise InvariantError("recovery version must not be negative")
        for name in ("created_at", "updated_at", "last_password_change", "email_verified_at"):
            value = getattr(self, name)
            if value is not None:
                check_aware(name, value)


@dataclass(frozen=True, slots=True, kw_only=True)
class LoginCounters:
    failed_login_attempts: int = 0
    lockout_until: datetime | None = None

    def locked(self, now: datetime) -> bool:
        return self.lockout_until is not None and self.lockout_until > now


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryCode:
    """One of the user's one-time recovery codes; only its argon2id hash is kept."""

    ordinal: int
    code_hash: str
    created_at: datetime
    used_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise InvariantError("recovery code ordinal must not be negative")
        check_aware("created_at", self.created_at)

    @property
    def used(self) -> bool:
        return self.used_at is not None


class TokenPurpose(StrEnum):
    RESET = "reset"  # forgot password / forced reset: 30 minutes
    INVITE = "invite"  # a new teacher sets a first password
    VERIFY_EMAIL = "verify_email"  # registration


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionToken:
    """A single-use emailed link. Only the SHA-256 of the token is stored."""

    token_hash: str
    college_id: CollegeId
    user_id: UserId
    purpose: TokenPurpose
    created_at: datetime
    expires_at: datetime
    used_at: datetime | None = None

    def __post_init__(self) -> None:
        if len(self.token_hash) != 64:
            raise InvariantError("token hash must be a SHA-256 hex digest")
        if self.expires_at <= self.created_at:
            raise InvariantError("a token expires after it is created")

    def usable(self, now: datetime) -> bool:
        return self.used_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthSession:
    """A signed-in browser. The refresh token rotates on every use; the previous hash is kept
    so that a replayed (stolen) refresh token revokes the session."""

    id: AuthSessionId
    college_id: CollegeId
    user_id: UserId
    refresh_hash: str
    remember: bool
    created_at: datetime
    expires_at: datetime
    previous_refresh_hash: str | None = None
    revoked_at: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at
