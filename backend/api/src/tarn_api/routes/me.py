"""The signed-in SME's own figures (P31, P36, D216, D226): ``/api/v1/me``. Counts of their own
current units only (credit follows the owner, D163); never another SME's, never a mark."""

from decimal import Decimal

from fastapi import APIRouter

from tarn_api.backends import unit_of_work
from tarn_api.schemas import (
    AcademicYearOut,
    AssignmentSummaryOut,
    ErrorOut,
    MyReportOut,
    QuarterOut,
    TurnaroundOut,
    UnitBreakdownOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.personas import Capability

router = APIRouter(
    prefix="/api/v1/me",
    tags=["me"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)


@router.get(
    "/assignments/summary",
    response_model=AssignmentSummaryOut,
    summary="My Assignments figures (SME)",
    description="Units owned (lifetime and in the current academic year, 1 July start, by due "
    "date), completed (submitted or frozen), active (open), completion rate, the state "
    "breakdown and the average turnaround (D216).",
)
def assignments_summary(who: PrincipalDep, backends: BackendsDep) -> AssignmentSummaryOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        found = unit.overview.own_work(who.college_id, who.user_id)
    rate, days = found.completion_rate, found.average_turnaround_days
    return AssignmentSummaryOut(
        academic_year=AcademicYearOut(
            start=found.year.start, end=found.year.end, label=found.year.label
        ),
        units_lifetime=found.counts.lifetime,
        units_current_year=found.counts.current_year,
        completed=found.completed,
        active=found.active,
        completion_rate=None if rate is None else float(rate),
        breakdown=UnitBreakdownOut(
            frozen=found.counts.frozen, submitted=found.counts.submitted, open=found.counts.open
        ),
        average_turnaround_days=None if days is None else float(days),
        turnaround_units=found.counts.turnaround_units,
    )


def _rounded(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@router.get(
    "/reports",
    response_model=MyReportOut,
    summary="My Reports figures (SME)",
    description="Average turnaround (lifetime and the current academic year), on-time rate, "
    "units submitted and booklets approved in each of the last four calendar quarters, open "
    "units and booklets pending. Over the SME's own current units (credit follows the owner, "
    "D163). No accuracy score, speed or marks (D167, D226, D239).",
)
def reports(who: PrincipalDep, backends: BackendsDep) -> MyReportOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        found = unit.overview.my_report(who.college_id, who.user_id)
    counts = found.counts
    return MyReportOut(
        academic_year=AcademicYearOut(
            start=found.year.start, end=found.year.end, label=found.year.label
        ),
        turnaround_lifetime=TurnaroundOut(
            average_days=_rounded(counts.lifetime.average_days), units=counts.lifetime.units
        ),
        turnaround_year=TurnaroundOut(
            average_days=_rounded(counts.current_year.average_days),
            units=counts.current_year.units,
        ),
        on_time_rate=_rounded(found.on_time_rate),
        units_handed_in=counts.handed_in,
        quarters=[
            QuarterOut(
                label=q.quarter.label,
                start=q.quarter.start,
                end=q.quarter.end,
                units_submitted=q.units_submitted,
                booklets_approved=q.booklets_approved,
            )
            for q in counts.quarters
        ],
        open_units=counts.open_units,
        booklets_pending=counts.booklets_pending,
    )
