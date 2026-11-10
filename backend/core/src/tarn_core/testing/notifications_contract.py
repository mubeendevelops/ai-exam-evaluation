"""SME notifications (P37, D227, D241, D242) on one scenario, for every backend: the in-memory
adapters and PostgreSQL must write the same notices for the same events and sweeps.

``build`` makes a college's paper usable for units (an exam code) and a second SME entitled to
its subject. ``check_events`` drives the unit services: create, a transfer that closes the
SME's own request, a transfer without one, a decline, a return; each writes exactly one notice
for each SME it concerns, none for the admin. ``check_inbox`` pages, counts and marks them.
``check_sweep`` runs the reminder sweep over units due on several days. ``check_isolated``
asks from another college.

Test code that lives in the package so adapters can reuse it."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.notifications import Notification, NotificationKind
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import Unit, UnitState, UnitSubmission, unit_code
from tarn_core.errors import NotFoundError
from tarn_core.ids import (
    BlueprintId,
    CollegeId,
    NotificationId,
    UnitId,
    UnitSubmissionId,
    UserId,
)
from tarn_core.ports.repositories import NotificationRepository
from tarn_core.testing.builders import (
    Backend,
    CollegeFixture,
    Services,
    ci_shaped_blueprint,
    make_services,
    meta,
)
from tarn_core.testing.memory_identity import MemoryMailer
from tarn_core.testing.memory_runtime import FixedClock

K = NotificationKind
REASON = "Capacity: on leave for two weeks"


@dataclass
class Scenario:
    college: CollegeFixture
    paper: BlueprintId
    first: User
    second: User
    services: Services
    mail: MemoryMailer
    clock: FixedClock

    def tick(self) -> None:
        """Events a minute apart, so "newest first" has one answer on every backend."""
        self.clock.advance(minutes=1)

    @property
    def cid(self) -> CollegeId:
        return self.college.id

    @property
    def admin(self) -> UserId:
        return self.college.admin.id


def build(mem: Backend, college: CollegeFixture, clock: FixedClock) -> Scenario:
    cid = college.id
    paper = ci_shaped_blueprint(mem, college)
    usable = replace(paper, exam_code="QP-SYN", meta=meta(college, 2))
    mem.content.save(cid, usable)
    mail = MemoryMailer()
    services = make_services(mem, mailer=mail)
    second = User(
        id=UserId(mem.ids.new()),
        college_id=cid,
        display_name="Second SME",
        email=f"second.{str(cid)[:8]}@example.test",
        role=Role.SME,
    )
    mem.users.save(cid, second)
    services.entitlements.grant(cid, college.admin.id, second.id, paper.subject_id)
    return Scenario(
        college=college,
        paper=paper.id,
        first=college.teacher,
        second=second,
        services=services,
        mail=mail,
        clock=clock,
    )


def _kinds(repo: NotificationRepository, cid: CollegeId, user: UserId) -> list[K]:
    return [n.kind for n in reversed(repo.page(cid, user, limit=50).items)]


def check_events(mem: Backend, repo: NotificationRepository, s: Scenario) -> UnitId:
    """Returns the unit the events were about (owned by the first SME at the end)."""
    cid, svc, today = s.cid, s.services, mem.runtime.clock.now().date()
    unit = svc.units.create(
        cid,
        s.admin,
        paper_id=s.paper,
        year="2024",
        owner_id=s.first.id,
        due_date=today + timedelta(days=10),
    )
    assert _kinds(repo, cid, s.first.id) == [K.ASSIGNED]
    first_notice = repo.page(cid, s.first.id, limit=1).items[0]
    assert first_notice.unit_id == unit.id
    assert dict(first_notice.payload) == {
        "unit_code": unit.code,
        "due_date": unit.due_date.isoformat(),
    }

    # The first SME asks; the admin moves the unit to the second: done for the first (not
    # also "transferred away"), assigned for the second.
    s.tick()
    request = svc.delegation.request(cid, s.first.id, unit.id, reason=REASON)
    svc.transfers.transfer(cid, s.admin, unit.id, owner_id=s.second.id, reason=REASON)
    assert _kinds(repo, cid, s.first.id) == [K.ASSIGNED, K.REQUEST_DONE]
    assert _kinds(repo, cid, s.second.id) == [K.ASSIGNED]
    done = repo.page(cid, s.first.id, limit=1).items[0]
    assert done.payload["request_id"] == str(request.id)

    # Moved back without a request: transferred away for the second, assigned for the first.
    s.tick()
    svc.transfers.transfer(cid, s.admin, unit.id, owner_id=s.first.id, reason=REASON)
    assert _kinds(repo, cid, s.second.id) == [K.ASSIGNED, K.TRANSFERRED_AWAY]
    assert _kinds(repo, cid, s.first.id) == [K.ASSIGNED, K.REQUEST_DONE, K.ASSIGNED]

    # A declined request: one notice, no note (the SME reads it in their list).
    s.tick()
    declined = svc.delegation.request(cid, s.first.id, unit.id, reason=REASON)
    svc.delegation.decline(cid, s.admin, declined.id, note="Not now, the term ends soon")
    latest = repo.page(cid, s.first.id, limit=1).items[0]
    assert latest.kind is K.REQUEST_DECLINED
    assert "note" not in latest.payload
    # A withdrawn request writes nothing.
    withdrawn = svc.delegation.request(cid, s.first.id, unit.id, reason=REASON)
    svc.delegation.withdraw(cid, s.first.id, withdrawn.id)
    assert len(_kinds(repo, cid, s.first.id)) == 4

    # A return carries the admin's note, the one free text.
    s.tick()
    current = mem.units.get(cid, unit.id)
    submitted = current.moved_to(UnitState.SUBMITTED)
    mem.units.save(cid, submitted)
    mem.units.save_submission(
        cid,
        UnitSubmission(
            id=UnitSubmissionId(mem.ids.new()),
            college_id=cid,
            unit_id=unit.id,
            submitted_by=s.first.id,
            submitted_at=mem.runtime.clock.now(),
        ),
    )
    svc.unit_flow.return_(cid, s.admin, unit.id, note="Q3 needs a second look")
    returned = repo.page(cid, s.first.id, limit=1).items[0]
    assert returned.kind is K.RETURNED
    assert returned.payload["note"] == "Q3 needs a second look"

    assert Counter(_kinds(repo, cid, s.first.id)) == Counter(
        {K.ASSIGNED: 2, K.REQUEST_DONE: 1, K.REQUEST_DECLINED: 1, K.RETURNED: 1}
    )
    assert Counter(_kinds(repo, cid, s.second.id)) == Counter(
        {K.ASSIGNED: 1, K.TRANSFERRED_AWAY: 1}
    )
    assert repo.page(cid, s.admin, limit=50).total == 0
    return unit.id


def check_inbox(mem: Backend, repo: NotificationRepository, s: Scenario) -> None:
    """Run after ``check_events``: newest first, paged, read once, never another user's."""
    cid, clock = s.cid, mem.runtime.clock
    page = repo.page(cid, s.first.id, limit=2)
    assert page.total == 5 and page.unread == 5 and len(page.items) == 2
    rest = repo.page(cid, s.first.id, limit=10, offset=2)
    assert len(rest.items) == 3
    stamps = [n.created_at for n in (*page.items, *rest.items)]
    assert stamps == sorted(stamps, reverse=True)
    assert repo.unread(cid, s.first.id) == 5

    target = page.items[0]
    at = clock.now()
    marked = repo.mark_read(cid, s.first.id, target.id, at)
    assert marked.read_at == at
    again = repo.mark_read(cid, s.first.id, target.id, at + timedelta(hours=1))
    assert again.read_at == at  # read once
    assert repo.unread(cid, s.first.id) == 4

    # Another SME's notice looks missing, and is left unread.
    theirs = repo.page(cid, s.second.id, limit=1).items[0]
    try:
        repo.mark_read(cid, s.first.id, theirs.id, at)
    except NotFoundError:
        pass
    else:
        raise AssertionError("another user's notice was marked")
    assert repo.unread(cid, s.second.id) == 2

    assert repo.mark_all_read(cid, s.first.id, at) == 4
    assert repo.unread(cid, s.first.id) == 0
    assert repo.mark_all_read(cid, s.first.id, at) == 0
    assert repo.unread(cid, s.second.id) == 2


def check_due_key(mem: Backend, repo: NotificationRepository, s: Scenario, unit: UnitId) -> None:
    """A due notice is unique per unit, kind and day; event notices are not keyed."""
    cid, now = s.cid, mem.runtime.clock.now()

    def notice(kind: K, day: date) -> Notification:
        return Notification(
            id=NotificationId(mem.ids.new()),
            college_id=cid,
            user_id=s.first.id,
            kind=kind,
            unit_id=unit,
            day=day,
            created_at=now,
            payload={"unit_code": "U-0001"},
        )

    day = now.date()
    assert repo.add(cid, notice(K.OVERDUE, day))
    assert not repo.add(cid, notice(K.OVERDUE, day))
    assert repo.add(cid, notice(K.OVERDUE, day + timedelta(days=1)))
    assert repo.add(cid, notice(K.DUE_SOON, day))
    assert not repo.add(cid, notice(K.DUE_SOON, day))
    assert repo.add(cid, notice(K.ASSIGNED, day))
    assert repo.add(cid, notice(K.ASSIGNED, day))


def due_unit(mem: Backend, s: Scenario, owner: User, due: date, year: str) -> Unit:
    """A unit saved directly (the service refuses past due dates)."""
    paper = mem.content.get(s.cid, ExamBlueprint, s.paper)
    unit = Unit(
        id=UnitId(mem.ids.new()),
        college_id=s.cid,
        code=unit_code(mem.units.count(s.cid) + 1),
        paper_id=paper.id,
        subject_id=paper.subject_id,
        year=year,
        owner_id=owner.id,
        due_date=due,
        created_by=None,
        created_at=mem.runtime.clock.now(),
    )
    mem.units.save(s.cid, unit)
    return unit


def check_sweep(mem: Backend, repo: NotificationRepository, s: Scenario) -> None:
    """Today is the clock's UTC date."""
    cid, svc = s.cid, s.services
    today = mem.runtime.clock.now().astimezone(UTC).date()
    units = {
        days: due_unit(mem, s, s.first if days % 2 else s.second, today + timedelta(days), y)
        for days, y in ((0, "S0"), (3, "S3"), (4, "S4"), (-1, "S-1"), (-5, "S-5"))
    }
    closed = due_unit(mem, s, s.first, today - timedelta(days=2), "SC")
    mem.units.save(cid, closed.moved_to(UnitState.SUBMITTED))
    soon_closed = due_unit(mem, s, s.first, today + timedelta(days=1), "SC1")
    mem.units.save(cid, soon_closed.moved_to(UnitState.SUBMITTED))
    mails = len(s.mail.sent)

    first = svc.reminders.run(cid)
    assert (first.due_soon, first.overdue, first.emails) == (2, 2, 2)
    by_unit = {n.unit_id: n for n in _all(repo, cid, s) if n.kind in (K.DUE_SOON, K.OVERDUE)}
    assert by_unit[units[0].id].kind is K.DUE_SOON
    assert by_unit[units[0].id].user_id == s.second.id
    assert by_unit[units[3].id].kind is K.DUE_SOON
    assert by_unit[units[3].id].user_id == s.first.id
    assert by_unit[units[3].id].day == units[3].due_date
    assert by_unit[units[-1].id].kind is K.OVERDUE
    assert by_unit[units[-1].id].day == today
    assert units[4].id not in by_unit
    assert closed.id not in by_unit and soon_closed.id not in by_unit
    sent = s.mail.sent[mails:]
    assert len(sent) == 2
    assert {m.to for m in sent} == {s.first.email, s.second.email}
    assert all("due" in m.subject for m in sent)

    # The same day again: nothing new, no mail.
    again = svc.reminders.run(cid)
    assert (again.due_soon, again.overdue, again.emails) == (0, 0, 0)
    assert len(s.mail.sent) == mails + 2

    # The next day: a new overdue notice for each overdue unit (now also the one due
    # yesterday-today), the 4-day unit enters the window: due soon once, emailed once.
    s.clock.advance(days=1)
    nxt = svc.reminders.run(cid)
    assert (nxt.due_soon, nxt.overdue, nxt.emails) == (1, 3, 1)
    assert len(s.mail.sent) == mails + 3


def _all(repo: NotificationRepository, cid: CollegeId, s: Scenario) -> list[Notification]:
    found: list[Notification] = []
    for user in (s.first.id, s.second.id):
        found.extend(repo.page(cid, user, limit=50).items)
    return found


def check_isolated(repo: NotificationRepository, college_id: CollegeId, s: Scenario) -> None:
    """Asked from another college (or with another college's id under row security)."""
    assert repo.page(college_id, s.first.id, limit=50).total == 0
    assert repo.unread(college_id, s.first.id) == 0
    assert repo.mark_all_read(college_id, s.first.id, datetime.now(UTC)) == 0


__all__ = [
    "Scenario",
    "build",
    "check_due_key",
    "check_events",
    "check_inbox",
    "check_isolated",
    "check_sweep",
    "due_unit",
]
