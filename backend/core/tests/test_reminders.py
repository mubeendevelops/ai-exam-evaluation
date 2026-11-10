"""The due-date sweep (P37, D242): today is the clock's UTC date whatever zone the clock speaks,
open units only, one notice per unit, kind and day, the email once and without student data,
one college at a time."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tarn_core.domain.notifications import NotificationKind
from tarn_core.domain.unit import UnitState
from tarn_core.testing import FixedClock, InMemory
from tarn_core.testing.builders import add_college
from tarn_core.testing.notifications_contract import Scenario, build, due_unit

IST = timezone(timedelta(hours=5, minutes=30))
K = NotificationKind


def world(start: datetime) -> tuple[InMemory, Scenario]:
    mem = InMemory(clock=FixedClock(start))
    return mem, build(mem, add_college(mem, "A"), mem.clock)


def kinds(mem: InMemory, s: Scenario) -> dict[str, K]:
    found: dict[str, K] = {}
    for n in mem.notifications.all(s.cid):
        if n.kind in (K.DUE_SOON, K.OVERDUE):
            found[mem.units.get(s.cid, n.unit_id).year] = n.kind
    return found


@pytest.mark.parametrize(
    "now",
    [
        # 22:30 UTC on 3 October is already 04:00 on the 4th in India: still the 3rd.
        datetime(2026, 10, 4, 4, 0, tzinfo=IST),
        datetime(2026, 10, 3, 22, 30, tzinfo=UTC),
        datetime(2026, 10, 3, 0, 0, tzinfo=UTC),
    ],
)
def test_today_is_the_utc_date(now: datetime) -> None:
    mem, s = world(now)
    today = datetime(2026, 10, 3).date()
    due_unit(mem, s, s.first, today, "today")
    due_unit(mem, s, s.first, today - timedelta(days=1), "yesterday")
    due_unit(mem, s, s.first, today + timedelta(days=3), "in3")
    due_unit(mem, s, s.first, today + timedelta(days=4), "in4")
    s.services.reminders.run(s.cid)
    assert kinds(mem, s) == {"today": K.DUE_SOON, "yesterday": K.OVERDUE, "in3": K.DUE_SOON}


def test_the_email_is_sent_once_and_names_no_student() -> None:
    mem, s = world(datetime(2026, 10, 3, 9, 0, tzinfo=UTC))
    unit = due_unit(mem, s, s.first, datetime(2026, 10, 6).date(), "2024")
    for _ in range(3):
        s.services.reminders.run(s.cid)
    sent = [m for m in s.mail.sent if m.to == s.first.email]
    assert len(sent) == 1
    assert sent[0].subject == f"Tarn: unit {unit.code} is due in 3 days"
    assert "2026-10-06" in sent[0].text and "QP-SYN" in sent[0].text
    for student in mem.students.list(s.cid):
        assert student.name not in sent[0].text and student.usn not in sent[0].text


def test_closed_units_and_other_colleges_are_left_alone() -> None:
    mem, s = world(datetime(2026, 10, 3, 9, 0, tzinfo=UTC))
    other = build(mem, add_college(mem, "B"), mem.clock)
    today = datetime(2026, 10, 3).date()
    theirs = due_unit(mem, other, other.first, today, "B")
    for state in (UnitState.SUBMITTED, UnitState.FROZEN):
        unit = due_unit(mem, s, s.first, today - timedelta(days=1), state.value)
        unit = unit.moved_to(UnitState.SUBMITTED)
        mem.units.save(s.cid, unit)
        if state is UnitState.FROZEN:
            mem.units.save(s.cid, unit.moved_to(UnitState.FROZEN))
    result = s.services.reminders.run(s.cid)
    assert (result.due_soon, result.overdue) == (0, 0)
    assert mem.notifications.all(other.cid) == []
    other.services.reminders.run(other.cid)
    assert [n.unit_id for n in mem.notifications.all(other.cid)] == [theirs.id]


def test_a_disabled_owner_gets_the_notice_but_no_email() -> None:
    mem, s = world(datetime(2026, 10, 3, 9, 0, tzinfo=UTC))
    due_unit(mem, s, s.second, datetime(2026, 10, 4).date(), "2024")
    mem.users.save(s.cid, replace(s.second, active=False))
    result = s.services.reminders.run(s.cid)
    assert (result.due_soon, result.emails) == (1, 0)
    assert not [m for m in s.mail.sent if m.to == s.second.email]
