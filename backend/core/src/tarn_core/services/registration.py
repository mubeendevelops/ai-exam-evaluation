"""Tenant registration, as in MainLogin.html "Tenant Registration" (P4).

A registration creates the college and its first admin, both pending. The tenant becomes
active once the admin's email is verified and, when the signup setting requires it, a Tarn
operator has approved it (``tarn tenants approve``)."""

from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.identity import (
    AuthPolicy,
    IdentityRecord,
    IdentityStatus,
    TenantRecord,
    TenantStatus,
    TokenPurpose,
)
from tarn_core.domain.tenancy import (
    College,
    Role,
    User,
    canonical_email,
    check_email,
    institution_id_problem,
)
from tarn_core.errors import AlreadyExistsError, InvariantError, NotFoundError
from tarn_core.ids import CollegeId, UserId
from tarn_core.ports.identity import IdentityStore
from tarn_core.ports.repositories import CollegeRepository, UserRepository
from tarn_core.services._support import Runtime
from tarn_core.services.auth import AuthKit, _Base, _link
from tarn_core.services.envelope import tenant_context


@dataclass(frozen=True, slots=True, kw_only=True)
class Availability:
    institution_id: str
    valid: bool
    available: bool
    problem: str | None = None


def check_availability(identity: IdentityStore, institution_id: str) -> Availability:
    problem = institution_id_problem(institution_id)
    if problem:
        return Availability(
            institution_id=institution_id, valid=False, available=False, problem=problem
        )
    taken = identity.find_tenant(institution_id) is not None
    return Availability(
        institution_id=institution_id,
        valid=True,
        available=not taken,
        problem="This Institution ID is already registered." if taken else None,
    )


class RegistrationService(_Base):
    def __init__(
        self,
        *,
        identity: IdentityStore,
        users: UserRepository,
        colleges: CollegeRepository,
        kit: AuthKit,
        runtime: Runtime,
    ) -> None:
        super().__init__(identity=identity, users=users, kit=kit, runtime=runtime)
        self._colleges = colleges

    def register(
        self,
        college_id: CollegeId,
        *,
        institution_id: str,
        admin_name: str,
        email: str,
        password: str,
        institution_name: str | None = None,
        policy: AuthPolicy | None = None,
    ) -> TenantRecord:
        """``college_id`` is chosen by the caller, which opens both units of work for it."""
        problem = institution_id_problem(institution_id)
        if problem:
            raise InvariantError(problem)
        if self._identity.find_tenant(institution_id) is not None:
            raise AlreadyExistsError("This Institution ID is already registered.")
        handle = email.strip()
        canonical = canonical_email(handle)
        check_email(canonical)
        policy = policy or AuthPolicy()
        now = self._now
        name = (institution_name or "").strip() or institution_id
        draft = TenantRecord(
            college_id=college_id,
            institution_id=institution_id,
            name=name,
            kms_key_ref=self._kit.settings.default_kms_key_ref,
            wrapped_data_key=b"pending",
            data_key_version=1,
            policy=policy,
            status=TenantStatus.PENDING_VERIFICATION,
            approval_required=self._kit.settings.signup_requires_approval,
            created_at=now,
            updated_at=now,
        )
        self._check_new_password(draft, canonical, password)
        data_key = self._kit.keys.generate_data_key(draft.kms_key_ref, tenant_context(draft))
        tenant = replace(draft, wrapped_data_key=data_key.wrapped)
        admin_id = self._rt.new_id(UserId)
        self._identity.save_tenant(tenant)
        self._identity.save_identity(
            college_id,
            IdentityRecord(
                user_id=admin_id,
                college_id=college_id,
                login_handle=handle,
                email_canonical=canonical,
                status=IdentityStatus.PENDING,
                password_hash=self._kit.hasher.hash(password, policy.hash_params),
                last_password_change=now,
                created_at=now,
                updated_at=now,
            ),
        )
        self._colleges.save(College(id=college_id, name=name, code=institution_id))
        self._users.save(
            college_id,
            User(
                id=admin_id,
                college_id=college_id,
                display_name=admin_name.strip(),
                email=handle,
                role=Role.ADMIN,
            ),
        )
        token = self._issue_link(
            college_id, admin_id, TokenPurpose.VERIFY_EMAIL, self._kit.settings.verify_link
        )
        approval = (
            "\n\nAfter verification, a Tarn operator reviews the registration; we'll let you "
            "know when your workspace is active."
            if tenant.approval_required
            else ""
        )
        self._send(
            handle,
            "Verify your email for Tarn AI Evaluation",
            f"You registered the Institution ID {institution_id}.\n\n"
            f"Verify your email address here (the link works once):\n"
            f"{_link(self._kit.settings, 'verify-email', token)}{approval}",
        )
        self._rt.record(
            college_id,
            admin_id,
            AuditAction.TENANT_REGISTERED,
            after={
                "institution_id": institution_id,
                "approval_required": tenant.approval_required,
            },
        )
        return tenant

    def verify_email(self, college_id: CollegeId, token: str) -> TenantRecord:
        found = self._usable_token(college_id, token, TokenPurpose.VERIFY_EMAIL)
        self._consume_token(college_id, token, TokenPurpose.VERIFY_EMAIL)
        now = self._now
        identity = self._identity.get_identity(college_id, found.user_id)
        self._identity.save_identity(
            college_id,
            replace(identity, status=IdentityStatus.ACTIVE, email_verified_at=now, updated_at=now),
        )
        tenant = self._identity.get_tenant(college_id)
        if tenant.email_verified_at is None:
            verified = replace(tenant, email_verified_at=now, updated_at=now)
            tenant = replace(verified, status=verified.next_status())
            self._identity.save_tenant(tenant)
        self._rt.record(
            college_id,
            identity.user_id,
            AuditAction.TENANT_EMAIL_VERIFIED,
            after={"status": tenant.status.value},
        )
        return tenant

    def approve(self, college_id: CollegeId, *, operator: str) -> TenantRecord:
        """A Tarn operator approves a registration (CLI). Active once the email is verified."""
        if not operator.strip():
            raise InvariantError("name the operator who approves")
        tenant = self._identity.get_tenant(college_id)
        if tenant.approved_at is not None:
            return tenant
        now = self._now
        approved = replace(tenant, approved_at=now, approved_by=operator.strip(), updated_at=now)
        approved = replace(approved, status=approved.next_status())
        self._identity.save_tenant(approved)
        self._rt.record(
            college_id,
            None,
            AuditAction.TENANT_APPROVED,
            after={"operator": operator.strip(), "status": approved.status.value},
        )
        if approved.status is TenantStatus.ACTIVE:
            admins = [u for u in self._users.list(college_id) if u.role is Role.ADMIN]
            for admin in admins:
                self._send(
                    admin.email,
                    "Your Tarn workspace is active",
                    f"The Institution ID {approved.institution_id} is approved. "
                    f"Sign in at {self._kit.settings.public_url}",
                )
        return approved

    def suspend(self, college_id: CollegeId, *, operator: str) -> TenantRecord:
        """A Tarn operator stops a college (incident, unpaid, abuse): nobody can sign in or
        refresh, and every open session ends now, so access tokens stop working at the next
        request (``current_user`` checks the session). Nothing is deleted."""
        who = self._operator(operator)
        tenant = self._identity.get_tenant(college_id)
        now = self._now
        if tenant.status is not TenantStatus.SUSPENDED:
            tenant = replace(tenant, status=TenantStatus.SUSPENDED, updated_at=now)
            self._identity.save_tenant(tenant)
        for user in self._users.list(college_id):
            self._identity.revoke_user_sessions(college_id, user.id, now)
        self._rt.record(college_id, None, AuditAction.TENANT_SUSPENDED, after={"operator": who})
        return tenant

    def resume(self, college_id: CollegeId, *, operator: str) -> TenantRecord:
        """Undo a suspension: the status verification and approval imply comes back."""
        who = self._operator(operator)
        tenant = self._identity.get_tenant(college_id)
        if tenant.status is not TenantStatus.SUSPENDED:
            return tenant
        unsuspended = replace(tenant, status=TenantStatus.PENDING_VERIFICATION)
        resumed = replace(unsuspended, status=unsuspended.next_status(), updated_at=self._now)
        self._identity.save_tenant(resumed)
        self._rt.record(
            college_id,
            None,
            AuditAction.TENANT_RESUMED,
            after={"operator": who, "status": resumed.status.value},
        )
        return resumed

    @staticmethod
    def _operator(operator: str) -> str:
        if not operator.strip():
            raise InvariantError("name the operator")
        return operator.strip()


def tenant_by_institution(identity: IdentityStore, institution_id: str) -> TenantRecord:
    tenant = identity.find_tenant(institution_id)
    if tenant is None:
        raise NotFoundError(f"institution {institution_id}")
    return tenant
