"""The admin's dashboard and SME progress report (P29, D163, D167): the figures on a seeded
set (before and after a transfer), Idle, overdue, "Heavy load", no marks, the capability."""

import dataclasses
from datetime import date, timedelta
from uuid import uuid4

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import APPROVED_STATUSES, BookletStatus
from tarn_core.domain.common import college_blob_key
from tarn_core.domain.overview import (
    HEAVY_LOAD_PENDING,
    STAGE_OF,
    SmeProgress,
    SmeStatus,
    Stage,
    StageCounts,
    UnitProgress,
    is_overdue,
    sme_status,
    statuses_of,
)
from tarn_core.domain.unit import UnitState
from tarn_core.errors import PermissionDeniedError
from tarn_core.ids import UserId
from tarn_core.services.overview import Dashboard, OverviewService, ProgressReport, UnitRow
from tarn_core.testing import InMemory
from tarn_core.testing.builders import CollegeFixture, Services, add_college, make_services
from tarn_core.testing.overview_contract import (
    Scenario,
    build,
    check_activity,
    check_after_transfer,
    check_before,
)


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.svc: Services = make_services(self.mem)
        self.college: CollegeFixture = add_college(self.mem, "OV")
        self.scenario: Scenario = build(self.mem, self.svc, self.college, self.mem.clock)
        m = self.mem
        self.service = OverviewService(
            overview=m.overview,
            users=m.users,
            content=m.content,
            blobs=m.blobs,
            runtime=m.runtime,
        )

    @property
    def admin(self) -> UserId:
        return self.college.admin.id


@pytest.fixture
def w() -> World:
    return World()


def test_every_booklet_status_is_in_exactly_one_stage() -> None:
    assert set(STAGE_OF) == set(BookletStatus)
    assert statuses_of(Stage.APPROVED) == APPROVED_STATUSES
    assert sum(len(statuses_of(s)) for s in Stage) == len(BookletStatus)


def test_the_figures_on_the_seeded_set(w: World) -> None:
    check_before(w.mem.overview, w.scenario, w.mem)


def test_credit_follows_the_unit_to_its_new_owner(w: World) -> None:
    s = w.scenario
    w.svc.transfers.transfer(
        s.college.id,
        w.admin,
        s.units["u1"],
        owner_id=s.second.id,
        reason="Capacity",
    )
    check_after_transfer(w.mem.overview, s, w.mem)
    check_activity(w.mem.overview, s)


def test_idle_is_computed_from_open_units(w: World) -> None:
    s = w.scenario
    cid = w.college.id
    # The second SME owns only a submitted and a frozen unit; the third is suspended.
    assert w.service.idle_smes(cid, w.admin) == {s.second.id}
    assert sme_status(active=True, open_units=0) is SmeStatus.IDLE
    assert sme_status(active=True, open_units=3) is SmeStatus.ACTIVE
    assert sme_status(active=False, open_units=0) is SmeStatus.SUSPENDED
    assert sme_status(active=False, open_units=2) is SmeStatus.SUSPENDED
    # Returning the submitted unit gives it back its SME's work: no longer idle.
    unit = w.mem.units.get(cid, s.units["u3"])
    w.mem.units.save(cid, dataclasses.replace(unit, state=UnitState.OPEN, version=unit.version + 1))
    assert w.service.idle_smes(cid, w.admin) == set()


def test_overdue_is_an_open_unit_past_its_due_date() -> None:
    today = date(2026, 10, 28)
    yesterday, tomorrow = today - timedelta(days=1), today + timedelta(days=1)
    assert is_overdue(UnitState.OPEN, yesterday, today)
    assert not is_overdue(UnitState.OPEN, today, today)
    assert not is_overdue(UnitState.OPEN, tomorrow, today)
    assert not is_overdue(UnitState.SUBMITTED, yesterday, today)
    assert not is_overdue(UnitState.FROZEN, yesterday, today)


def test_heavy_load_is_more_than_a_hundred_pending() -> None:
    def row(pending: int) -> SmeProgress:
        return SmeProgress(
            user_id=UserId(uuid4()),
            display_name="X",
            status=SmeStatus.ACTIVE,
            units_owned=1,
            open_units=1,
            overdue_units=0,
            booklets=pending,
            approved=0,
            pending=pending,
            last_activity=None,
        )

    assert HEAVY_LOAD_PENDING == 100
    assert not row(100).heavy_load
    assert row(101).heavy_load


def test_stage_counts_add_up() -> None:
    counts = StageCounts.of([BookletStatus.SCORED, BookletStatus.APPROVED, BookletStatus.FAILED])
    assert (counts.total, counts.approved, counts.pending) == (3, 1, 2)


def test_the_service_names_units_and_people(w: World) -> None:
    report = w.service.progress(w.college.id, w.admin)
    assert isinstance(report, ProgressReport)
    assert [r.display_name for r in report.smes] == ["Second SME", "Teacher OV", "Third SME"]
    row = next(r for r in report.units if r.progress.year == "2021")
    assert row.owner_name == "Teacher OV"
    assert (row.paper_title, row.subject_code) == ("Synthetic paper, CI shape", "SYN101")
    assert row.exam_code is not None and row.subject_name
    assert row.progress.overdue


def test_the_dashboard_lists_the_feed_without_details(w: World) -> None:
    w.svc.transfers.transfer(
        w.college.id, w.admin, w.scenario.units["u1"], owner_id=w.scenario.second.id, reason="R"
    )
    dash = w.service.dashboard(w.college.id, w.admin)
    assert (dash.smes.active, dash.units.overdue, dash.booklets.total) == (2, 1, 12)
    assert dash.activity
    assert {f.name for f in dataclasses.fields(dash.activity[0])} == {
        "at",
        "action",
        "actor_name",
        "unit_code",
    }
    assert dash.activity[0].action is AuditAction.UNIT_TRANSFERRED


def test_only_the_admin_sees_the_figures(w: World) -> None:
    for who in (w.scenario.first.id, w.scenario.second.id):
        with pytest.raises(PermissionDeniedError):
            w.service.dashboard(w.college.id, who)
        with pytest.raises(PermissionDeniedError):
            w.service.progress(w.college.id, who)
        with pytest.raises(PermissionDeniedError):
            w.service.system(w.college.id, who)
        with pytest.raises(PermissionDeniedError):
            w.service.idle_smes(w.college.id, who)


def test_the_system_figures_are_the_colleges_own(w: World) -> None:
    other = add_college(w.mem, "OW")
    w.mem.blobs.put(
        college_blob_key(w.college.id, "booklet", "b", "p.jpg"), b"x" * 40, "image/jpeg"
    )
    w.mem.blobs.put(college_blob_key(other.id, "booklet", "b", "p.jpg"), b"y" * 1000, "image/jpeg")
    w.mem.jobs.enqueue(w.college.id, "booklet.prepare", {})
    w.mem.jobs.enqueue(other.id, "booklet.prepare", {})
    w.mem.jobs.enqueue(other.id, "booklet.read", {})
    found = w.service.system(w.college.id, w.admin)
    assert found.storage_bytes == 40
    assert (found.queue.queued, found.queue.running) == (1, 0)


def test_no_figure_is_a_mark_or_a_speed() -> None:
    """D167: the admin sees progress, never marks; the mock's papers per hour and accuracy
    score are dropped. No field of any figure is named for them."""
    banned = ("mark", "score", "speed", "pace", "accuracy", "per_hour", "total_marks", "grade")
    for cls in (Dashboard, ProgressReport, UnitRow, SmeProgress, UnitProgress, StageCounts):
        for field in dataclasses.fields(cls):
            assert not any(word in field.name for word in banned), (cls.__name__, field.name)
