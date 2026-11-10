"""An SME's profile fields (P36, D225): employee ID and department on the core User, set when
the admin adds an SME and edited later with the name; audited with names and before/after
values (staff data); the identity record untouched; only the admin may edit."""

from uuid import uuid4

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.tenancy import (
    DEPARTMENT_MAX,
    EMPLOYEE_ID_MAX,
    Role,
    User,
    normalise_profile_field,
)
from tarn_core.errors import InvariantError, NotFoundError, PermissionDeniedError
from tarn_core.ids import CollegeId, UserId
from tarn_core.services.auth import SignedIn
from tarn_core.testing import InMemory
from tarn_core.testing.auth_world import TEACHER_PASSWORD, AuthWorld

ADMIN = "admin@college-a.example"
TEACHER = "teacher@college-a.example"


def _user(**fields: str | None) -> User:
    return User(
        id=UserId(uuid4()),
        college_id=CollegeId(uuid4()),
        display_name="Asha Rao",
        email="asha@example.test",
        role=Role.SME,
        **fields,  # type: ignore[arg-type]
    )


def test_the_fields_are_optional_and_default_to_none() -> None:
    user = _user()
    assert user.employee_id is None and user.department is None
    full = _user(employee_id="E-1", department="Physics")
    assert (full.employee_id, full.department) == ("E-1", "Physics")


@pytest.mark.parametrize(
    ("field", "longest"), [("employee_id", EMPLOYEE_ID_MAX), ("department", DEPARTMENT_MAX)]
)
def test_the_limits_are_one_to_forty_and_one_to_eighty(field: str, longest: int) -> None:
    assert (EMPLOYEE_ID_MAX, DEPARTMENT_MAX) == (40, 80)
    assert getattr(_user(**{field: "x" * longest}), field) == "x" * longest
    assert getattr(_user(**{field: "x"}), field) == "x"
    for bad in ("x" * (longest + 1), "", "   ", " padded", "padded ", "two  spaces"):
        with pytest.raises(InvariantError):
            _user(**{field: bad})


def test_normalising_trims_collapses_and_turns_blank_into_none() -> None:
    assert normalise_profile_field("  Computer   Science ") == "Computer Science"
    assert normalise_profile_field("   ") is None
    assert normalise_profile_field("") is None
    assert normalise_profile_field(None) is None


@pytest.fixture
def w() -> AuthWorld:
    return AuthWorld(InMemory())


def _events(w: AuthWorld, action: AuditAction) -> list[tuple[object, object]]:
    return [(e.before, e.after) for e in w.mem.audit.events if e.action is action]


def test_the_admin_sets_the_fields_when_adding_an_sme(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    user = w.accounts.invite_sme(
        cid,
        admin,
        display_name="Teacher T",
        email=TEACHER,
        employee_id="  TKS-8904 ",
        department=" ",
    )
    assert (user.employee_id, user.department) == ("TKS-8904", None)
    assert w.mem.users.get(cid, user.id).employee_id == "TKS-8904"


def test_the_admin_edits_name_and_fields_and_the_audit_holds_before_and_after(
    w: AuthWorld,
) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    handle = w.mem.identity.get_identity(cid, teacher).login_handle
    done = w.accounts.update_profile(
        cid,
        admin,
        teacher,
        display_name="  Dr. Teacher T ",
        employee_id="TKS-1",
        department="Physics",
    )
    assert (done.display_name, done.employee_id, done.department) == (
        "Dr. Teacher T",
        "TKS-1",
        "Physics",
    )
    assert w.mem.users.get(cid, teacher) == done
    ((before, after),) = _events(w, AuditAction.ACCOUNT_UPDATED)
    assert before == {
        "user_id": str(teacher),
        "display_name": "Teacher T",
        "employee_id": None,
        "department": None,
    }
    assert after == {
        "user_id": str(teacher),
        "display_name": "Dr. Teacher T",
        "employee_id": "TKS-1",
        "department": "Physics",
    }
    # The identity record, and so the way to sign in, is untouched.
    assert w.mem.identity.get_identity(cid, teacher).login_handle == handle
    assert isinstance(w.auth.login(cid, TEACHER, TEACHER_PASSWORD), SignedIn)


def test_only_the_changed_fields_are_audited_and_a_blank_clears_one(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    w.accounts.update_profile(
        cid, admin, teacher, display_name="Teacher T", employee_id="E-1", department="Physics"
    )
    cleared = w.accounts.update_profile(
        cid, admin, teacher, display_name="Teacher T", employee_id="E-1", department="  "
    )
    assert cleared.department is None and cleared.employee_id == "E-1"
    last = _events(w, AuditAction.ACCOUNT_UPDATED)[-1]
    assert last == (
        {"user_id": str(teacher), "department": "Physics"},
        {"user_id": str(teacher), "department": None},
    )


def test_saving_the_same_values_audits_nothing(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    w.accounts.update_profile(
        cid, admin, teacher, display_name="Teacher T", employee_id=None, department=None
    )
    assert _events(w, AuditAction.ACCOUNT_UPDATED) == []


def test_bad_values_are_refused_and_change_nothing(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    before = w.mem.users.get(cid, teacher)
    for name, employee_id, department in (
        ("T", "x" * 41, None),
        ("T", None, "x" * 81),
        ("   ", None, None),
    ):
        with pytest.raises(InvariantError):
            w.accounts.update_profile(
                cid,
                admin,
                teacher,
                display_name=name,
                employee_id=employee_id,
                department=department,
            )
    assert w.mem.users.get(cid, teacher) == before
    assert _events(w, AuditAction.ACCOUNT_UPDATED) == []


def test_an_sme_cannot_edit_their_own_or_anyone_elses_profile(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    other = w.invite(cid, admin, "other@college-a.example")
    for target in (teacher, other):
        with pytest.raises(PermissionDeniedError):
            w.accounts.update_profile(
                cid, teacher, target, display_name="Me", employee_id="E-9", department="X"
            )
    assert w.mem.users.get(cid, teacher).employee_id is None


def test_the_admin_edits_smes_only_and_only_their_own_college(w: AuthWorld) -> None:
    a, admin_a = w.register("COLLEGE_A", ADMIN)
    b, admin_b = w.register("COLLEGE_B", "admin@college-b.example")
    teacher_a = w.invite(a, admin_a, TEACHER)
    with pytest.raises(PermissionDeniedError):
        w.accounts.update_profile(
            a, admin_a, admin_a, display_name="X", employee_id=None, department=None
        )
    with pytest.raises(NotFoundError):
        w.accounts.update_profile(
            b, admin_b, teacher_a, display_name="X", employee_id="E-1", department=None
        )
    assert w.mem.users.get(a, teacher_a).employee_id is None


def test_a_removed_sme_cannot_be_edited(w: AuthWorld) -> None:
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    w.accounts.remove(cid, admin, teacher)
    with pytest.raises(NotFoundError):
        w.accounts.update_profile(
            cid, admin, teacher, display_name="X", employee_id=None, department=None
        )
