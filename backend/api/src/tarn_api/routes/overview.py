"""The admin's dashboard and SME progress report (P29, D163, D167): ``/api/v1/overview``.
Counts of SMEs, units and booklets by stage, overdue units, recent activity, the college's
queue and storage. Never a mark, student data or speed metric."""

from fastapi import APIRouter

from tarn_api.backends import unit_of_work
from tarn_api.schemas import (
    ActivityOut,
    DashboardOut,
    ErrorOut,
    ProgressOut,
    SmeCountsOut,
    SmeProgressOut,
    StageCountsOut,
    SystemOut,
    UnitProgressOut,
    UnitStateCountsOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.overview import SmeProgress, StageCounts
from tarn_core.domain.personas import Capability
from tarn_core.services.overview import UnitRow

router = APIRouter(
    prefix="/api/v1/overview",
    tags=["overview"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

VIEW = Capability.VIEW_OVERVIEW


def stage_out(c: StageCounts) -> StageCountsOut:
    return StageCountsOut(
        processing=c.processing,
        ready=c.ready,
        in_review=c.in_review,
        approved=c.approved,
        attention=c.attention,
        total=c.total,
    )


def sme_out(p: SmeProgress) -> SmeProgressOut:
    return SmeProgressOut(
        user_id=p.user_id,
        display_name=p.display_name,
        status=p.status.value,
        units_owned=p.units_owned,
        open_units=p.open_units,
        overdue_units=p.overdue_units,
        booklets=p.booklets,
        approved=p.approved,
        pending=p.pending,
        last_activity=p.last_activity,
        heavy_load=p.heavy_load,
    )


def unit_out(row: UnitRow) -> UnitProgressOut:
    p = row.progress
    return UnitProgressOut(
        unit_id=p.unit_id,
        code=p.code,
        year=p.year,
        paper_title=row.paper_title,
        exam_code=row.exam_code,
        subject_code=row.subject_code,
        subject_name=row.subject_name,
        owner_id=p.owner_id,
        owner_name=row.owner_name,
        due_date=p.due_date,
        state=p.state.value,
        overdue=p.overdue,
        booklets=stage_out(p.stages),
    )


@router.get("", response_model=DashboardOut, summary="Dashboard figures (admin)")
def dashboard(who: PrincipalDep, backends: BackendsDep) -> DashboardOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, VIEW)
        found = unit.overview.dashboard(who.college_id, who.user_id)
    return DashboardOut(
        smes=SmeCountsOut(
            active=found.smes.active, idle=found.smes.idle, suspended=found.smes.suspended
        ),
        units=UnitStateCountsOut(
            open=found.units.open,
            submitted=found.units.submitted,
            frozen=found.units.frozen,
            overdue=found.units.overdue,
        ),
        booklets=stage_out(found.booklets),
        activity=[
            ActivityOut(
                at=a.at, action=a.action.value, actor_name=a.actor_name, unit_code=a.unit_code
            )
            for a in found.activity
        ],
    )


@router.get(
    "/progress",
    response_model=ProgressOut,
    summary="Progress per SME and per unit (admin)",
    description="Credit follows the unit's current owner (D163). No marks and no speed (D167).",
)
def progress(who: PrincipalDep, backends: BackendsDep) -> ProgressOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, VIEW)
        report = unit.overview.progress(who.college_id, who.user_id)
    return ProgressOut(
        smes=[sme_out(p) for p in report.smes], units=[unit_out(r) for r in report.units]
    )


@router.get(
    "/system",
    response_model=SystemOut,
    summary="The college's queue and storage used (admin)",
    description="API and worker status are on `GET /api/v1/health`. Listing the stored files "
    "takes longer than the other figures, so it is a call of its own.",
)
def system(who: PrincipalDep, backends: BackendsDep) -> SystemOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, VIEW)
        found = unit.overview.system(who.college_id, who.user_id)
    return SystemOut(
        queue_queued=found.queue.queued,
        queue_running=found.queue.running,
        storage_bytes=found.storage_bytes,
    )
