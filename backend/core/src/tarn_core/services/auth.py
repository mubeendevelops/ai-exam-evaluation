"""Sign-in, sessions, password reset, recovery codes and account administration (P4).

Credentials live in the identity store; profiles, roles and the audit log live in the college
zone. Every method takes ``college_id`` explicitly (rule 3).

Failures that must leave a trace (a failed sign-in increments the lockout counter, a replayed
refresh token revokes its session) are *returned*, not raised, so the caller's unit of work
commits them. Errors raised here change nothing."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.identity import (
    ActionToken,
    AuthSession,
    IdentityRecord,
    IdentityStatus,
    LoginCounters,
    RecoveryCode,
    TenantRecord,
    TenantStatus,
    TokenPurpose,
)
from tarn_core.domain.tenancy import Role, User, canonical_email, check_email
from tarn_core.errors import NotFoundError, PermissionDeniedError, TokenError
from tarn_core.ids import AuthSessionId, CollegeId, UserId
from tarn_core.ports.identity import (
    Cipher,
    CommonPasswords,
    EmailMessage,
    IdentityStore,
    KeyManager,
    Mailer,
    PasswordHasher,
    RandomSource,
)
from tarn_core.ports.repositories import UserRepository
from tarn_core.services._support import Runtime
from tarn_core.services.passwords import (
    RECOVERY_CODE_COUNT,
    check_password,
    new_link_token,
    new_recovery_code,
    new_refresh_token,
    normalise_recovery_code,
    sha256_hex,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthSettings:
    public_url: str = "http://localhost:5173"
    reset_link: timedelta = timedelta(minutes=30)
    invite_link: timedelta = timedelta(hours=72)
    verify_link: timedelta = timedelta(hours=24)
    session: timedelta = timedelta(hours=12)
    remembered_session: timedelta = timedelta(days=30)
    signup_requires_approval: bool = True
    default_kms_key_ref: str = "local:tarn-dev"


@dataclass(frozen=True, slots=True, kw_only=True)
class AuthKit:
    """The non-repository ports the auth services use."""

    hasher: PasswordHasher
    keys: KeyManager
    cipher: Cipher
    random: RandomSource
    mailer: Mailer
    common_passwords: CommonPasswords
    settings: AuthSettings


# --- outcomes ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SignedIn:
    user: User
    session: AuthSession
    refresh_token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResetRequired:
    """Password right, but an admin forced a reset: the client goes to the reset page."""

    user_id: UserId
    reset_token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Refused:
    """Sign-in, refresh or recovery refused. ``reason`` is for the audit log and tests; the
    user always sees one generic message."""

    reason: str


type LoginOutcome = SignedIn | ResetRequired | Refused


@dataclass(frozen=True, slots=True, kw_only=True)
class Refreshed:
    user: User
    session: AuthSession
    refresh_token: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AccountView:
    user: User
    status: IdentityStatus
    locked_until: datetime | None
    force_reset: bool


def _link(settings: AuthSettings, path: str, token: str) -> str:
    return f"{settings.public_url.rstrip('/')}/{path}?token={token}"


class _Base:
    def __init__(
        self, *, identity: IdentityStore, users: UserRepository, kit: AuthKit, runtime: Runtime
    ) -> None:
        self._identity = identity
        self._users = users
        self._kit = kit
        self._rt = runtime

    @property
    def _now(self) -> datetime:
        return self._rt.clock.now()

    def _issue_link(
        self, college_id: CollegeId, user_id: UserId, purpose: TokenPurpose, ttl: timedelta
    ) -> str:
        token = new_link_token(self._kit.random, college_id)
        now = self._now
        self._identity.add_action_token(
            college_id,
            ActionToken(
                token_hash=sha256_hex(token),
                college_id=college_id,
                user_id=user_id,
                purpose=purpose,
                created_at=now,
                expires_at=now + ttl,
            ),
        )
        return token

    def _usable_token(
        self, college_id: CollegeId, token: str, purpose: TokenPurpose
    ) -> ActionToken:
        found = self._identity.find_action_token(college_id, sha256_hex(token), purpose)
        if found is None or not found.usable(self._now):
            raise TokenError("the link is not valid or has expired")
        return found

    def _consume_token(self, college_id: CollegeId, token: str, purpose: TokenPurpose) -> None:
        if (
            self._identity.use_action_token(college_id, sha256_hex(token), purpose, self._now)
            is None
        ):
            raise TokenError("the link is not valid or has expired")

    def _set_password(
        self, tenant: TenantRecord, identity: IdentityRecord, password: str, **changes: object
    ) -> IdentityRecord:
        now = self._now
        updated = replace(
            identity,
            password_hash=self._kit.hasher.hash(password, tenant.policy.hash_params),
            last_password_change=now,
            force_reset=False,
            updated_at=now,
            **changes,  # type: ignore[arg-type]  # dataclass fields, checked by __post_init__
        )
        self._identity.save_identity(tenant.college_id, updated)
        self._identity.set_counters(tenant.college_id, identity.user_id, LoginCounters())
        self._identity.revoke_user_sessions(tenant.college_id, identity.user_id, now)
        return updated

    def _check_new_password(self, tenant: TenantRecord, email: str, password: str) -> None:
        check_password(
            password,
            tenant.policy,
            self._kit.common_passwords,
            email=email,
            institution_id=tenant.institution_id,
        )

    def _send(self, to: str, subject: str, text: str) -> None:
        self._kit.mailer.send(EmailMessage(to=to, subject=subject, text=text))


class AuthService(_Base):
    """What a user does for themself: sign in, refresh, sign out, reset, recover."""

    # --- sign-in ----------------------------------------------------------------------------

    def login(
        self, college_id: CollegeId, email: str, password: str, *, remember: bool = False
    ) -> LoginOutcome:
        tenant = self._identity.get_tenant(college_id)
        now = self._now
        identity = self._identity.find_identity(college_id, canonical_email(email))
        if identity is None or identity.password_hash is None:
            # Same work as a real check, so timing does not reveal which emails exist.
            self._kit.hasher.hash(password, tenant.policy.hash_params)
            self._rt.record(college_id, None, AuditAction.LOGIN_FAILED, after={"reason": "unknown"})
            return Refused(reason="unknown")
        uid = identity.user_id
        if self._identity.counters(college_id, uid).locked(now):
            self._rt.record(college_id, uid, AuditAction.LOGIN_FAILED, after={"reason": "locked"})
            return Refused(reason="locked")
        if not self._kit.hasher.verify(identity.password_hash, password):
            counters = self._identity.record_failed_login(college_id, uid, now, tenant.policy)
            self._rt.record(
                college_id,
                uid,
                AuditAction.LOGIN_FAILED,
                after={"reason": "password", "failed_attempts": counters.failed_login_attempts},
            )
            if counters.locked(now):
                self._rt.record(
                    college_id,
                    uid,
                    AuditAction.LOCKED_OUT,
                    after={"until": counters.lockout_until.isoformat()}
                    if counters.lockout_until
                    else None,
                )
            return Refused(reason="password")
        # The password is right; anything else that blocks sign-in is about the account.
        reason = self._blocked(tenant, identity)
        if reason:
            self._rt.record(college_id, uid, AuditAction.LOGIN_FAILED, after={"reason": reason})
            return Refused(reason=reason)
        user = self._users.get(college_id, uid)
        self._identity.set_counters(college_id, uid, LoginCounters())
        if self._kit.hasher.needs_rehash(identity.password_hash, tenant.policy.hash_params):
            identity = replace(
                identity,
                password_hash=self._kit.hasher.hash(password, tenant.policy.hash_params),
                updated_at=now,
            )
            self._identity.save_identity(college_id, identity)
        if identity.force_reset:
            token = self._issue_link(
                college_id, uid, TokenPurpose.RESET, self._kit.settings.reset_link
            )
            self._rt.record(college_id, uid, AuditAction.LOGIN, after={"next": "reset_password"})
            return ResetRequired(user_id=uid, reset_token=token)
        session, refresh = self._new_session(college_id, uid, remember)
        self._rt.record(college_id, uid, AuditAction.LOGIN, after={"remember": remember})
        return SignedIn(user=user, session=session, refresh_token=refresh)

    def _blocked(self, tenant: TenantRecord, identity: IdentityRecord) -> str | None:
        if tenant.status is not TenantStatus.ACTIVE:
            return "tenant_" + tenant.status.value.lower()
        if identity.status is not IdentityStatus.ACTIVE:
            return "account_" + identity.status.value.lower()
        try:
            user = self._users.get(tenant.college_id, identity.user_id)
        except NotFoundError:
            return "no_profile"
        return None if user.active else "account_disabled"

    def _new_session(
        self, college_id: CollegeId, user_id: UserId, remember: bool
    ) -> tuple[AuthSession, str]:
        session_id = self._rt.new_id(AuthSessionId)
        token = new_refresh_token(self._kit.random, college_id, session_id)
        now = self._now
        ttl = self._kit.settings.remembered_session if remember else self._kit.settings.session
        session = AuthSession(
            id=session_id,
            college_id=college_id,
            user_id=user_id,
            refresh_hash=sha256_hex(token),
            remember=remember,
            created_at=now,
            expires_at=now + ttl,
        )
        self._identity.add_session(college_id, session)
        return session, token

    # --- sessions ---------------------------------------------------------------------------

    def refresh(
        self, college_id: CollegeId, session_id: AuthSessionId, token: str
    ) -> Refreshed | Refused:
        """Rotate the refresh token. A token that was already rotated away is a replay: the
        whole session is revoked."""
        try:
            session = self._identity.get_session(college_id, session_id)
        except NotFoundError:
            return Refused(reason="unknown")
        now = self._now
        presented = sha256_hex(token)
        if presented == session.previous_refresh_hash and session.revoked_at is None:
            self._identity.revoke_session(college_id, session_id, now)
            self._rt.record(college_id, session.user_id, AuditAction.SESSION_REUSED)
            return Refused(reason="reused")
        if presented != session.refresh_hash or not session.active(now):
            return Refused(reason="invalid")
        tenant = self._identity.get_tenant(college_id)
        identity = self._identity.get_identity(college_id, session.user_id)
        reason = self._blocked(tenant, identity)
        if reason:
            self._identity.revoke_session(college_id, session_id, now)
            return Refused(reason=reason)
        new_token = new_refresh_token(self._kit.random, college_id, session_id)
        if not self._identity.rotate_session(
            college_id, session_id, presented, sha256_hex(new_token)
        ):
            return Refused(reason="raced")
        user = self._users.get(college_id, session.user_id)
        return Refreshed(
            user=user,
            session=self._identity.get_session(college_id, session_id),
            refresh_token=new_token,
        )

    def session_active(self, college_id: CollegeId, session_id: AuthSessionId) -> bool:
        try:
            return self._identity.get_session(college_id, session_id).active(self._now)
        except NotFoundError:
            return False

    def logout(self, college_id: CollegeId, user_id: UserId, session_id: AuthSessionId) -> None:
        session = self._identity.get_session(college_id, session_id)
        if session.user_id != user_id:
            raise PermissionDeniedError("not your session")
        self._identity.revoke_session(college_id, session_id, self._now)
        self._rt.record(college_id, user_id, AuditAction.LOGOUT)

    # --- forgot password --------------------------------------------------------------------

    def request_reset(self, college_id: CollegeId, email: str) -> None:
        """Email a single-use reset link. Says nothing about whether the email exists."""
        identity = self._identity.find_identity(college_id, canonical_email(email))
        if identity is None or identity.status is not IdentityStatus.ACTIVE:
            return
        token = self._issue_link(
            college_id, identity.user_id, TokenPurpose.RESET, self._kit.settings.reset_link
        )
        minutes = int(self._kit.settings.reset_link.total_seconds() // 60)
        self._send(
            identity.login_handle,
            "Reset your Tarn password",
            "Someone asked to reset the password of your Tarn AI Evaluation account.\n\n"
            f"Open this link within {minutes} minutes to choose a new password:\n"
            f"{_link(self._kit.settings, 'reset-password', token)}\n\n"
            "The link works once. If you didn't ask for this, ignore this email.",
        )
        self._rt.record(college_id, identity.user_id, AuditAction.RESET_REQUESTED)

    def reset_password(self, college_id: CollegeId, token: str, new_password: str) -> None:
        found = self._usable_token(college_id, token, TokenPurpose.RESET)
        tenant = self._identity.get_tenant(college_id)
        identity = self._identity.get_identity(college_id, found.user_id)
        self._check_new_password(tenant, identity.email_canonical, new_password)
        self._consume_token(college_id, token, TokenPurpose.RESET)
        self._set_password(tenant, identity, new_password)
        self._rt.record(college_id, identity.user_id, AuditAction.PASSWORD_RESET)

    # --- recovery codes ---------------------------------------------------------------------

    def issue_recovery_codes(
        self, college_id: CollegeId, user_id: UserId, current_password: str
    ) -> tuple[str, ...]:
        """Ten new one-time codes, replacing any earlier set. Shown once; only hashes kept."""
        tenant = self._identity.get_tenant(college_id)
        identity = self._identity.get_identity(college_id, user_id)
        if identity.password_hash is None or not self._kit.hasher.verify(
            identity.password_hash, current_password
        ):
            raise PermissionDeniedError("the current password is wrong")
        now = self._now
        params = tenant.policy.hash_params
        codes = tuple(new_recovery_code(self._kit.random) for _ in range(RECOVERY_CODE_COUNT))
        stored = [
            RecoveryCode(
                ordinal=i,
                code_hash=self._kit.hasher.hash(normalise_recovery_code(code), params),
                created_at=now,
            )
            for i, code in enumerate(codes)
        ]
        self._identity.replace_recovery_codes(college_id, user_id, stored)
        self._identity.save_identity(
            college_id,
            replace(
                identity,
                recovery_version=identity.recovery_version + 1,
                updated_at=now,
            ),
        )
        self._rt.record(
            college_id, user_id, AuditAction.RECOVERY_CODES_ISSUED, after={"count": len(codes)}
        )
        return codes

    def recovery_codes_left(self, college_id: CollegeId, user_id: UserId) -> int:
        return sum(not c.used for c in self._identity.recovery_codes(college_id, user_id))

    def recover(
        self, college_id: CollegeId, email: str, code: str, new_password: str
    ) -> Refused | None:
        """Set a new password with a one-time recovery code. Wrong codes count towards the
        lockout like wrong passwords. Returns None on success."""
        tenant = self._identity.get_tenant(college_id)
        # Policy first: its answer must not depend on whether the email exists.
        self._check_new_password(tenant, canonical_email(email), new_password)
        identity = self._identity.find_identity(college_id, canonical_email(email))
        if identity is None or identity.status is not IdentityStatus.ACTIVE:
            return Refused(reason="unknown")
        uid = identity.user_id
        now = self._now
        if self._identity.counters(college_id, uid).locked(now):
            self._rt.record(
                college_id, uid, AuditAction.RECOVERY_FAILED, after={"reason": "locked"}
            )
            return Refused(reason="locked")
        wanted = normalise_recovery_code(code)
        match = next(
            (
                c
                for c in self._identity.recovery_codes(college_id, uid)
                if not c.used and self._kit.hasher.verify(c.code_hash, wanted)
            ),
            None,
        )
        if match is None or not self._identity.use_recovery_code(
            college_id, uid, match.ordinal, now
        ):
            counters = self._identity.record_failed_login(college_id, uid, now, tenant.policy)
            self._rt.record(college_id, uid, AuditAction.RECOVERY_FAILED, after={"reason": "code"})
            if counters.locked(now):
                self._rt.record(college_id, uid, AuditAction.LOCKED_OUT)
            return Refused(reason="code")
        self._set_password(tenant, identity, new_password)
        self._rt.record(
            college_id,
            uid,
            AuditAction.RECOVERY_CODE_USED,
            after={"codes_left": self.recovery_codes_left(college_id, uid)},
        )
        return None

    # --- signed-in changes ------------------------------------------------------------------

    def change_password(
        self, college_id: CollegeId, user_id: UserId, current: str, new_password: str
    ) -> None:
        tenant = self._identity.get_tenant(college_id)
        identity = self._identity.get_identity(college_id, user_id)
        if identity.password_hash is None or not self._kit.hasher.verify(
            identity.password_hash, current
        ):
            raise PermissionDeniedError("the current password is wrong")
        self._check_new_password(tenant, identity.email_canonical, new_password)
        self._set_password(tenant, identity, new_password)
        self._rt.record(college_id, user_id, AuditAction.PASSWORD_CHANGED)

    def accept_invite(self, college_id: CollegeId, token: str, password: str) -> None:
        """A new teacher sets a first password from the invitation link."""
        found = self._usable_token(college_id, token, TokenPurpose.INVITE)
        tenant = self._identity.get_tenant(college_id)
        identity = self._identity.get_identity(college_id, found.user_id)
        if identity.status is not IdentityStatus.PENDING:
            raise TokenError("the link is not valid or has expired")
        self._check_new_password(tenant, identity.email_canonical, password)
        self._consume_token(college_id, token, TokenPurpose.INVITE)
        self._set_password(
            tenant,
            identity,
            password,
            status=IdentityStatus.ACTIVE,
            email_verified_at=self._now,
        )
        self._rt.record(college_id, identity.user_id, AuditAction.ACCOUNT_ACTIVATED)


class AccountService(_Base):
    """College admins manage teacher accounts. Teachers can do none of this."""

    def _admin(self, college_id: CollegeId, actor_id: UserId) -> User:
        actor = self._users.get(college_id, actor_id)
        if actor.role is not Role.ADMIN or not actor.active:
            raise PermissionDeniedError("only a college admin can manage accounts")
        return actor

    def _teacher(self, college_id: CollegeId, user_id: UserId) -> User:
        user = self._users.get(college_id, user_id)
        if user.role is not Role.TEACHER:
            raise PermissionDeniedError("admins manage teacher accounts only")
        return user

    def list_accounts(self, college_id: CollegeId, actor_id: UserId) -> Sequence[AccountView]:
        self._admin(college_id, actor_id)
        views = []
        for user in self._users.list(college_id):
            identity = self._identity.get_identity(college_id, user.id)
            counters = self._identity.counters(college_id, user.id)
            views.append(
                AccountView(
                    user=user,
                    status=identity.status,
                    locked_until=counters.lockout_until if counters.locked(self._now) else None,
                    force_reset=identity.force_reset,
                )
            )
        return views

    def invite_teacher(
        self, college_id: CollegeId, actor_id: UserId, *, display_name: str, email: str
    ) -> User:
        self._admin(college_id, actor_id)
        handle = email.strip()
        canonical = canonical_email(handle)
        check_email(canonical)
        now = self._now
        user = User(
            id=self._rt.new_id(UserId),
            college_id=college_id,
            display_name=display_name.strip(),
            email=handle,
            role=Role.TEACHER,
        )
        # The identity first: it refuses a duplicate email before anything else is written.
        self._identity.save_identity(
            college_id,
            IdentityRecord(
                user_id=user.id,
                college_id=college_id,
                login_handle=handle,
                email_canonical=canonical,
                status=IdentityStatus.PENDING,
                created_at=now,
                updated_at=now,
            ),
        )
        self._users.save(college_id, user)
        token = self._issue_link(
            college_id, user.id, TokenPurpose.INVITE, self._kit.settings.invite_link
        )
        tenant = self._identity.get_tenant(college_id)
        self._send(
            handle,
            "You're invited to Tarn AI Evaluation",
            f"An administrator of {tenant.name} created a teacher account for you.\n\n"
            f"Institution ID: {tenant.institution_id}\n"
            f"Choose your password here (the link works once):\n"
            f"{_link(self._kit.settings, 'accept-invite', token)}",
        )
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.ACCOUNT_CREATED,
            after={"user_id": str(user.id), "role": user.role.value},
        )
        return user

    def set_active(
        self, college_id: CollegeId, actor_id: UserId, user_id: UserId, *, active: bool
    ) -> User:
        self._admin(college_id, actor_id)
        user = self._teacher(college_id, user_id)
        identity = self._identity.get_identity(college_id, user_id)
        now = self._now
        if identity.status is not IdentityStatus.PENDING:
            status = IdentityStatus.ACTIVE if active else IdentityStatus.DISABLED
            self._identity.save_identity(
                college_id, replace(identity, status=status, updated_at=now)
            )
        updated = replace(user, active=active)
        self._users.save(college_id, updated)
        if not active:
            self._identity.revoke_user_sessions(college_id, user_id, now)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.ACCOUNT_ENABLED if active else AuditAction.ACCOUNT_DISABLED,
            before={"user_id": str(user_id), "active": user.active},
            after={"user_id": str(user_id), "active": active},
        )
        return updated

    def force_reset(self, college_id: CollegeId, actor_id: UserId, user_id: UserId) -> None:
        """The user must choose a new password at the next sign-in; open sessions end and a
        reset link is emailed."""
        self._admin(college_id, actor_id)
        self._teacher(college_id, user_id)
        identity = self._identity.get_identity(college_id, user_id)
        now = self._now
        self._identity.save_identity(
            college_id, replace(identity, force_reset=True, updated_at=now)
        )
        self._identity.revoke_user_sessions(college_id, user_id, now)
        if identity.status is IdentityStatus.ACTIVE:
            token = self._issue_link(
                college_id, user_id, TokenPurpose.RESET, self._kit.settings.reset_link
            )
            self._send(
                identity.login_handle,
                "Your administrator asked you to reset your Tarn password",
                "Choose a new password here (the link works once):\n"
                f"{_link(self._kit.settings, 'reset-password', token)}",
            )
        self._rt.record(
            college_id, actor_id, AuditAction.ACCOUNT_FORCE_RESET, after={"user_id": str(user_id)}
        )

    def unlock(self, college_id: CollegeId, actor_id: UserId, user_id: UserId) -> None:
        self._admin(college_id, actor_id)
        self._teacher(college_id, user_id)
        self._identity.set_counters(college_id, user_id, LoginCounters())
        self._rt.record(
            college_id, actor_id, AuditAction.ACCOUNT_UNLOCKED, after={"user_id": str(user_id)}
        )
