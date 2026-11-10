"""My Reports (P36, D226, D239): calendar quarters and their edges, the on-time rule (a UTC
date), the averages, and the figures on a scenario with boundaries, a returned and resubmitted
unit and a transfer; only the SME may ask."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from tarn_core.domain.assignments import academic_year_of
from tarn_core.domain.reports import (
    Quarter,
    QuarterCounts,
    ReportCounts,
    TurnaroundCounts,
    last_quarters,
    on_time_percent,
    quarter_of,
    submitted_on_time,
)
from tarn_core.errors import InvariantError, PermissionDeniedError
from tarn_core.services.overview import OverviewService
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, make_services
from tarn_core.testing.reports_contract import build, check, expected_first

# --- quarters ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "year", "number"),
    [
        (date(2026, 1, 1), 2026, 1),
        (date(2026, 3, 31), 2026, 1),
        (date(2026, 4, 1), 2026, 2),
        (date(2026, 6, 30), 2026, 2),
        (date(2026, 7, 1), 2026, 3),
        (date(2026, 9, 30), 2026, 3),
        (date(2026, 10, 1), 2026, 4),
        (date(2026, 12, 31), 2026, 4),
        (date(2027, 1, 1), 2027, 1),
    ],
)
def test_quarter_boundaries(day: date, year: int, number: int) -> None:
    quarter = quarter_of(day)
    assert (quarter.year, quarter.number) == (year, number)
    assert day in quarter


def test_a_quarter_spans_its_three_months_and_the_next_starts_where_it_ends() -> None:
    q4 = Quarter(2026, 4)
    assert (q4.start, q4.end) == (date(2026, 10, 1), date(2027, 1, 1))
    assert (q4.start_at, q4.end_at) == (
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert date(2027, 1, 1) not in q4 and date(2026, 9, 30) not in q4
    assert q4.label == "2026 Q4"
    for q in (Quarter(2026, n) for n in (1, 2, 3)):
        assert q.end == Quarter(2026, q.number + 1).start
    with pytest.raises(InvariantError):
        Quarter(2026, 5)
    with pytest.raises(InvariantError):
        Quarter(2026, 0)


def test_the_last_four_quarters_end_with_the_current_one_across_a_year_end() -> None:
    assert [q.label for q in last_quarters(date(2026, 10, 3))] == [
        "2026 Q1",
        "2026 Q2",
        "2026 Q3",
        "2026 Q4",
    ]
    assert [q.label for q in last_quarters(date(2027, 2, 14))] == [
        "2026 Q2",
        "2026 Q3",
        "2026 Q4",
        "2027 Q1",
    ]
    assert [q.label for q in last_quarters(date(2027, 1, 1), 2)] == ["2026 Q4", "2027 Q1"]
    with pytest.raises(InvariantError):
        last_quarters(date(2026, 10, 3), 0)


# --- on time and the averages -------------------------------------------------------------------


def test_on_time_is_a_utc_date_on_or_before_the_due_date() -> None:
    due = date(2026, 7, 10)
    assert submitted_on_time(datetime(2026, 7, 10, 23, 59, 59, tzinfo=UTC), due)
    assert not submitted_on_time(datetime(2026, 7, 11, 0, 0, 0, tzinfo=UTC), due)
    assert submitted_on_time(datetime(2026, 7, 1, tzinfo=UTC), due)
    # 01:00 on the 11th in India is still the 10th in UTC; 23:00 on the 10th in UTC-5 is the 11th.
    assert submitted_on_time(
        datetime(2026, 7, 11, 1, 0, tzinfo=timezone(timedelta(hours=5.5))), due
    )
    assert not submitted_on_time(
        datetime(2026, 7, 10, 23, 0, tzinfo=timezone(timedelta(hours=-5))), due
    )
    with pytest.raises(InvariantError):
        submitted_on_time(datetime(2026, 7, 10), due)


def test_the_on_time_rate_is_a_percent_to_one_decimal() -> None:
    assert on_time_percent(2, 3) == Decimal("66.7")
    assert on_time_percent(0, 4) == Decimal("0.0")
    assert on_time_percent(4, 4) == Decimal("100.0")
    assert on_time_percent(0, 0) is None
    with pytest.raises(InvariantError):
        on_time_percent(3, 2)
    with pytest.raises(InvariantError):
        on_time_percent(-1, 2)


def test_the_average_turnaround_is_days_to_one_decimal_or_none() -> None:
    assert TurnaroundCounts(units=3, seconds=Decimal(int(7.5 * 86_400))).average_days == Decimal(
        "2.5"
    )
    assert TurnaroundCounts().average_days is None
    with pytest.raises(InvariantError):
        TurnaroundCounts(units=-1)


def test_the_counts_refuse_what_cannot_be_true() -> None:
    quarters = [QuarterCounts(quarter=Quarter(2026, 4))]
    ok = {
        "lifetime": TurnaroundCounts(units=2, seconds=Decimal(1)),
        "current_year": TurnaroundCounts(units=1, seconds=Decimal(1)),
        "handed_in": 2,
        "on_time": 1,
        "quarters": quarters,
        "open_units": 0,
        "booklets_pending": 0,
    }
    assert ReportCounts(**ok).on_time == 1  # type: ignore[arg-type]
    for bad in (
        {"on_time": 3},
        {"handed_in": 1, "on_time": 1},
        {"current_year": TurnaroundCounts(units=3, seconds=Decimal(1))},
        {"open_units": -1},
    ):
        with pytest.raises(InvariantError):
            ReportCounts(**{**ok, **bad})  # type: ignore[arg-type]
    with pytest.raises(InvariantError):
        QuarterCounts(quarter=Quarter(2026, 4), units_submitted=-1)


# --- the scenario --------------------------------------------------------------------------------


def test_the_figures_in_memory_match_the_hand_counts() -> None:
    mem = InMemory()
    svc = make_services(mem)
    scenario = build(mem, svc, add_college(mem, "A"))
    other = build(mem, svc, add_college(mem, "B"))  # another college's identical set
    check(mem.overview, scenario)
    check(mem.overview, other)
    # B's SME asked about in A's college: nothing of B's enters A's figures.
    found = mem.overview.report_counts(
        scenario.college.id, other.first.id, scenario.year, scenario.quarters
    )
    assert found.handed_in == 0 and found.open_units == 0


def test_a_unit_resubmitted_counts_once_in_the_quarter_of_its_latest_submission() -> None:
    mem = InMemory()
    scenario = build(mem, make_services(mem), add_college(mem, "A"))
    found = mem.overview.report_counts(
        scenario.college.id, scenario.first.id, scenario.year, scenario.quarters
    )
    # u4 was first submitted in q2 and again in q4: q2 holds u8 only, q4 holds u2 and u4.
    assert [q.units_submitted for q in found.quarters] == [0, 1, 2, 2]


def test_credit_follows_the_unit_after_a_transfer() -> None:
    mem = InMemory()
    scenario = build(mem, make_services(mem), add_college(mem, "A"))
    cid = scenario.college.id
    # u5 was the second SME's, moved to the first: its turnaround runs from the transfer, its
    # approved booklet and submission are the first SME's now and gone from the second's.
    want = scenario.turnarounds["u5"]
    assert want == timedelta(days=2)
    first = mem.overview.report_counts(cid, scenario.first.id, scenario.year, scenario.quarters)
    second = mem.overview.report_counts(cid, scenario.second.id, scenario.year, scenario.quarters)
    assert first.lifetime.units == 6 and second.lifetime.units == 1
    assert [q.booklets_approved for q in second.quarters] == [0, 0, 0, 1]  # u7's, not u5's
    assert first.quarters[1].booklets_approved == 2  # u5's (approved by the second) and u8's


def test_the_report_is_for_the_sme_alone() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    scenario = build(mem, make_services(mem), college)
    overview = OverviewService(
        overview=mem.overview,
        users=mem.users,
        content=mem.content,
        blobs=mem.blobs,
        runtime=mem.runtime,
    )
    mine = overview.my_report(college.id, college.teacher.id)
    assert mine.year == academic_year_of(mem.clock.now().date())
    assert mine.counts.handed_in == 6 and mine.on_time_rate == Decimal("66.7")
    assert mine.counts.lifetime == expected_first(scenario).lifetime
    for who in (college.admin.id,):
        with pytest.raises(PermissionDeniedError):
            overview.my_report(college.id, who)
