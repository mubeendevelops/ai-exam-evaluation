"""The SME's own reports (P36, D226, D239) on one scenario, for every ``OverviewRepository``: the
in-memory one and PostgreSQL's SQL aggregates must give the same answers.

Every instant hangs off the calendar quarters ending with the clock's date (q1 oldest, q4 the
current one), so the boundaries are tested on the edge: a submission exactly at a quarter's
start is in it, one second before is in the quarter before.

The first SME owns, now:

- u1  created q2+1d, submitted q3+5d12h, due q3+10d (on time); booklets approved at q3+4d and
      q3+5d, a third (amended) at q3+4d12h with a second sheet at q4+2d: all three count in q3.
- u2  created q3, submitted exactly at q4's start, due q3+20d (late), frozen; booklets approved
      one second before q4 (q3) and exactly at q4's start (q4).
- u3  open: one approved at q4+1d, one scored, one in review, one uploaded: 3 pending.
- u4  submitted q2+2d, returned, submitted again q4+3d12h, due that day (on time, the date
      counts); counted once, in q4.
- u5  created for the second SME, approved booklet at q2+10d by them, transferred to the first
      at q3+1d, submitted q3+3d, due q3+10d: turnaround from the transfer, credit to the first.
- u6  submitted before the window (q1 start minus 10 days), late; counts in the lifetime only.
- u8  submitted q2+40d, due q2+50d; a booklet approved at q2+39d.

The second SME owns u7 (submitted q4+5d, due q4+9d) and nothing else. Test code that lives in
the package so adapters can reuse it."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from tarn_core.domain.assignments import AcademicYear, academic_year_of
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.reports import (
    Quarter,
    QuarterCounts,
    ReportCounts,
    TurnaroundCounts,
    last_quarters,
)
from tarn_core.domain.review import ResultSheet
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import UnitState, UnitSubmission, UnitTransfer
from tarn_core.ids import (
    BookletId,
    ResultSheetId,
    UnitId,
    UnitSubmissionId,
    UnitTransferId,
    UserId,
)
from tarn_core.ports.repositories import OverviewRepository
from tarn_core.testing.builders import (
    Backend,
    CollegeFixture,
    Services,
    ci_shaped_blueprint,
    unit_for,
)

_S = BookletStatus
DAY = timedelta(days=1)


@dataclass
class Scenario:
    college: CollegeFixture
    first: User
    second: User
    units: dict[str, UnitId]
    year: AcademicYear
    quarters: tuple[Quarter, ...]
    turnarounds: dict[str, timedelta]
    """Receipt to latest submission, by unit key, as the scenario made them."""


def build(mem: Backend, svc: Services, college: CollegeFixture) -> Scenario:
    cid = college.id
    admin = college.admin.id
    paper = ci_shaped_blueprint(mem, college)
    today = mem.runtime.clock.now().date()
    quarters = last_quarters(today)
    q1, q2, q3, q4 = (q.start_at for q in quarters)
    year = academic_year_of(today)

    second = User(
        id=UserId(mem.ids.new()),
        college_id=cid,
        display_name="Second SME",
        email="second.sme@example.test",
        role=Role.SME,
    )
    mem.users.save(cid, second)
    svc.entitlements.grant(cid, admin, second.id, paper.subject_id)
    first = college.teacher
    owners = {"u1": first, "u2": first, "u3": first, "u4": first, "u5": second, "u6": first}
    owners |= {"u7": second, "u8": first}

    number = 0

    def booklet(unit_key: str, status: BookletStatus, *sheets: datetime) -> None:
        """A booklet (registered while its unit is open) in ``status`` with its sheets issued
        at the given instants."""
        nonlocal number
        number += 1
        made = svc.booklets.register(
            cid,
            first.id,
            student_id=college.students[number % 3].id,
            unit_id=units[unit_key],
            file_sha256=f"{number:064x}",
        ).booklet
        mem.booklets.save(cid, replace(made, status=status, version=made.version + 1))
        for version, at in enumerate(sheets, start=1):
            mem.sheets.save(
                cid,
                ResultSheet(
                    id=ResultSheetId(mem.ids.new()),
                    college_id=cid,
                    booklet_id=BookletId(made.id),
                    version=version,
                    lines=(),
                    total=Decimal(0),
                    max_marks=Decimal(10),
                    issued_by=first.id,
                    issued_at=at,
                ),
            )

    units = {
        key: unit_for(mem, cid, paper.id, owner_id=owner.id, year=f"20{i}")
        for i, (key, owner) in enumerate(owners.items(), start=10)
    }
    booklet("u1", _S.APPROVED, q3 + 4 * DAY)
    booklet("u1", _S.APPROVED, q3 + 5 * DAY)
    booklet("u1", _S.APPROVED_AMENDED, q3 + 4 * DAY + timedelta(hours=12), q4 + 2 * DAY)
    booklet("u2", _S.APPROVED, q4 - timedelta(seconds=1))
    booklet("u2", _S.APPROVED, q4)
    booklet("u3", _S.APPROVED, q4 + DAY)
    booklet("u3", _S.SCORED)
    booklet("u3", _S.IN_REVIEW)
    booklet("u3", _S.UPLOADED)
    booklet("u5", _S.APPROVED, q2 + 10 * DAY)
    booklet("u6", _S.APPROVED, q1 - 10 * DAY)
    booklet("u7", _S.APPROVED, q4 + 4 * DAY)
    booklet("u8", _S.APPROVED, q2 + 39 * DAY)

    created = {
        "u1": q2 + DAY,
        "u2": q3,
        "u4": q2,
        "u5": q2 + 5 * DAY,
        "u6": q1 - 40 * DAY,
        "u7": q4 + DAY,
        "u8": q2 + 5 * DAY,
    }
    due = {
        "u1": q3 + 10 * DAY,
        "u2": q3 + 20 * DAY,
        "u4": q4 + 3 * DAY,
        "u5": q3 + 10 * DAY,
        "u6": q1 - 15 * DAY,
        "u7": q4 + 9 * DAY,
        "u8": q2 + 50 * DAY,
    }
    for key in owners:
        unit = mem.units.get(cid, units[key])
        mem.units.save(
            cid,
            replace(
                unit,
                created_at=created.get(key, unit.created_at),
                due_date=(due[key] if key in due else q4 + 40 * DAY).date(),
                version=unit.version + 1,
            ),
        )

    def transfer(key: str, to: User, at: datetime) -> None:
        unit = mem.units.get(cid, units[key])
        mem.units.save_transfer(
            cid,
            UnitTransfer(
                id=UnitTransferId(mem.ids.new()),
                college_id=cid,
                unit_id=unit.id,
                from_owner=unit.owner_id,
                to_owner=to.id,
                reason="Capacity",
                by=admin,
                at=at,
            ),
        )
        mem.units.save(cid, replace(unit, owner_id=to.id, version=unit.version + 1))

    def submit(key: str, at: datetime, state: UnitState = UnitState.SUBMITTED) -> UnitSubmission:
        unit = mem.units.get(cid, units[key])
        submission = UnitSubmission(
            id=UnitSubmissionId(mem.ids.new()),
            college_id=cid,
            unit_id=unit.id,
            submitted_by=unit.owner_id,
            submitted_at=at,
        )
        mem.units.save_submission(cid, submission)
        mem.units.save(cid, replace(unit, state=state, version=unit.version + 1))
        return submission

    def give_back(key: str, submission: UnitSubmission, at: datetime) -> None:
        mem.units.save_submission(
            cid, replace(submission, returned_by=admin, returned_at=at, return_note="Recheck")
        )
        unit = mem.units.get(cid, units[key])
        mem.units.save(cid, replace(unit, state=UnitState.OPEN, version=unit.version + 1))

    half = timedelta(hours=12)
    submitted = {
        "u1": q3 + 5 * DAY + half,
        "u2": q4,
        "u4": q4 + 3 * DAY + half,
        "u5": q3 + 3 * DAY,
        "u6": q1 - 10 * DAY,
        "u7": q4 + 5 * DAY,
        "u8": q2 + 40 * DAY,
    }
    submit("u1", submitted["u1"])
    submit("u2", submitted["u2"], UnitState.FROZEN)
    give_back("u4", submit("u4", q2 + 2 * DAY), q2 + 3 * DAY)
    submit("u4", submitted["u4"])
    transfer("u5", first, q3 + DAY)
    submit("u5", submitted["u5"])
    submit("u6", submitted["u6"])
    submit("u7", submitted["u7"])
    submit("u8", submitted["u8"])
    received = created | {"u5": q3 + DAY}
    return Scenario(
        college=college,
        first=first,
        second=second,
        units=units,
        year=year,
        quarters=quarters,
        turnarounds={k: submitted[k] - received[k] for k in submitted},
    )


def _seconds(*spans: timedelta) -> Decimal:
    return Decimal(int(sum((s.total_seconds() for s in spans), 0.0)))


def expected_first(s: Scenario) -> ReportCounts:
    t = s.turnarounds
    mine = ("u1", "u2", "u4", "u5", "u6", "u8")
    in_year = ("u1", "u2", "u4", "u5")
    q1, q2, q3, q4 = s.quarters
    return ReportCounts(
        lifetime=TurnaroundCounts(units=6, seconds=_seconds(*(t[k] for k in mine))),
        current_year=TurnaroundCounts(units=4, seconds=_seconds(*(t[k] for k in in_year))),
        handed_in=6,
        on_time=4,  # u1, u4, u5, u8
        quarters=[
            QuarterCounts(quarter=q1, units_submitted=0, booklets_approved=0),
            QuarterCounts(quarter=q2, units_submitted=1, booklets_approved=2),  # u8; u5, u8
            QuarterCounts(quarter=q3, units_submitted=2, booklets_approved=4),  # u1, u5; 3 + 1
            QuarterCounts(quarter=q4, units_submitted=2, booklets_approved=2),  # u2, u4; u2, u3
        ],
        open_units=1,
        booklets_pending=3,
    )


def check(overview: OverviewRepository, s: Scenario) -> None:
    cid = s.college.id
    found = overview.report_counts(cid, s.first.id, s.year, s.quarters)
    want = expected_first(s)
    assert found.lifetime == want.lifetime
    assert found.current_year == want.current_year
    assert (found.handed_in, found.on_time) == (want.handed_in, want.on_time)
    assert list(found.quarters) == list(want.quarters)
    assert (found.open_units, found.booklets_pending) == (1, 3)

    # The second SME owns u7 only: u5 left them, u7 was submitted in the current quarter.
    q1, q2, q3, q4 = s.quarters
    second = overview.report_counts(cid, s.second.id, s.year, s.quarters)
    assert second.lifetime == TurnaroundCounts(units=1, seconds=_seconds(s.turnarounds["u7"]))
    assert (second.handed_in, second.on_time) == (1, 1)
    assert list(second.quarters) == [
        QuarterCounts(quarter=q1),
        QuarterCounts(quarter=q2),
        QuarterCounts(quarter=q3),
        QuarterCounts(quarter=q4, units_submitted=1, booklets_approved=1),
    ]
    assert (second.open_units, second.booklets_pending) == (0, 0)

    # The admin owns nothing; an earlier window holds only u6, submitted and approved then.
    nothing = overview.report_counts(cid, s.college.admin.id, s.year, s.quarters)
    assert nothing.handed_in == 0 and nothing.lifetime.units == 0
    assert all(q.units_submitted == q.booklets_approved == 0 for q in nothing.quarters)
    earlier = last_quarters(q1.start, 2)
    old = overview.report_counts(cid, s.first.id, s.year, earlier)
    assert [(q.units_submitted, q.booklets_approved) for q in old.quarters] == [(1, 1), (0, 0)]
