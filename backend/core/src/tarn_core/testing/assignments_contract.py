"""The SME's own figures (P31, D216) on one scenario, for every ``OverviewRepository``: the
in-memory one and PostgreSQL's SQL aggregates must give the same answers.

``build`` gives the college's SME (``first``) four units and a second SME one:

- u1 (first): created at t0, submitted at t0 + 36 h: 1.5 days.
- u2 (created for the second SME at t0, transferred to first at t0 + 24 h, submitted at
  t0 + 72 h, frozen): 2 days, counted from the transfer.
- u3 (first): submitted at t0 + 12 h, returned, resubmitted at t0 + 96 h: 4 days.
- u4 (first): open, due in the next academic year.
- u5 (created for first, transferred to the second SME, submitted): the second SME's only.

So the first SME owns 4 units (3 this academic year), 1 open, 2 submitted, 1 frozen, average
turnaround (1.5 + 2 + 4) / 3 = 2.5 days, completion 75 %. Test code that lives in the package
so adapters can reuse it."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal

from tarn_core.domain.assignments import AcademicYear, AssignmentCounts, academic_year_of
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import UnitState, UnitSubmission, UnitTransfer
from tarn_core.ids import UnitId, UnitSubmissionId, UnitTransferId, UserId
from tarn_core.ports.repositories import OverviewRepository
from tarn_core.testing.builders import Backend, CollegeFixture, ci_shaped_blueprint, unit_for


@dataclass
class Scenario:
    college: CollegeFixture
    first: User
    second: User
    units: dict[str, UnitId]
    year: AcademicYear


def build(mem: Backend, college: CollegeFixture) -> Scenario:
    cid = college.id
    admin = college.admin.id
    paper = ci_shaped_blueprint(mem, college)
    second = User(
        id=UserId(mem.ids.new()),
        college_id=cid,
        display_name="Second SME",
        email="second.sme@example.test",
        role=Role.SME,
    )
    mem.users.save(cid, second)
    first = college.teacher
    t0 = mem.runtime.clock.now()
    today = t0.date()
    year = academic_year_of(today)

    def hours(n: float) -> datetime:
        return t0 + timedelta(hours=n)

    units: dict[str, UnitId] = {}
    owners = (("u1", first), ("u2", second), ("u3", first), ("u4", first), ("u5", first))
    for number, (key, owner) in enumerate(owners, start=1):
        units[key] = unit_for(mem, cid, paper.id, owner_id=owner.id, year=f"202{number}")

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

    def submit(key: str, at: datetime) -> UnitSubmission:
        unit = mem.units.get(cid, units[key])
        submission = UnitSubmission(
            id=UnitSubmissionId(mem.ids.new()),
            college_id=cid,
            unit_id=unit.id,
            submitted_by=unit.owner_id,
            submitted_at=at,
        )
        mem.units.save_submission(cid, submission)
        mem.units.save(cid, replace(unit, state=UnitState.SUBMITTED, version=unit.version + 1))
        return submission

    def give_back(key: str, submission: UnitSubmission, at: datetime) -> None:
        mem.units.save_submission(
            cid, replace(submission, returned_by=admin, returned_at=at, return_note="Recheck")
        )
        unit = mem.units.get(cid, units[key])
        mem.units.save(cid, replace(unit, state=UnitState.OPEN, version=unit.version + 1))

    submit("u1", hours(36))
    transfer("u2", first, hours(24))
    submit("u2", hours(72))
    u2 = mem.units.get(cid, units["u2"])
    mem.units.save(cid, replace(u2, state=UnitState.FROZEN, version=u2.version + 1))
    give_back("u3", submit("u3", hours(12)), hours(24))
    submit("u3", hours(96))
    u4 = mem.units.get(cid, units["u4"])
    mem.units.save(cid, replace(u4, due_date=year.end + timedelta(days=30), version=u4.version + 1))
    transfer("u5", second, hours(1))
    submit("u5", hours(49))
    return Scenario(college=college, first=first, second=second, units=units, year=year)


def check(overview: OverviewRepository, s: Scenario) -> None:
    cid = s.college.id
    assert overview.assignment_counts(cid, s.first.id, s.year) == AssignmentCounts(
        lifetime=4,
        current_year=3,
        open=1,
        submitted=2,
        frozen=1,
        turnaround_units=3,
        turnaround_seconds=Decimal(int(7.5 * 86_400)),
    )
    # The second SME: only u5 (u2 left them), 2 days from the transfer to them.
    assert overview.assignment_counts(cid, s.second.id, s.year) == AssignmentCounts(
        lifetime=1,
        current_year=1,
        submitted=1,
        turnaround_units=1,
        turnaround_seconds=Decimal(2 * 86_400),
    )
    # The admin owns nothing; next year holds only u4.
    assert overview.assignment_counts(cid, s.college.admin.id, s.year) == AssignmentCounts()
    later = AcademicYear(start=s.year.end, end=s.year.end.replace(year=s.year.end.year + 1))
    assert overview.assignment_counts(cid, s.first.id, later).current_year == 1
