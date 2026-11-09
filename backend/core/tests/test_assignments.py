"""The SME's own figures (P31, D216): the academic year, when a unit reached its owner, its
turnaround, the averages; the summary on a seeded set; only the SME may ask."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from tarn_core.domain.assignments import (
    AcademicYear,
    AssignmentCounts,
    AssignmentSummary,
    academic_year_of,
    average_days,
    completion_percent,
    latest_submission,
    received_at,
    turnaround,
)
from tarn_core.domain.unit import UnitSubmission, UnitTransfer
from tarn_core.errors import InvariantError, PermissionDeniedError
from tarn_core.ids import CollegeId, UnitId, UnitSubmissionId, UnitTransferId, UserId
from tarn_core.services.overview import OverviewService
from tarn_core.testing import InMemory
from tarn_core.testing.assignments_contract import build, check
from tarn_core.testing.builders import add_college

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
COLLEGE = CollegeId(uuid4())
UNIT = UnitId(uuid4())
A, B, ADMIN = UserId(uuid4()), UserId(uuid4()), UserId(uuid4())


def _transfer(from_owner: UserId, to_owner: UserId, at: datetime) -> UnitTransfer:
    return UnitTransfer(
        id=UnitTransferId(uuid4()),
        college_id=COLLEGE,
        unit_id=UNIT,
        from_owner=from_owner,
        to_owner=to_owner,
        reason="Capacity",
        by=ADMIN,
        at=at,
    )


def _submission(at: datetime, *, returned: datetime | None = None) -> UnitSubmission:
    return UnitSubmission(
        id=UnitSubmissionId(uuid4()),
        college_id=COLLEGE,
        unit_id=UNIT,
        submitted_by=A,
        submitted_at=at,
        returned_by=ADMIN if returned else None,
        returned_at=returned,
        return_note="Recheck" if returned else "",
    )


@pytest.mark.parametrize(
    ("day", "start"),
    [
        (date(2026, 6, 30), date(2025, 7, 1)),
        (date(2026, 7, 1), date(2026, 7, 1)),
        (date(2026, 12, 31), date(2026, 7, 1)),
        (date(2028, 2, 29), date(2027, 7, 1)),
        (date(2027, 1, 1), date(2026, 7, 1)),
    ],
)
def test_the_academic_year_starts_on_1_july(day: date, start: date) -> None:
    year = academic_year_of(day)
    assert year.start == start
    assert year.end == date(start.year + 1, 7, 1)
    assert day in year
    assert year.end not in year and (year.start - timedelta(days=1)) not in year


def test_the_year_reads_like_2026_27() -> None:
    assert academic_year_of(date(2026, 11, 9)).label == "2026-27"
    assert academic_year_of(date(2099, 8, 1)).label == "2099-00"
    with pytest.raises(InvariantError):
        AcademicYear(start=date(2026, 7, 1), end=date(2026, 7, 1))


def test_a_unit_is_received_when_created_without_a_transfer() -> None:
    assert received_at(T0, A, []) == T0


def test_a_transfer_in_the_middle_restarts_the_clock_for_the_new_owner() -> None:
    moved = T0 + timedelta(days=3)
    transfers = [_transfer(B, A, moved)]
    assert received_at(T0, A, transfers) == moved
    done = moved + timedelta(days=2)
    assert turnaround(received_at(T0, A, transfers), done) == timedelta(days=2)


def test_the_last_transfer_to_the_owner_counts_when_it_came_back() -> None:
    back = T0 + timedelta(days=5)
    transfers = [_transfer(A, B, T0 + timedelta(days=1)), _transfer(B, A, back)]
    assert received_at(T0, A, transfers) == back
    # The SME who passed it on is not the owner: their transfer away is not a receipt.
    assert received_at(T0, B, transfers) == T0 + timedelta(days=1)


def test_a_returned_and_resubmitted_unit_counts_its_latest_submission() -> None:
    first = _submission(T0 + timedelta(days=1), returned=T0 + timedelta(days=2))
    again = _submission(T0 + timedelta(days=4))
    assert latest_submission([again, first]) == again.submitted_at
    assert turnaround(T0, latest_submission([first, again])) == timedelta(days=4)


def test_no_submission_has_no_turnaround() -> None:
    assert latest_submission([]) is None
    assert turnaround(T0, None) is None
    assert turnaround(T0, T0 - timedelta(seconds=1)) is None
    assert turnaround(T0, T0) == timedelta(0)


def test_average_days_to_one_decimal_half_up() -> None:
    assert average_days(Decimal(0), 0) is None
    assert average_days(Decimal(7.5 * 86_400), 3) == Decimal("2.5")
    assert average_days(Decimal(86_400 + 4_320), 1) == Decimal("1.1")  # 1.05 → 1.1
    assert average_days(Decimal(3_600), 2) == Decimal("0.0")
    with pytest.raises(InvariantError):
        average_days(Decimal(-1), 1)


def test_completion_percent() -> None:
    assert completion_percent(0, 0) is None
    assert completion_percent(3, 4) == Decimal("75.0")
    assert completion_percent(2, 3) == Decimal("66.7")
    assert completion_percent(0, 5) == Decimal("0.0")
    with pytest.raises(InvariantError):
        completion_percent(5, 4)


def test_counts_must_add_up() -> None:
    with pytest.raises(InvariantError):
        AssignmentCounts(lifetime=2, open=1)
    with pytest.raises(InvariantError):
        AssignmentCounts(lifetime=1, open=1, current_year=2)
    with pytest.raises(InvariantError):
        AssignmentCounts(lifetime=1, open=1, turnaround_units=1)


def test_the_summary_reads_the_counts() -> None:
    year = academic_year_of(T0.date())
    summary = AssignmentSummary(
        year=year,
        counts=AssignmentCounts(
            lifetime=4,
            current_year=3,
            open=1,
            submitted=2,
            frozen=1,
            turnaround_units=3,
            turnaround_seconds=Decimal(7.5 * 86_400),
        ),
    )
    assert (summary.completed, summary.active) == (3, 1)
    assert summary.completion_rate == Decimal("75.0")
    assert summary.average_turnaround_days == Decimal("2.5")
    empty = AssignmentSummary(year=year, counts=AssignmentCounts())
    assert empty.completion_rate is None and empty.average_turnaround_days is None


def test_the_figures_in_memory_match_the_hand_counts() -> None:
    mem = InMemory()
    scenario = build(mem, add_college(mem, "A"))
    # Another college's identical set changes nothing in the first.
    other = build(mem, add_college(mem, "B"))
    check(mem.overview, scenario)
    check(mem.overview, other)
    empty = AssignmentCounts()
    assert (
        mem.overview.assignment_counts(other.college.id, scenario.first.id, scenario.year) == empty
    )


def test_only_the_sme_reads_their_own_work() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    scenario = build(mem, college)
    overview = OverviewService(
        overview=mem.overview,
        users=mem.users,
        content=mem.content,
        blobs=mem.blobs,
        runtime=mem.runtime,
    )
    mine = overview.own_work(college.id, college.teacher.id)
    assert mine.counts.lifetime == 4 and mine.year == scenario.year
    assert mine.average_turnaround_days == Decimal("2.5")
    with pytest.raises(PermissionDeniedError):
        overview.own_work(college.id, college.admin.id)
