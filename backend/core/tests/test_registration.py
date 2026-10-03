"""P4: tenant registration, email verification and operator approval."""

from dataclasses import replace

import pytest

from tarn_core.domain.identity import IdentityStatus, TenantStatus
from tarn_core.domain.tenancy import institution_id_problem
from tarn_core.errors import AlreadyExistsError, InvariantError, PasswordPolicyError, TokenError
from tarn_core.ids import CollegeId
from tarn_core.services.auth import Refused, SignedIn
from tarn_core.services.registration import check_availability
from tarn_core.testing import InMemory
from tarn_core.testing.auth_world import ADMIN_PASSWORD, AuthWorld

ADMIN = "admin@college-a.example"


@pytest.mark.parametrize(
    "value",
    ["TARN_INST_01&*", "A", "ABC-123", "X" * 20, "*&_-", "0123456789"],
)
def test_valid_institution_ids(value: str) -> None:
    assert institution_id_problem(value) is None


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("", "required"),
        ("X" * 21, "at most 20"),
        ("TARN INST", "spaces"),
        ("tarn_inst", "upper-case"),
        ("TARN.EDU", "upper-case"),
        ("TARN#1", "upper-case"),
        ("TARN\t1", "spaces"),
        ("ÄBC", "upper-case"),
    ],
)
def test_invalid_institution_ids(value: str, fragment: str) -> None:
    problem = institution_id_problem(value)
    assert problem is not None and fragment in problem


def test_availability() -> None:
    w = AuthWorld(InMemory())
    w.register("TAKEN_ID", ADMIN)
    assert check_availability(w.mem.identity, "FREE_ID").available
    taken = check_availability(w.mem.identity, "TAKEN_ID")
    assert taken.valid and not taken.available
    bad = check_availability(w.mem.identity, "bad id")
    assert not bad.valid and not bad.available


def test_registration_creates_a_pending_college_and_admin() -> None:
    w = AuthWorld(InMemory())
    cid, admin = w.register("COLLEGE_A", ADMIN, verify=False, approve=False)
    tenant = w.tenant(cid)
    assert tenant.status is TenantStatus.PENDING_VERIFICATION
    assert tenant.approval_required
    assert w.mem.colleges.get(cid).code == "COLLEGE_A"
    assert w.mem.users.get(cid, admin).role.value == "admin"
    assert w.mem.identity.get_identity(cid, admin).status is IdentityStatus.PENDING
    # The data key is stored wrapped and opens with the tenant's KMS key.
    assert tenant.wrapped_data_key != b"pending"
    assert w.mem.keys.generated == 1
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), Refused)


def test_activation_needs_verification_and_approval() -> None:
    w = AuthWorld(InMemory())
    cid, _ = w.register("COLLEGE_A", ADMIN, verify=False, approve=False)
    token = w.mem.mailer.last_token(ADMIN)
    w.registration.approve(cid, operator="ops@tarn")
    assert w.tenant(cid).status is TenantStatus.PENDING_VERIFICATION
    assert w.auth.login(cid, ADMIN, ADMIN_PASSWORD) == Refused(reason="tenant_pending_verification")
    w.registration.verify_email(cid, token)
    assert w.tenant(cid).status is TenantStatus.ACTIVE
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)
    with pytest.raises(TokenError):
        w.registration.verify_email(cid, token)


def test_verified_but_unapproved_tenant_waits_for_the_operator() -> None:
    w = AuthWorld(InMemory())
    cid, _ = w.register("COLLEGE_A", ADMIN, approve=False)
    assert w.tenant(cid).status is TenantStatus.PENDING_APPROVAL
    assert w.auth.login(cid, ADMIN, ADMIN_PASSWORD) == Refused(reason="tenant_pending_approval")
    approved = w.registration.approve(cid, operator="ops@tarn")
    assert approved.status is TenantStatus.ACTIVE and approved.approved_by == "ops@tarn"
    assert "active" in w.mem.mailer.sent[-1].subject


def test_without_required_approval_verification_activates() -> None:
    mem = InMemory()
    mem.auth_settings = replace(mem.auth_settings, signup_requires_approval=False)
    w = AuthWorld(mem)
    cid, _ = w.register("COLLEGE_A", ADMIN, approve=False)
    assert w.tenant(cid).status is TenantStatus.ACTIVE
    assert isinstance(w.auth.login(cid, ADMIN, ADMIN_PASSWORD), SignedIn)


def test_registration_validation() -> None:
    w = AuthWorld(InMemory())
    w.register("COLLEGE_A", ADMIN)

    def attempt(
        institution_id: str = "COLLEGE_B",
        email: str = "admin@college-b.example",
        password: str = ADMIN_PASSWORD,
    ) -> None:
        w.registration.register(
            CollegeId(w.mem.ids.new()),
            institution_id=institution_id,
            admin_name="Dr. B",
            email=email,
            password=password,
        )

    with pytest.raises(AlreadyExistsError):
        attempt(institution_id="COLLEGE_A")
    with pytest.raises(InvariantError):
        attempt(institution_id="college b")
    with pytest.raises(InvariantError):
        attempt(email="not-an-email")
    with pytest.raises(PasswordPolicyError) as weak:
        attempt(password="short")
    assert "at least 12" in weak.value.reasons[0]
    with pytest.raises(PasswordPolicyError):
        attempt(password="QwertyUiop12")
    with pytest.raises(PasswordPolicyError):
        attempt(password="COLLEGE_B")
    with pytest.raises(PasswordPolicyError):
        attempt(password="admin@college-b.example")
    attempt()  # all good
    assert w.mem.identity.find_tenant("COLLEGE_B") is not None


def test_operator_name_is_required() -> None:
    w = AuthWorld(InMemory())
    cid, _ = w.register("COLLEGE_A", ADMIN, approve=False)
    with pytest.raises(InvariantError):
        w.registration.approve(cid, operator=" ")
