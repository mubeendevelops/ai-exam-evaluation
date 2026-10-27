"""P24 in the core: the capability table, one admin and one governor per college, the
Governor invited by a Tarn operator, subjects kept by the admin (rename, retire), SME subject
grants (audited, the last one kept), and content limited to the SME's subjects (D159-D161,
D176-D181). In-memory adapters; synthetic data."""

from dataclasses import replace

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.personas import CAPABILITIES, Capability, can
from tarn_core.domain.tenancy import Role
from tarn_core.errors import (
    AlreadyExistsError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
)
from tarn_core.ids import UserId
from tarn_core.ports.repositories import QuestionQuery
from tarn_core.services.access import WHOLE_COLLEGE, Reach, reach_of
from tarn_core.testing import InMemory
from tarn_core.testing.auth_world import TEACHER_PASSWORD, AuthWorld
from tarn_core.testing.builders import add_college, make_services, make_subject


@pytest.fixture
def mem() -> InMemory:
    return InMemory()


def test_the_capability_table() -> None:
    assert set(CAPABILITIES) == set(Role)
    admin, sme = CAPABILITIES[Role.ADMIN], CAPABILITIES[Role.SME]
    assert Capability.EVALUATE not in admin and Capability.AUTHOR_CONTENT not in admin  # D167
    assert {Capability.MANAGE_SMES, Capability.MANAGE_SUBJECTS} <= admin
    assert Capability.UPLOAD_BOOKLETS in admin and Capability.UPLOAD_BOOKLETS not in sme  # D162
    assert CAPABILITIES[Role.GOVERNOR] == frozenset()
    assert {c for caps in CAPABILITIES.values() for c in caps} == set(Capability)


def test_a_disabled_user_can_do_nothing(mem: InMemory) -> None:
    college = add_college(mem, "A")
    assert can(college.teacher, Capability.EVALUATE)
    assert not can(replace(college.teacher, active=False), Capability.EVALUATE)


def test_one_admin_and_one_governor_per_college(mem: InMemory) -> None:
    college = add_college(mem, "A")
    second = replace(college.admin, id=UserId(mem.ids.new()), email="second@a.example")
    with pytest.raises(InvariantError):
        mem.users.save(college.id, second)
    other = add_college(mem, "B")  # another college has its own admin
    assert other.admin.role is Role.ADMIN


def test_the_operator_invites_one_governor(mem: InMemory) -> None:
    world = AuthWorld(mem)
    college, _ = world.register("COLLEGE_G", "admin@g.example")
    governor = world.accounts.invite_governor(
        college, display_name="The Governor", email="gov@g.example", operator="ops@tarn"
    )
    assert governor.role is Role.GOVERNOR and governor.active
    event = mem.audit.events[-1]
    assert event.action is AuditAction.GOVERNOR_INVITED and event.actor_id is None
    assert event.after == {
        "user_id": str(governor.id),
        "role": "governor",
        "operator": "ops@tarn",
    }
    world.auth.accept_invite(college, mem.mailer.last_token("gov@g.example"), TEACHER_PASSWORD)
    with pytest.raises(AlreadyExistsError):
        world.accounts.invite_governor(
            college, display_name="Another", email="gov2@g.example", operator="ops@tarn"
        )
    with pytest.raises(PermissionDeniedError):
        world.accounts.invite_governor(college, display_name="X", email="x@g.example", operator=" ")
    # The admin manages SMEs only: not the Governor.
    admin = next(u for u in mem.users.list(college) if u.role is Role.ADMIN)
    with pytest.raises(PermissionDeniedError):
        world.accounts.set_active(college, admin.id, governor.id, active=False)


def test_smes_and_the_governor_cannot_manage_accounts(mem: InMemory) -> None:
    world = AuthWorld(mem)
    college, admin = world.register("COLLEGE_S", "admin@s.example")
    sme = world.invite(college, admin, "sme@s.example")
    with pytest.raises(PermissionDeniedError):
        world.accounts.invite_sme(college, sme, display_name="X", email="x@s.example")
    with pytest.raises(PermissionDeniedError):
        world.accounts.list_accounts(college, sme)


# --- subjects and grants ------------------------------------------------------------------------


def test_the_admin_keeps_the_subject_list(mem: InMemory) -> None:
    college, svc = add_college(mem, "A"), make_services(mem)
    with pytest.raises(PermissionDeniedError):
        svc.subjects.create(college.id, college.teacher.id, code="PHY", name="Physics")
    physics = svc.subjects.create(college.id, college.admin.id, code="PHY", name="Physics")
    with pytest.raises(AlreadyExistsError):
        svc.subjects.create(college.id, college.admin.id, code="phy", name="Again")
    renamed = svc.subjects.rename(
        college.id, college.admin.id, physics.id, code="PHY-2", name="Physics II"
    )
    assert renamed.meta.version == 2 and renamed.name == "Physics II"
    retired = svc.subjects.retire(college.id, college.admin.id, physics.id)
    assert retired.retired and retired.meta.version == 3
    assert mem.audit.events[-1].action is AuditAction.CONTENT_RETIRED
    assert svc.subjects.retire(college.id, college.admin.id, physics.id) == retired  # once
    assert svc.subjects.list(college.id, reach=WHOLE_COLLEGE) == []
    assert [s.code for s in svc.subjects.all(college.id)] == ["PHY-2"]
    with pytest.raises(InvariantError, match="retired"):
        svc.entitlements.grant(college.id, college.admin.id, college.teacher.id, physics.id)


def test_grants_are_audited_and_the_last_subject_stays(mem: InMemory) -> None:
    college, svc = add_college(mem, "A"), make_services(mem)
    physics = make_subject(svc, college, code="PHY", name="Physics")
    maths = make_subject(svc, college, code="MAT", name="Maths")
    actions = [e.action for e in mem.audit.events]
    assert actions.count(AuditAction.SUBJECT_GRANTED) == 2
    held = svc.entitlements.revoke(college.id, college.admin.id, college.teacher.id, maths.id)
    assert held == {physics.id}
    revoked = mem.audit.events[-1]
    assert revoked.action is AuditAction.SUBJECT_REVOKED and revoked.actor_id == college.admin.id
    assert revoked.before == {"user_id": str(college.teacher.id), "subject_id": str(maths.id)}
    with pytest.raises(IllegalTransitionError):
        svc.entitlements.revoke(college.id, college.admin.id, college.teacher.id, physics.id)
    with pytest.raises(NotFoundError):
        svc.entitlements.revoke(college.id, college.admin.id, college.teacher.id, maths.id)
    with pytest.raises(PermissionDeniedError):  # SMEs do not grant
        svc.entitlements.grant(college.id, college.teacher.id, college.teacher.id, maths.id)
    with pytest.raises(InvariantError):  # only an SME has subjects
        svc.entitlements.grant(college.id, college.admin.id, college.admin.id, maths.id)
    again = svc.entitlements.grant(college.id, college.admin.id, college.teacher.id, physics.id)
    assert again == {physics.id}  # granting a held subject changes nothing
    assert [e.action for e in mem.audit.events].count(AuditAction.SUBJECT_GRANTED) == 2


def test_reach_by_persona(mem: InMemory) -> None:
    college, svc = add_college(mem, "A"), make_services(mem)
    physics = make_subject(svc, college, code="PHY", name="Physics")
    assert reach_of(college.teacher, mem.entitlements) == Reach(frozenset({physics.id}))
    assert reach_of(college.admin, mem.entitlements) == WHOLE_COLLEGE
    governor = replace(college.admin, id=UserId(mem.ids.new()), role=Role.GOVERNOR)
    assert reach_of(governor, mem.entitlements) == Reach(frozenset())


def test_an_sme_works_only_in_their_subjects(mem: InMemory) -> None:
    from decimal import Decimal

    college, svc = add_college(mem, "A"), make_services(mem)
    physics = make_subject(svc, college, code="PHY", name="Physics")
    maths = svc.subjects.create(college.id, college.admin.id, code="MAT", name="Maths")  # not his
    question = svc.bank.create_question(
        college.id,
        college.teacher.id,
        subject_id=physics.id,
        code="PHY-1",
        text="What is a field?",
        max_marks=Decimal(2),
    )
    with pytest.raises(NotFoundError):
        svc.bank.create_question(
            college.id,
            college.teacher.id,
            subject_id=maths.id,
            code="MAT-1",
            text="Integrate.",
            max_marks=Decimal(2),
        )
    maths_only = Reach(frozenset({maths.id}))
    with pytest.raises(NotFoundError):
        svc.bank.detail(college.id, question.id, reach=maths_only)
    assert svc.bank.search(college.id, QuestionQuery(), reach=maths_only, limit=5).total == 0
    assert svc.bank.search(college.id, QuestionQuery(), reach=WHOLE_COLLEGE, limit=5).total == 1
    # Losing the subject (the admin granted Maths, then took Physics away) closes the question.
    svc.entitlements.grant(college.id, college.admin.id, college.teacher.id, maths.id)
    svc.entitlements.revoke(college.id, college.admin.id, college.teacher.id, physics.id)
    with pytest.raises(NotFoundError):
        svc.bank.add_reference_answer(college.id, college.teacher.id, question.id, text="Mine.")
    # The admin authors nothing (D167).
    with pytest.raises(PermissionDeniedError):
        svc.bank.add_reference_answer(college.id, college.admin.id, question.id, text="Mine.")
