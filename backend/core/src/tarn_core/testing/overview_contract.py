"""The overview figures (P29) on one scenario, for every ``OverviewRepository``: the in-memory
one and PostgreSQL's SQL aggregates must give the same answers.

``build`` makes a college with three live SMEs (one active with two open units, one with only
closed units, one suspended), a removed SME, four units (one overdue, one submitted, one
frozen) and twelve booklets in every stage; ``check_before`` and ``check_after_transfer`` hold
the figures by hand. Test code that lives in the package so adapters can reuse it."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.overview import SmeCounts, SmeProgress, SmeStatus, StageCounts, UnitCounts
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import UnitState
from tarn_core.ids import BookletId, UnitId, UserId
from tarn_core.ports.repositories import OverviewRepository
from tarn_core.testing.builders import (
    Backend,
    CollegeFixture,
    Services,
    ci_shaped_blueprint,
    unit_for,
)
from tarn_core.testing.memory_runtime import FixedClock

_S = BookletStatus
_UNIT_SPEC = (
    # (key, year, owner, state, due in days, booklet statuses)
    (
        "u1",
        "2021",
        "first",
        UnitState.OPEN,
        -1,
        (_S.UPLOADED, _S.SCORED, _S.IN_REVIEW, _S.APPROVED, _S.NEEDS_RETAKE),
    ),
    (
        "u2",
        "2022",
        "first",
        UnitState.OPEN,
        10,
        (_S.APPROVED_AMENDED, _S.AMENDMENT_IN_PROGRESS, _S.FAILED, _S.SEGMENTED),
    ),
    ("u3", "2023", "second", UnitState.SUBMITTED, -5, (_S.APPROVED, _S.APPROVED)),
    ("u5", "2025", "second", UnitState.FROZEN, -30, (_S.APPROVED,)),
)


@dataclass
class Scenario:
    college: CollegeFixture
    first: User
    second: User
    third: User
    units: dict[str, UnitId]
    opened_at: datetime
    approved_at: datetime


def build(mem: Backend, svc: Services, college: CollegeFixture, clock: FixedClock) -> Scenario:
    cid = college.id
    admin = college.admin.id
    paper = ci_shaped_blueprint(mem, college)

    def sme(name: str, *, active: bool = True, removed: bool = False) -> User:
        user = User(
            id=UserId(mem.ids.new()),
            college_id=cid,
            display_name=name,
            email=f"{name.lower().replace(' ', '.')}@example.test",
            role=Role.SME,
            active=active and not removed,
            removed_at=mem.runtime.clock.now() if removed else None,
        )
        mem.users.save(cid, user)
        return user

    second = sme("Second SME")
    third = sme("Third SME", active=False)
    sme("Removed SME", removed=True)
    owners = {"first": college.teacher, "second": second}
    # Every live SME may own units of the paper's subject (a transfer needs it).
    svc.entitlements.grant(cid, admin, second.id, paper.subject_id)

    today = mem.runtime.clock.now().date()
    units: dict[str, UnitId] = {}
    number = 0
    for key, year, owner, state, due_in, statuses in _UNIT_SPEC:
        unit_id = unit_for(mem, cid, paper.id, owner_id=owners[owner].id, year=year)
        units[key] = unit_id
        for status in statuses:
            number += 1
            booklet = svc.booklets.register(
                cid,
                college.teacher.id,
                student_id=college.students[number % 3].id,
                unit_id=unit_id,
                file_sha256=f"{number:064x}",
            ).booklet
            if status is not _S.UPLOADED:
                mem.booklets.save(
                    cid,
                    replace(
                        booklet,
                        status=status,
                        version=booklet.version + 1,
                        failure_reason="unreadable_file" if status is _S.FAILED else None,
                    ),
                )
        unit = mem.units.get(cid, unit_id)
        mem.units.save(
            cid,
            replace(
                unit,
                state=state,
                due_date=today + timedelta(days=due_in),
                version=unit.version + 1,
            ),
        )

    # What the SMEs did, after the registrations above (which the uploader, the first SME,
    # is credited with in the log): the first SME's last work is the opening; signing in later
    # is not work; the second SME approved something last.
    some_booklet = BookletId(UUID(int=1))
    clock.advance(hours=1)
    opened_at = clock.now()
    mem.runtime.record(cid, college.teacher.id, AuditAction.BOOKLET_OPENED, booklet_id=some_booklet)
    clock.advance(hours=1)
    mem.runtime.record(cid, college.teacher.id, AuditAction.LOGIN)
    clock.advance(hours=1)
    approved_at = clock.now()
    mem.runtime.record(cid, second.id, AuditAction.ANSWER_APPROVED, booklet_id=some_booklet)
    scenario = Scenario(
        college=college,
        first=college.teacher,
        second=second,
        third=third,
        units=units,
        opened_at=opened_at,
        approved_at=approved_at,
    )
    return scenario


def _row(rows: list[SmeProgress] | tuple[SmeProgress, ...], user: User) -> SmeProgress:
    return next(r for r in rows if r.user_id == user.id)


def check_before(overview: OverviewRepository, s: Scenario, mem: Backend) -> None:
    cid = s.college.id
    today = mem.runtime.clock.now().date()
    assert overview.sme_counts(cid) == SmeCounts(active=1, idle=1, suspended=1)
    assert overview.unit_counts(cid, today) == UnitCounts(open=2, submitted=1, frozen=1, overdue=1)
    assert overview.booklet_stages(cid) == StageCounts(
        processing=2, ready=1, in_review=1, approved=6, attention=2
    )
    assert overview.owners_of_open_units(cid) == {s.first.id}

    rows = list(overview.sme_progress(cid, today))
    assert [r.display_name for r in rows] == sorted(r.display_name for r in rows)
    assert len(rows) == 3  # the admin and the removed SME are not listed
    one, two, three = _row(rows, s.first), _row(rows, s.second), _row(rows, s.third)
    assert (one.status, one.units_owned, one.open_units, one.overdue_units) == (
        SmeStatus.ACTIVE,
        2,
        2,
        1,
    )
    assert (one.booklets, one.approved, one.pending) == (9, 3, 6)
    assert (two.status, two.units_owned, two.open_units, two.overdue_units) == (
        SmeStatus.IDLE,
        2,
        0,
        0,
    )
    assert (two.booklets, two.approved, two.pending) == (3, 3, 0)
    assert (three.status, three.units_owned, three.booklets) == (SmeStatus.SUSPENDED, 0, 0)
    assert one.last_activity == s.opened_at  # not the later sign-in
    assert two.last_activity == s.approved_at
    assert three.last_activity is None

    by_code = {u.code: u for u in overview.unit_progress(cid, today)}
    assert len(by_code) == 4
    first = by_code[mem.units.get(cid, s.units["u1"]).code]
    assert (first.state, first.overdue, first.owner_id) == (UnitState.OPEN, True, s.first.id)
    assert first.stages == StageCounts(processing=1, ready=1, in_review=1, approved=1, attention=1)
    submitted = by_code[mem.units.get(cid, s.units["u3"]).code]
    assert (submitted.state, submitted.overdue) == (
        UnitState.SUBMITTED,
        False,
    )  # handed in: not late
    assert submitted.stages.approved == 2
    newest_first = [u.code for u in overview.unit_progress(cid, today)]
    assert newest_first == sorted(newest_first, reverse=True)


def check_after_transfer(overview: OverviewRepository, s: Scenario, mem: Backend) -> None:
    """The first unit (overdue, five booklets, one approved) moved to the second SME: credit
    follows the current owner (D163), and the second SME is no longer idle."""
    cid = s.college.id
    today = mem.runtime.clock.now().date()
    assert overview.sme_counts(cid) == SmeCounts(active=2, idle=0, suspended=1)
    assert overview.unit_counts(cid, today) == UnitCounts(open=2, submitted=1, frozen=1, overdue=1)
    rows = list(overview.sme_progress(cid, today))
    one, two = _row(rows, s.first), _row(rows, s.second)
    assert (one.units_owned, one.open_units, one.overdue_units) == (1, 1, 0)
    assert (one.booklets, one.approved, one.pending) == (4, 2, 2)
    assert (two.status, two.units_owned, two.open_units, two.overdue_units) == (
        SmeStatus.ACTIVE,
        3,
        1,
        1,
    )
    assert (two.booklets, two.approved, two.pending) == (8, 4, 4)
    assert overview.owners_of_open_units(cid) == {s.first.id, s.second.id}
    moved = next(u for u in overview.unit_progress(cid, today) if u.unit_id == s.units["u1"])
    assert moved.owner_id == s.second.id
    assert overview.booklet_stages(cid) == StageCounts(  # a transfer moves no booklet
        processing=2, ready=1, in_review=1, approved=6, attention=2
    )


def check_activity(overview: OverviewRepository, s: Scenario) -> None:
    """The feed lists only unit, people and subject events, newest first, with the unit's
    code and the actor's name, and has no sign-ins and no pipeline steps."""
    cid = s.college.id
    items = overview.activity(cid, 50)
    actions = [i.action for i in items]
    assert AuditAction.LOGIN not in actions
    assert AuditAction.BOOKLET_OPENED not in actions
    assert AuditAction.BOOKLET_REGISTERED not in actions
    assert [i.at for i in items] == sorted((i.at for i in items), reverse=True)
    assert all(a in {AuditAction.UNIT_TRANSFERRED, AuditAction.SUBJECT_GRANTED} for a in actions)
    transfer = next(i for i in items if i.action is AuditAction.UNIT_TRANSFERRED)
    assert transfer.actor_name == s.college.admin.display_name
    assert transfer.unit_code is not None
    assert len(overview.activity(cid, 1)) == 1
