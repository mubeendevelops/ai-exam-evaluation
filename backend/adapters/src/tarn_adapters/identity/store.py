"""PostgreSQL implementation of ``tarn_core.ports.identity.IdentityStore``.

One store works on the connection of one identity session (one transaction). The first
per-tenant call binds the transaction to that college (``set_config('app.college_id', …,
true)``), and row-level security then scopes every statement to it; a call for another
college in the same transaction is refused (``TenantViolationError``). Tenant lookups by
Institution ID need no binding: ``tenants`` is readable by the app role.

Queries also filter by ``college_id``, as the college repositories do."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, and_, case, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from tarn_adapters.identity import metadata as m
from tarn_adapters.postgres.repositories import translate
from tarn_core.domain.identity import (
    ActionToken,
    AuthPolicy,
    AuthSession,
    IdentityRecord,
    IdentityStatus,
    LoginCounters,
    RecoveryCode,
    TenantRecord,
    TenantStatus,
    TokenPurpose,
)
from tarn_core.errors import AlreadyExistsError, NotFoundError, TenantViolationError
from tarn_core.ids import AuthSessionId, CollegeId, UserId

UNIQUE_VIOLATION = "23505"


def _tenant(r: Row[Any]) -> TenantRecord:
    return TenantRecord(
        college_id=CollegeId(r.college_id),
        institution_id=r.institution_id,
        name=r.name,
        kms_key_ref=r.kms_key_ref,
        wrapped_data_key=bytes(r.wrapped_data_key),
        data_key_version=r.data_key_version,
        policy=AuthPolicy(
            min_password_length=r.min_password_length,
            argon_time_cost=r.argon_time_cost,
            argon_memory_cost=r.argon_memory_cost,
            argon_parallelism=r.argon_parallelism,
            max_failed_logins=r.max_failed_logins,
            lockout_minutes=r.lockout_minutes,
        ),
        status=TenantStatus(r.status),
        approval_required=r.approval_required,
        created_at=r.created_at,
        updated_at=r.updated_at,
        email_verified_at=r.email_verified_at,
        approved_at=r.approved_at,
        approved_by=r.approved_by,
    )


def _tenant_values(t: TenantRecord) -> dict[str, object]:
    p = t.policy
    return {
        "college_id": t.college_id,
        "institution_id": t.institution_id,
        "name": t.name,
        "kms_key_ref": t.kms_key_ref,
        "wrapped_data_key": t.wrapped_data_key,
        "data_key_version": t.data_key_version,
        "min_password_length": p.min_password_length,
        "argon_time_cost": p.argon_time_cost,
        "argon_memory_cost": p.argon_memory_cost,
        "argon_parallelism": p.argon_parallelism,
        "max_failed_logins": p.max_failed_logins,
        "lockout_minutes": p.lockout_minutes,
        "status": t.status.value,
        "approval_required": t.approval_required,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "email_verified_at": t.email_verified_at,
        "approved_at": t.approved_at,
        "approved_by": t.approved_by,
    }


def _identity(r: Row[Any]) -> IdentityRecord:
    return IdentityRecord(
        user_id=UserId(r.user_id),
        college_id=CollegeId(r.college_id),
        login_handle=r.login_handle,
        email_canonical=r.email_canonical,
        status=IdentityStatus(r.status),
        password_hash=r.password_hash,
        last_password_change=r.last_password_change,
        force_reset=r.force_reset,
        email_verified_at=r.email_verified_at,
        recovery_version=r.recovery_version,
        other_recovery_factors=tuple(r.other_recovery_factors),
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _identity_values(i: IdentityRecord) -> dict[str, object]:
    return {
        "user_id": i.user_id,
        "college_id": i.college_id,
        "login_handle": i.login_handle,
        "email_canonical": i.email_canonical,
        "status": i.status.value,
        "password_hash": i.password_hash,
        "last_password_change": i.last_password_change,
        "force_reset": i.force_reset,
        "email_verified_at": i.email_verified_at,
        "recovery_version": i.recovery_version,
        "other_recovery_factors": list(i.other_recovery_factors),
        "created_at": i.created_at,
        "updated_at": i.updated_at,
    }


def _token(r: Row[Any]) -> ActionToken:
    return ActionToken(
        token_hash=r.token_hash,
        college_id=CollegeId(r.college_id),
        user_id=UserId(r.user_id),
        purpose=TokenPurpose(r.purpose),
        created_at=r.created_at,
        expires_at=r.expires_at,
        used_at=r.used_at,
    )


def _session(r: Row[Any]) -> AuthSession:
    return AuthSession(
        id=AuthSessionId(r.id),
        college_id=CollegeId(r.college_id),
        user_id=UserId(r.user_id),
        refresh_hash=r.refresh_hash,
        previous_refresh_hash=r.previous_refresh_hash,
        remember=r.remember,
        created_at=r.created_at,
        expires_at=r.expires_at,
        revoked_at=r.revoked_at,
    )


class PgIdentityStore:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn
        self._bound: CollegeId | None = None

    @property
    def bound_college(self) -> CollegeId | None:
        return self._bound

    def _bind(self, college_id: CollegeId) -> None:
        if self._bound is None:
            self._conn.execute(
                text("SELECT set_config('app.college_id', :college, true)"),
                {"college": str(college_id)},
            )
            self._bound = college_id
        elif self._bound != college_id:
            raise TenantViolationError("one identity session serves one college")

    @contextmanager
    def _writing(self, what: str, taken: str) -> Iterator[None]:
        try:
            with self._conn.begin_nested():
                yield
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
                raise AlreadyExistsError(taken) from exc
            translate(exc, what, TenantViolationError)

    # --- tenants ----------------------------------------------------------------------------

    def find_tenant(self, institution_id: str) -> TenantRecord | None:
        row = self._conn.execute(
            select(m.tenants).where(m.tenants.c.institution_id == institution_id)
        ).first()
        return None if row is None else _tenant(row)

    def list_tenants(self, status: TenantStatus | None = None) -> Sequence[TenantRecord]:
        query = select(m.tenants).order_by(m.tenants.c.seq)
        if status is not None:
            query = query.where(m.tenants.c.status == status.value)
        return [_tenant(r) for r in self._conn.execute(query)]

    def get_tenant(self, college_id: CollegeId) -> TenantRecord:
        self._bind(college_id)
        row = self._conn.execute(
            select(m.tenants).where(m.tenants.c.college_id == college_id)
        ).first()
        if row is None:
            raise NotFoundError(f"tenant {college_id}")
        return _tenant(row)

    def save_tenant(self, tenant: TenantRecord) -> None:
        self._bind(tenant.college_id)
        values = _tenant_values(tenant)
        stmt = insert(m.tenants).values(**values)
        updates = {k: stmt.excluded[k] for k in values if k not in {"college_id", "created_at"}}
        with self._writing(
            f"tenant {tenant.college_id}", "This Institution ID is already registered."
        ):
            self._conn.execute(
                stmt.on_conflict_do_update(index_elements=["college_id"], set_=updates)
            )

    # --- identities -------------------------------------------------------------------------

    def _identity_row(self, college_id: CollegeId, **where: object) -> Row[Any] | None:
        self._bind(college_id)
        conditions = [m.identities.c.college_id == college_id]
        conditions += [m.identities.c[k] == v for k, v in where.items()]
        return self._conn.execute(select(m.identities).where(and_(*conditions))).first()

    def get_identity(self, college_id: CollegeId, user_id: UserId) -> IdentityRecord:
        row = self._identity_row(college_id, user_id=user_id)
        if row is None:
            raise NotFoundError(f"identity {user_id}")
        return _identity(row)

    def find_identity(self, college_id: CollegeId, email_canonical: str) -> IdentityRecord | None:
        row = self._identity_row(college_id, email_canonical=email_canonical)
        return None if row is None else _identity(row)

    def list_identities(self, college_id: CollegeId) -> Sequence[IdentityRecord]:
        self._bind(college_id)
        rows = self._conn.execute(
            select(m.identities)
            .where(m.identities.c.college_id == college_id)
            .order_by(m.identities.c.seq)
        )
        return [_identity(r) for r in rows]

    def save_identity(self, college_id: CollegeId, identity: IdentityRecord) -> None:
        self._bind(college_id)
        if identity.college_id != college_id:
            raise TenantViolationError(f"identity of another college written through {college_id}")
        values = _identity_values(identity)
        stmt = insert(m.identities).values(**values)
        updates = {k: stmt.excluded[k] for k in values if k not in {"user_id", "college_id"}}
        with self._writing(
            f"identity {identity.user_id}", "An account with this email already exists."
        ):
            # RETURNING, not rowcount: an upsert reports no rowcount through SQLAlchemy.
            written = self._conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["user_id"],
                    set_=updates,
                    where=m.identities.c.college_id == college_id,
                ).returning(m.identities.c.user_id)
            ).first()
            if written is None:
                raise TenantViolationError(f"identity {identity.user_id} of another college")

    # --- counters ---------------------------------------------------------------------------

    def counters(self, college_id: CollegeId, user_id: UserId) -> LoginCounters:
        self._bind(college_id)
        row = self._conn.execute(
            select(m.login_counters).where(
                m.login_counters.c.college_id == college_id,
                m.login_counters.c.user_id == user_id,
            )
        ).first()
        if row is None:
            self.get_identity(college_id, user_id)  # NotFoundError for unknown users
            return LoginCounters()
        return LoginCounters(
            failed_login_attempts=row.failed_login_attempts, lockout_until=row.lockout_until
        )

    def record_failed_login(
        self, college_id: CollegeId, user_id: UserId, now: datetime, policy: AuthPolicy
    ) -> LoginCounters:
        """One statement: the row lock makes concurrent failures count exactly."""
        self._bind(college_id)
        c = m.login_counters.c
        reached = c.failed_login_attempts + 1 >= policy.max_failed_logins
        first_reached = policy.max_failed_logins <= 1
        stmt = insert(m.login_counters).values(
            user_id=user_id,
            college_id=college_id,
            failed_login_attempts=0 if first_reached else 1,
            lockout_until=now + policy.lockout if first_reached else None,
        )
        upsert = stmt.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "failed_login_attempts": case((reached, 0), else_=c.failed_login_attempts + 1),
                "lockout_until": case((reached, now + policy.lockout), else_=c.lockout_until),
            },
            where=c.college_id == college_id,
        ).returning(c.failed_login_attempts, c.lockout_until)
        with self._writing(f"counters of {user_id}", "counters"):
            row = self._conn.execute(upsert).one()
        return LoginCounters(failed_login_attempts=row[0], lockout_until=row[1])

    def set_counters(self, college_id: CollegeId, user_id: UserId, counters: LoginCounters) -> None:
        self._bind(college_id)
        stmt = insert(m.login_counters).values(
            user_id=user_id,
            college_id=college_id,
            failed_login_attempts=counters.failed_login_attempts,
            lockout_until=counters.lockout_until,
        )
        with self._writing(f"counters of {user_id}", "counters"):
            self._conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["user_id"],
                    set_={
                        "failed_login_attempts": stmt.excluded.failed_login_attempts,
                        "lockout_until": stmt.excluded.lockout_until,
                    },
                    where=m.login_counters.c.college_id == college_id,
                )
            )

    # --- recovery codes ---------------------------------------------------------------------

    def recovery_codes(self, college_id: CollegeId, user_id: UserId) -> Sequence[RecoveryCode]:
        self._bind(college_id)
        rows = self._conn.execute(
            select(m.recovery_codes)
            .where(
                m.recovery_codes.c.college_id == college_id,
                m.recovery_codes.c.user_id == user_id,
            )
            .order_by(m.recovery_codes.c.ordinal)
        )
        return [
            RecoveryCode(
                ordinal=r.ordinal, code_hash=r.code_hash, created_at=r.created_at, used_at=r.used_at
            )
            for r in rows
        ]

    def replace_recovery_codes(
        self, college_id: CollegeId, user_id: UserId, codes: Sequence[RecoveryCode]
    ) -> None:
        self._bind(college_id)
        with self._writing(f"recovery codes of {user_id}", "recovery codes"):
            self._conn.execute(
                delete(m.recovery_codes).where(
                    m.recovery_codes.c.college_id == college_id,
                    m.recovery_codes.c.user_id == user_id,
                )
            )
            if codes:
                self._conn.execute(
                    insert(m.recovery_codes),
                    [
                        {
                            "college_id": college_id,
                            "user_id": user_id,
                            "ordinal": c.ordinal,
                            "code_hash": c.code_hash,
                            "created_at": c.created_at,
                            "used_at": c.used_at,
                        }
                        for c in codes
                    ],
                )

    def use_recovery_code(
        self, college_id: CollegeId, user_id: UserId, ordinal: int, now: datetime
    ) -> bool:
        self._bind(college_id)
        c = m.recovery_codes.c
        result = self._conn.execute(
            update(m.recovery_codes)
            .where(
                c.college_id == college_id,
                c.user_id == user_id,
                c.ordinal == ordinal,
                c.used_at.is_(None),
            )
            .values(used_at=now)
        )
        return result.rowcount == 1

    # --- single-use links -------------------------------------------------------------------

    def add_action_token(self, college_id: CollegeId, token: ActionToken) -> None:
        self._bind(college_id)
        if token.college_id != college_id:
            raise TenantViolationError("token of another college")
        c = m.action_tokens.c
        with self._writing("action token", "action token"):
            self._conn.execute(
                update(m.action_tokens)
                .where(
                    c.college_id == college_id,
                    c.user_id == token.user_id,
                    c.purpose == token.purpose.value,
                    c.used_at.is_(None),
                )
                .values(used_at=token.created_at)
            )
            self._conn.execute(
                insert(m.action_tokens).values(
                    token_hash=token.token_hash,
                    college_id=college_id,
                    user_id=token.user_id,
                    purpose=token.purpose.value,
                    created_at=token.created_at,
                    expires_at=token.expires_at,
                    used_at=token.used_at,
                )
            )

    def find_action_token(
        self, college_id: CollegeId, token_hash: str, purpose: TokenPurpose
    ) -> ActionToken | None:
        self._bind(college_id)
        c = m.action_tokens.c
        row = self._conn.execute(
            select(m.action_tokens).where(
                c.college_id == college_id, c.token_hash == token_hash, c.purpose == purpose.value
            )
        ).first()
        return None if row is None else _token(row)

    def use_action_token(
        self, college_id: CollegeId, token_hash: str, purpose: TokenPurpose, now: datetime
    ) -> ActionToken | None:
        """One conditional UPDATE: two concurrent uses cannot both succeed."""
        self._bind(college_id)
        c = m.action_tokens.c
        row = self._conn.execute(
            update(m.action_tokens)
            .where(
                c.college_id == college_id,
                c.token_hash == token_hash,
                c.purpose == purpose.value,
                c.used_at.is_(None),
                c.expires_at > now,
            )
            .values(used_at=now)
            .returning(*m.action_tokens.c)
        ).first()
        return None if row is None else _token(row)

    # --- sessions ---------------------------------------------------------------------------

    def add_session(self, college_id: CollegeId, session: AuthSession) -> None:
        self._bind(college_id)
        if session.college_id != college_id:
            raise TenantViolationError("session of another college")
        with self._writing("session", "session"):
            self._conn.execute(
                insert(m.auth_sessions).values(
                    id=session.id,
                    college_id=college_id,
                    user_id=session.user_id,
                    refresh_hash=session.refresh_hash,
                    previous_refresh_hash=session.previous_refresh_hash,
                    remember=session.remember,
                    created_at=session.created_at,
                    expires_at=session.expires_at,
                    revoked_at=session.revoked_at,
                )
            )

    def get_session(self, college_id: CollegeId, session_id: AuthSessionId) -> AuthSession:
        self._bind(college_id)
        row = self._conn.execute(
            select(m.auth_sessions).where(
                m.auth_sessions.c.college_id == college_id, m.auth_sessions.c.id == session_id
            )
        ).first()
        if row is None:
            raise NotFoundError(f"session {session_id}")
        return _session(row)

    def rotate_session(
        self, college_id: CollegeId, session_id: AuthSessionId, old_hash: str, new_hash: str
    ) -> bool:
        self._bind(college_id)
        c = m.auth_sessions.c
        result = self._conn.execute(
            update(m.auth_sessions)
            .where(
                c.college_id == college_id,
                c.id == session_id,
                c.refresh_hash == old_hash,
                c.revoked_at.is_(None),
            )
            .values(refresh_hash=new_hash, previous_refresh_hash=old_hash)
        )
        return result.rowcount == 1

    def revoke_session(
        self, college_id: CollegeId, session_id: AuthSessionId, now: datetime
    ) -> None:
        self._bind(college_id)
        c = m.auth_sessions.c
        self._conn.execute(
            update(m.auth_sessions)
            .where(c.college_id == college_id, c.id == session_id, c.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    def revoke_user_sessions(self, college_id: CollegeId, user_id: UserId, now: datetime) -> None:
        self._bind(college_id)
        c = m.auth_sessions.c
        self._conn.execute(
            update(m.auth_sessions)
            .where(c.college_id == college_id, c.user_id == user_id, c.revoked_at.is_(None))
            .values(revoked_at=now)
        )
