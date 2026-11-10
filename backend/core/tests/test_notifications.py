"""SME notifications (P37, D227, D241): each event writes its notice once, in its own
transaction, only for the SME it concerns; the inbox is the SME's own; payloads hold codes,
dates and ids (and the return note) only."""

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from tarn_core.domain.notifications import Notification, NotificationKind
from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import InvariantError, NotFoundError, PermissionDeniedError
from tarn_core.ids import CollegeId, NotificationId, UnitId, UserId
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college
from tarn_core.testing.notifications_contract import (
    Scenario,
    build,
    check_due_key,
    check_events,
    check_inbox,
    check_isolated,
    check_sweep,
)

K = NotificationKind


@pytest.fixture
def mem() -> InMemory:
    return InMemory()


@pytest.fixture
def s(mem: InMemory) -> Scenario:
    return build(mem, add_college(mem, "A"), mem.clock)


def test_the_contract_in_memory(mem: InMemory, s: Scenario) -> None:
    other = add_college(mem, "B")
    unit = check_events(mem, mem.notifications, s)
    check_inbox(mem, mem.notifications, s)
    check_due_key(mem, mem.notifications, s, unit)
    check_isolated(mem.notifications, other.id, s)


def test_the_sweep_contract_in_memory(mem: InMemory, s: Scenario) -> None:
    check_sweep(mem, mem.notifications, s)


def test_an_event_that_fails_writes_no_notice(mem: InMemory, s: Scenario) -> None:
    unit = s.services.units.create(
        s.cid,
        s.admin,
        paper_id=s.paper,
        year="2024",
        owner_id=s.first.id,
        due_date=date(2026, 12, 1),
    )
    before = len(mem.notifications.all(s.cid))
    with pytest.raises(InvariantError):
        s.services.transfers.transfer(s.cid, s.admin, unit.id, owner_id=s.first.id, reason="Why")
    with pytest.raises(InvariantError):
        s.services.units.create(
            s.cid,
            s.admin,
            paper_id=s.paper,
            year="2025",
            owner_id=s.first.id,
            due_date=date(2020, 1, 1),
        )
    assert len(mem.notifications.all(s.cid)) == before


def test_the_inbox_is_for_smes_only(mem: InMemory, s: Scenario) -> None:
    svc = s.services.notifications
    governor = User(
        id=UserId(mem.ids.new()),
        college_id=s.cid,
        display_name="Governor",
        email="gov@example.test",
        role=Role.GOVERNOR,
    )
    mem.users.save(s.cid, governor)
    for who in (s.admin, governor.id):
        with pytest.raises(PermissionDeniedError):
            svc.unread(s.cid, who)
        with pytest.raises(PermissionDeniedError):
            svc.page(s.cid, who)
        with pytest.raises(PermissionDeniedError):
            svc.mark_all_read(s.cid, who)
    assert svc.unread(s.cid, s.first.id) == 0
    with pytest.raises(InvariantError):
        svc.page(s.cid, s.first.id, limit=51)


def test_the_service_marks_only_the_callers_notice(mem: InMemory, s: Scenario) -> None:
    check_events(mem, mem.notifications, s)
    svc = s.services.notifications
    theirs = svc.page(s.cid, s.second.id).items[0]
    with pytest.raises(NotFoundError):
        svc.mark_read(s.cid, s.first.id, theirs.id)
    mine = svc.page(s.cid, s.first.id).items[0]
    assert svc.mark_read(s.cid, s.first.id, mine.id).read
    # Another college's id: not found, like a missing one.
    with pytest.raises(NotFoundError):
        svc.mark_read(s.cid, s.first.id, NotificationId(mem.ids.new()))


def test_a_revoke_with_transfer_writes_the_same_two_notices(mem: InMemory, s: Scenario) -> None:
    unit = s.services.units.create(
        s.cid,
        s.admin,
        paper_id=s.paper,
        year="2024",
        owner_id=s.first.id,
        due_date=date(2026, 12, 1),
    )
    spare = s.services.subjects.create(s.cid, s.admin, code="SPARE", name="Spare")
    s.services.entitlements.grant(s.cid, s.admin, s.first.id, spare.id)
    s.services.transfers.revoke_subject(
        s.cid,
        s.admin,
        s.first.id,
        unit.subject_id,
        transfers={unit.id: s.second.id},
        reason="Subject moves to the second SME",
    )
    kinds = {(n.user_id, n.kind) for n in mem.notifications.all(s.cid) if n.unit_id == unit.id}
    assert kinds == {
        (s.first.id, K.ASSIGNED),
        (s.first.id, K.TRANSFERRED_AWAY),
        (s.second.id, K.ASSIGNED),
    }


def _notice(**changes: object) -> Notification:
    base = Notification(
        id=NotificationId(UUID(int=1)),
        college_id=CollegeId(UUID(int=2)),
        user_id=UserId(UUID(int=3)),
        kind=K.ASSIGNED,
        unit_id=UnitId(UUID(int=4)),
        day=date(2026, 10, 3),
        created_at=datetime(2026, 10, 3, tzinfo=UTC),
        payload={"unit_code": "U-0001"},
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def test_a_notice_carries_codes_only() -> None:
    _notice()
    with pytest.raises(InvariantError):
        _notice(payload={"unit_code": "U-0001", "student": "A name"})
    with pytest.raises(InvariantError):
        _notice(payload={"note": "free text on a notice that is not a return"})
    with pytest.raises(InvariantError):
        _notice(kind=K.RETURNED, payload={"note": "x" * 501})
    with pytest.raises(InvariantError):
        _notice(created_at=datetime(2026, 10, 3))
    _notice(kind=K.RETURNED, payload={"unit_code": "U-0001", "note": "Look again at Q3"})
    with pytest.raises(TypeError):
        _notice().payload["note"] = "changed"  # type: ignore[index]
