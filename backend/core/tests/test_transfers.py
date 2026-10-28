"""Transfers, subject removal with transfers, SME removal and the delegation inbox (P27, D163):
the unit moves whole with every booklet's state, the old SME loses access and their locks at
once, both SMEs are emailed (no reason, no student data), every refusal changes nothing."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.identity import IdentityStatus
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import DelegationRequest, RequestState, Unit, UnitTransfer
from tarn_core.errors import (
    AlreadyExistsError,
    BookletLockedError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
    StaleWriteError,
)
from tarn_core.ids import (
    BlueprintId,
    CollegeId,
    DelegationRequestId,
    SubjectId,
    UnitId,
    UnitTransferId,
    UserId,
)
from tarn_core.services.access import AccessService
from tarn_core.testing import InMemory, MemoryMailer
from tarn_core.testing.auth_world import AuthWorld
from tarn_core.testing.builders import (
    CollegeFixture,
    Services,
    add_college,
    ci_shaped_blueprint,
    make_services,
    unit_for,
)
from tarn_core.testing.workflow import GOOD, HALF, Workflow, scored_booklet, workflow

REASON = "Capacity: the first SME is on leave"


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.mail = MemoryMailer()
        self.svc: Services = make_services(self.mem, mailer=self.mail)
        self.college: CollegeFixture = add_college(self.mem, "A")
        self.other: CollegeFixture = add_college(self.mem, "B")
        self.paper = ci_shaped_blueprint(self.mem, self.college)
        self.subject = self.paper.subject_id
        self.wf: Workflow = workflow(self.mem)
        self.second = self.add_sme("Second SME")
        # The first SME keeps another subject, so the paper's subject may be taken away.
        self.spare = self.svc.subjects.create(
            self.college.id, self.college.admin.id, code="SPARE", name="Spare"
        )
        self.grant(self.college.teacher, self.spare.id)

    @property
    def cid(self) -> CollegeId:
        return self.college.id

    @property
    def admin(self) -> UserId:
        return self.college.admin.id

    @property
    def first(self) -> User:
        return self.college.teacher

    def add_sme(self, name: str, *, entitled: bool = True) -> User:
        sme = User(
            id=UserId(self.mem.ids.new()),
            college_id=self.cid,
            display_name=name,
            email=f"{name.lower().replace(' ', '.')}@example.test",
            role=Role.SME,
        )
        self.mem.users.save(self.cid, sme)
        if entitled:
            self.grant(sme, self.subject)
        return sme

    def grant(self, user: User, subject: SubjectId) -> None:
        self.svc.entitlements.grant(self.cid, self.admin, user.id, subject)

    def unit(self, year: str = "2024") -> Unit:
        return self.mem.units.get(self.cid, unit_for(self.mem, self.cid, self.paper.id, year=year))

    def move(
        self, unit: Unit, to: User, *, reason: str = REASON, expected_version: int | None = None
    ) -> Unit:
        return self.svc.transfers.transfer(
            self.cid,
            self.admin,
            unit.id,
            owner_id=to.id,
            reason=reason,
            expected_version=expected_version,
        )

    def access(self) -> AccessService:
        m = self.mem
        return AccessService(
            users=m.users,
            entitlements=m.entitlements,
            content=m.content,
            booklets=m.booklets,
            units=m.units,
        )


@pytest.fixture
def w() -> World:
    return World()


def _approve_all(w: World, booklet: Booklet, actor: UserId) -> None:
    for a in w.mem.booklets.answers(w.cid, booklet.id):
        if a.status is not AnswerStatus.APPROVED:
            w.wf.review.approve_answer(w.cid, actor, booklet.id, a.id, expected_version=a.version)


def _amended(w: World) -> tuple[Booklet, Booklet]:
    """Two booklets of the unit (both the first student's: the builder does not mind): one
    approved and then reopened (an amendment draft open, the first SME's lock), one in
    review with one answer approved (the first SME's lock)."""
    done = scored_booklet(w.mem, w.college, w.paper, {"1": GOOD, "2": HALF})
    opened = w.wf.review.open(w.cid, w.first.id, done.id)
    _approve_all(w, done, w.first.id)
    w.wf.review.approve_booklet(w.cid, w.first.id, done.id, expected_version=opened.booklet.version)
    answer = next(a for a in w.mem.booklets.answers(w.cid, done.id) if a.slot_label == "2")
    w.wf.review.reopen(
        w.cid, w.first.id, done.id, answer.id, expected_version=answer.version, reason="Recount"
    )
    busy = scored_booklet(w.mem, w.college, w.paper, {"1": GOOD, "2": HALF})
    w.wf.review.open(w.cid, w.first.id, busy.id)
    one = next(a for a in w.mem.booklets.answers(w.cid, busy.id) if a.slot_label == "1")
    w.wf.review.approve_answer(w.cid, w.first.id, busy.id, one.id, expected_version=one.version)
    return w.mem.booklets.get(w.cid, done.id), w.mem.booklets.get(w.cid, busy.id)


# --- the records ---------------------------------------------------------------------------


def test_transfer_and_request_invariants() -> None:
    now = InMemory().clock.now()
    college = CollegeId(uuid4())
    a, b = UserId(uuid4()), UserId(uuid4())
    base: dict[str, object] = {
        "id": UnitTransferId(uuid4()),
        "college_id": college,
        "unit_id": UnitId(uuid4()),
        "from_owner": a,
        "to_owner": b,
        "reason": "ok",
        "by": a,
        "at": now,
    }
    UnitTransfer(**base)  # type: ignore[arg-type]
    for bad in ({"to_owner": a}, {"reason": ""}, {"reason": " x"}, {"reason": "x" * 501}):
        with pytest.raises(InvariantError):
            UnitTransfer(**(base | bad))  # type: ignore[arg-type]
    request = DelegationRequest(
        id=DelegationRequestId(uuid4()),
        college_id=college,
        unit_id=UnitId(uuid4()),
        requested_by=a,
        reason="r",
        requested_at=now,
    )
    assert request.pending
    with pytest.raises(InvariantError):
        replace(request, state=RequestState.DONE)  # names nobody
    with pytest.raises(InvariantError):
        replace(request, state=RequestState.DECLINED, closed_by=b, closed_at=now)  # no note
    with pytest.raises(InvariantError):
        replace(request, state=RequestState.DONE, closed_by=b, closed_at=now, note="x")
    assert replace(request, state=RequestState.DECLINED, closed_by=b, closed_at=now, note="n")


# --- a transfer ----------------------------------------------------------------------------


def test_a_transfer_moves_the_whole_unit_and_cuts_the_old_sme_off(w: World) -> None:
    done, busy = _amended(w)
    unit = w.unit()
    before = {b.id: (b.status, b.version) for b in (done, busy)}
    assert done.status is BookletStatus.AMENDMENT_IN_PROGRESS
    assert busy.status is BookletStatus.IN_REVIEW
    reviewer_before = {
        a.id: [r.reviewer for r in w.mem.scores.reviews(w.cid, a.id)]
        for b in (done, busy)
        for a in w.mem.booklets.answers(w.cid, b.id)
    }

    moved = w.move(unit, w.second, expected_version=unit.version)
    assert (moved.owner_id, moved.version) == (w.second.id, unit.version + 1)
    assert w.mem.units.get(w.cid, unit.id) == moved

    # Every booklet keeps its state: completed stays completed, the draft stays open.
    for b in (done, busy):
        now = w.mem.booklets.get(w.cid, b.id)
        assert (now.status, now.version) == before[b.id]
        assert w.mem.booklets.lock(w.cid, b.id) is None  # the old SME's locks are gone
    assert any(a.open for a in w.mem.booklets.amendments(w.cid, done.id))
    for answer_id, reviewers in reviewer_before.items():  # each answer keeps its reviewer
        assert [r.reviewer for r in w.mem.scores.reviews(w.cid, answer_id)] == reviewers

    # The old SME: out of reach at once, and a write with the old lock is refused.
    access = w.access()
    old_reach = access.reach(w.cid, w.first.id)
    with pytest.raises(NotFoundError):
        access.booklet(w.cid, old_reach, busy.id)
    answer = next(a for a in w.mem.booklets.answers(w.cid, busy.id) if a.slot_label == "2")
    with pytest.raises(BookletLockedError):
        w.wf.review.approve_answer(
            w.cid, w.first.id, busy.id, answer.id, expected_version=answer.version
        )
    assert w.svc.units.list(w.cid, w.first.id) == []

    # The new owner reaches both, opens the amended booklet and closes its draft: sheet v2.
    new_reach = access.reach(w.cid, w.second.id)
    assert access.booklet(w.cid, new_reach, done.id).id == done.id
    w.wf.review.open(w.cid, w.second.id, done.id)
    draft = next(a for a in w.mem.booklets.answers(w.cid, done.id) if a.slot_label == "2")
    decision = w.wf.review.approve_answer(
        w.cid,
        w.second.id,
        done.id,
        draft.id,
        expected_version=draft.version,
        teacher_mark=Decimal(1),
    )
    assert decision.sheet is not None and decision.sheet.version == 2
    assert w.mem.scores.reviews(w.cid, draft.id)[0].reviewer == w.first.id
    assert w.mem.scores.reviews(w.cid, draft.id)[-1].reviewer == w.second.id

    # History, audit, emails.
    [history] = w.svc.transfers.history(w.cid, w.admin, unit.id)
    assert (history.from_owner, history.to_owner, history.reason) == (
        w.first.id,
        w.second.id,
        REASON,
    )
    [event] = [e for e in w.mem.audit.events if e.action is AuditAction.UNIT_TRANSFERRED]
    assert event.actor_id == w.admin
    assert event.before == {"unit_id": str(unit.id), "owner_id": str(w.first.id), "version": 1}
    assert event.after["reason"] == REASON  # type: ignore[index, call-overload]
    assert event.after["locks_released"] == 2  # type: ignore[index, call-overload]
    received, away = w.mail.sent
    assert received.to == w.second.email and unit.code in received.subject
    assert away.to == w.first.email and "no longer have access" in away.text
    for message in (received, away):
        assert "leave" not in message.text  # the reason stays out of the emails
        for student in w.college.students:
            assert student.usn not in message.text and student.name not in message.text


@pytest.mark.parametrize("who", ["outsider", "suspended", "removed", "same", "admin", "foreign"])
def test_only_another_active_entitled_sme_receives_a_unit(w: World, who: str) -> None:
    unit = w.unit()
    target = {
        "outsider": lambda: w.add_sme("No Subject", entitled=False),
        "suspended": lambda: _saved(w, replace(w.add_sme("Suspended"), active=False)),
        "removed": lambda: _saved(
            w, replace(w.add_sme("Gone"), active=False, removed_at=w.mem.clock.now())
        ),
        "same": lambda: w.first,
        "admin": lambda: w.college.admin,
        "foreign": lambda: w.other.teacher,
    }[who]()
    with pytest.raises(InvariantError):
        w.move(unit, target)
    _nothing_moved(w, unit)


def _saved(w: World, user: User) -> User:
    w.mem.users.save(w.cid, user)
    return user


def _nothing_moved(w: World, *units: Unit) -> None:
    for unit in units:
        assert w.mem.units.get(w.cid, unit.id) == unit
        assert w.mem.units.transfers(w.cid, unit.id) == []
    assert w.mail.sent == []
    assert not [e for e in w.mem.audit.events if e.action is AuditAction.UNIT_TRANSFERRED]


def test_a_transfer_needs_a_reason_a_current_version_and_the_admin(w: World) -> None:
    unit = w.unit()
    for reason in ("", "   ", "x" * 501):
        with pytest.raises(InvariantError):
            w.move(unit, w.second, reason=reason)
    with pytest.raises(StaleWriteError):
        w.move(unit, w.second, expected_version=unit.version + 1)
    with pytest.raises(PermissionDeniedError):
        w.svc.transfers.transfer(w.cid, w.first.id, unit.id, owner_id=w.second.id, reason=REASON)
    with pytest.raises(NotFoundError):  # another college's unit
        w.svc.transfers.transfer(
            w.other.id, w.other.admin.id, unit.id, owner_id=w.other.teacher.id, reason=REASON
        )
    _nothing_moved(w, unit)


# --- removing a subject ---------------------------------------------------------------------


def test_revoking_a_subject_with_open_units_is_all_or_nothing(w: World) -> None:
    first, second = w.unit("2024"), w.unit("2025")
    outsider = w.add_sme("No Subject", entitled=False)
    third = w.add_sme("Third SME")

    def revoke(transfers: dict[UnitId, UserId]) -> frozenset[SubjectId]:
        return w.svc.transfers.revoke_subject(
            w.cid, w.admin, w.first.id, w.subject, transfers=transfers, reason="Subject removed"
        )

    with pytest.raises(IllegalTransitionError):  # the plain revoke asks for transfers
        w.svc.entitlements.revoke(w.cid, w.admin, w.first.id, w.subject)
    with pytest.raises(IllegalTransitionError):  # one unit left behind
        revoke({first.id: w.second.id})
    with pytest.raises(IllegalTransitionError):  # a unit that is not one of them
        revoke(
            {first.id: w.second.id, second.id: w.second.id, UnitId(w.mem.ids.new()): w.second.id}
        )
    with pytest.raises(InvariantError):  # the second unit's target is not entitled
        revoke({first.id: w.second.id, second.id: outsider.id})
    with pytest.raises(InvariantError):  # nor may the SME losing the subject keep one
        revoke({first.id: w.second.id, second.id: w.first.id})
    _nothing_moved(w, first, second)
    assert w.subject in w.mem.entitlements.subjects_of(w.cid, w.first.id)

    left = revoke({first.id: w.second.id, second.id: third.id})
    assert left == {w.spare.id}
    assert w.mem.units.get(w.cid, first.id).owner_id == w.second.id
    assert w.mem.units.get(w.cid, second.id).owner_id == third.id
    actions = [e.action for e in w.mem.audit.events[-3:]]
    assert actions == [
        AuditAction.UNIT_TRANSFERRED,
        AuditAction.UNIT_TRANSFERRED,
        AuditAction.SUBJECT_REVOKED,
    ]
    assert len(w.mail.sent) == 4


def test_revoking_without_open_units_needs_no_transfer(w: World) -> None:
    w.unit()
    w.grant(w.second, w.spare.id)
    left = w.svc.transfers.revoke_subject(
        w.cid, w.admin, w.second.id, w.subject, transfers={}, reason=""
    )
    assert left == {w.spare.id}
    with pytest.raises(IllegalTransitionError):  # the last subject still stays
        w.svc.transfers.revoke_subject(
            w.cid, w.admin, w.second.id, w.spare.id, transfers={}, reason=""
        )


# --- removing an SME -----------------------------------------------------------------------


def test_an_sme_with_open_units_cannot_be_removed() -> None:
    mem = InMemory()
    world = AuthWorld(mem)
    college, admin = world.register("COLLEGE_R", "admin@r.example")
    sme = world.invite(college, admin, "sme@r.example")
    svc = make_services(mem)
    subject = svc.subjects.create(college, admin, code="S", name="Synthetic")
    svc.entitlements.grant(college, admin, sme, subject.id)
    unit = Unit(
        id=UnitId(mem.ids.new()),
        college_id=college,
        code="U-0001",
        paper_id=BlueprintId(mem.ids.new()),
        subject_id=subject.id,
        year="2024",
        owner_id=sme,
        due_date=date(2030, 1, 1),
        created_by=admin,
        created_at=mem.clock.now(),
    )
    mem.units.save(college, unit)
    with pytest.raises(IllegalTransitionError, match="U-0001"):
        world.accounts.remove(college, admin, sme)
    assert mem.users.get(college, sme).active
    with pytest.raises(PermissionDeniedError):
        world.accounts.remove(college, sme, sme)

    # With the unit gone elsewhere (a second SME takes it), the removal goes through.
    other = world.invite(college, admin, "other@r.example")
    svc.entitlements.grant(college, admin, other, subject.id)
    mem.units.save(college, replace(unit, owner_id=other, version=2))
    removed = world.accounts.remove(college, admin, sme)
    assert removed.removed and not removed.active
    assert mem.identity.get_identity(college, sme).status is IdentityStatus.DISABLED
    assert mem.entitlements.subjects_of(college, sme) == frozenset()
    listed = {v.user.id for v in world.accounts.list_accounts(college, admin)}
    assert sme not in listed and other in listed
    with pytest.raises(NotFoundError):  # gone from the directory: nothing more to manage
        world.accounts.set_active(college, admin, sme, active=True)
    with pytest.raises(NotFoundError):
        world.accounts.remove(college, admin, sme)
    event = mem.audit.events[-1]
    assert event.action is AuditAction.ACCOUNT_REMOVED and event.actor_id == admin
    assert event.before == {
        "user_id": str(sme),
        "active": True,
        "subject_ids": [str(subject.id)],
    }
    # The history stays: the user row (for names) and the earlier events.
    assert mem.users.get(college, sme).display_name
    assert any(e.action is AuditAction.ACCOUNT_ACTIVATED for e in mem.audit.events)


# --- the delegation inbox ------------------------------------------------------------------


def test_the_inbox_lifecycle(w: World) -> None:
    unit = w.unit()
    ask = w.svc.delegation
    made = ask.request(w.cid, w.first.id, unit.id, reason="  Too many units this week ")
    assert made.reason == "Too many units this week" and made.pending
    with pytest.raises(AlreadyExistsError):  # one pending request per unit
        ask.request(w.cid, w.first.id, unit.id, reason="Again")
    with pytest.raises(NotFoundError):  # another SME's unit looks missing
        ask.request(w.cid, w.second.id, unit.id, reason="Mine?")
    with pytest.raises(PermissionDeniedError):  # the admin moves units, never asks
        ask.request(w.cid, w.admin, unit.id, reason="x")
    with pytest.raises(InvariantError):
        ask.request(w.cid, w.first.id, w.unit("2025").id, reason=" ")

    # The admin declines with a note; the SME sees it; then asks again and withdraws.
    with pytest.raises(InvariantError):
        ask.decline(w.cid, w.admin, made.id, note="")
    with pytest.raises(PermissionDeniedError):
        ask.decline(w.cid, w.first.id, made.id, note="no")
    declined = ask.decline(w.cid, w.admin, made.id, note="Nobody else has the subject")
    assert (declined.state, declined.note, declined.closed_by) == (
        RequestState.DECLINED,
        "Nobody else has the subject",
        w.admin,
    )
    with pytest.raises(IllegalTransitionError):
        ask.decline(w.cid, w.admin, made.id, note="twice")
    again = ask.request(w.cid, w.first.id, unit.id, reason="Still too many")
    with pytest.raises(NotFoundError):  # only the SME who asked withdraws
        ask.withdraw(w.cid, w.second.id, again.id)
    assert ask.withdraw(w.cid, w.first.id, again.id).state is RequestState.WITHDRAWN

    # A third request, answered by a transfer: done, and linked from the transfer.
    third = ask.request(w.cid, w.first.id, unit.id, reason="Please move it")
    w.move(unit, w.second)
    done = w.mem.units.get_request(w.cid, third.id)
    assert (done.state, done.closed_by) == (RequestState.DONE, w.admin)
    [history] = w.mem.units.transfers(w.cid, unit.id)
    assert history.request_id == third.id

    # Lists: the admin sees every request, an SME their own; newest first.
    assert [r.id for r in ask.list(w.cid, w.admin)] == [third.id, again.id, made.id]
    assert [r.id for r in ask.list(w.cid, w.admin, state=RequestState.DONE)] == [third.id]
    assert [r.id for r in ask.list(w.cid, w.first.id)] == [third.id, again.id, made.id]
    assert ask.list(w.cid, w.second.id) == []
    assert ask.list(w.other.id, w.other.admin.id) == []
    with pytest.raises(PermissionDeniedError):  # the Governor (or a disabled user) has none
        ask.list(w.cid, _saved(w, replace(w.second, active=False)).id)
    actions = [e.action for e in w.mem.audit.events if e.action.value.startswith("delegation.")]
    assert actions == [
        AuditAction.DELEGATION_REQUESTED,
        AuditAction.DELEGATION_DECLINED,
        AuditAction.DELEGATION_REQUESTED,
        AuditAction.DELEGATION_WITHDRAWN,
        AuditAction.DELEGATION_REQUESTED,
    ]
