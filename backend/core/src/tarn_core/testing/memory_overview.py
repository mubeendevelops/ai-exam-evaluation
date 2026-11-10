"""The overview figures (P29) computed in plain Python over the other in-memory adapters. The
PostgreSQL adapter computes the same figures with SQL aggregates; ``overview_contract`` runs
one scenario against both."""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from tarn_core.domain.assignments import (
    AcademicYear,
    AssignmentCounts,
    latest_submission,
    received_at,
    turnaround,
)
from tarn_core.domain.audit import AuditEvent
from tarn_core.domain.booklet import APPROVED_STATUSES, BookletStatus
from tarn_core.domain.overview import (
    FEED_ACTIONS,
    ActivityItem,
    QueueDepth,
    SmeCounts,
    SmeProgress,
    SmeStatus,
    StageCounts,
    UnitCounts,
    UnitProgress,
    is_overdue,
    sme_status,
)
from tarn_core.domain.reports import (
    Quarter,
    QuarterCounts,
    ReportCounts,
    TurnaroundCounts,
    submitted_on_time,
)
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import UnitState
from tarn_core.errors import NotFoundError
from tarn_core.ids import CollegeId, UnitId, UserId
from tarn_core.testing.memory_repositories import (
    MemoryBookletRepository,
    MemoryResultSheetRepository,
    MemoryUnitRepository,
    MemoryUserRepository,
)
from tarn_core.testing.memory_runtime import MemoryAuditSink, MemoryJobQueue


class MemoryOverviewRepository:
    def __init__(
        self,
        *,
        users: MemoryUserRepository,
        units: MemoryUnitRepository,
        booklets: MemoryBookletRepository,
        sheets: MemoryResultSheetRepository,
        audit: MemoryAuditSink,
        jobs: MemoryJobQueue,
    ) -> None:
        self._users = users
        self._units = units
        self._booklets = booklets
        self._sheets = sheets
        self._audit = audit
        self._jobs = jobs

    def _open_units(self, college_id: CollegeId) -> dict[UserId, int]:
        found: dict[UserId, int] = defaultdict(int)
        for unit in self._units.list(college_id):
            if unit.state is UnitState.OPEN:
                found[unit.owner_id] += 1
        return found

    def _live_smes(self, college_id: CollegeId) -> list[User]:
        return [u for u in self._users.list(college_id) if u.role is Role.SME and not u.removed]

    def sme_counts(self, college_id: CollegeId) -> SmeCounts:
        open_units = self._open_units(college_id)
        states = [
            sme_status(active=u.active, open_units=open_units.get(u.id, 0))
            for u in self._live_smes(college_id)
        ]
        return SmeCounts(
            active=states.count(SmeStatus.ACTIVE),
            idle=states.count(SmeStatus.IDLE),
            suspended=states.count(SmeStatus.SUSPENDED),
        )

    def unit_counts(self, college_id: CollegeId, today: date) -> UnitCounts:
        units = self._units.list(college_id)
        return UnitCounts(
            open=sum(1 for u in units if u.state is UnitState.OPEN),
            submitted=sum(1 for u in units if u.state is UnitState.SUBMITTED),
            frozen=sum(1 for u in units if u.state is UnitState.FROZEN),
            overdue=sum(1 for u in units if is_overdue(u.state, u.due_date, today)),
        )

    def booklet_stages(self, college_id: CollegeId) -> StageCounts:
        return StageCounts.of(b.status for b in self._booklets.list(college_id))

    def _statuses_by_unit(self, college_id: CollegeId) -> dict[UnitId, list[BookletStatus]]:
        found: dict[UnitId, list[BookletStatus]] = defaultdict(list)
        for booklet in self._booklets.list(college_id):
            found[booklet.unit_id].append(booklet.status)
        return found

    def sme_progress(self, college_id: CollegeId, today: date) -> Sequence[SmeProgress]:
        by_unit = self._statuses_by_unit(college_id)
        last: dict[UserId, datetime] = {}
        for event in self._audit.events:
            if event.college_id != college_id or event.actor_id is None:
                continue
            if event.action.value.startswith("auth."):
                continue
            if event.actor_id not in last or event.at > last[event.actor_id]:
                last[event.actor_id] = event.at
        units = self._units.list(college_id)
        rows = []
        for user in self._live_smes(college_id):
            mine = [u for u in units if u.owner_id == user.id]
            open_units = [u for u in mine if u.state is UnitState.OPEN]
            total = sum(len(by_unit.get(u.id, [])) for u in mine)
            approved = sum(StageCounts.of(by_unit.get(u.id, [])).approved for u in mine)
            pending = sum(StageCounts.of(by_unit.get(u.id, [])).pending for u in open_units)
            rows.append(
                SmeProgress(
                    user_id=user.id,
                    display_name=user.display_name,
                    status=sme_status(active=user.active, open_units=len(open_units)),
                    units_owned=len(mine),
                    open_units=len(open_units),
                    overdue_units=sum(1 for u in mine if is_overdue(u.state, u.due_date, today)),
                    booklets=total,
                    approved=approved,
                    pending=pending,
                    last_activity=last.get(user.id),
                )
            )
        return sorted(rows, key=lambda r: (r.display_name.casefold(), str(r.user_id)))

    def unit_progress(self, college_id: CollegeId, today: date) -> Sequence[UnitProgress]:
        by_unit = self._statuses_by_unit(college_id)
        rows = [
            UnitProgress(
                unit_id=u.id,
                code=u.code,
                year=u.year,
                paper_id=u.paper_id,
                subject_id=u.subject_id,
                owner_id=u.owner_id,
                due_date=u.due_date,
                state=u.state,
                overdue=is_overdue(u.state, u.due_date, today),
                stages=StageCounts.of(by_unit.get(u.id, [])),
            )
            for u in self._units.list(college_id)
        ]
        return list(reversed(rows))  # units are kept oldest first

    def owners_of_open_units(self, college_id: CollegeId) -> frozenset[UserId]:
        return frozenset(self._open_units(college_id))

    def activity(self, college_id: CollegeId, limit: int) -> Sequence[ActivityItem]:
        names = {u.id: u.display_name for u in self._users.list(college_id)}
        codes = {u.id: u.code for u in self._units.list(college_id)}
        events = [
            e for e in self._audit.events if e.college_id == college_id and e.action in FEED_ACTIONS
        ]
        newest_first = sorted(
            enumerate(events), key=lambda pair: (pair[1].at, pair[0]), reverse=True
        )[:limit]
        items = []
        for _, event in newest_first:
            items.append(
                ActivityItem(
                    at=event.at,
                    action=event.action,
                    actor_name=names.get(event.actor_id) if event.actor_id else None,
                    unit_code=self._unit_code(college_id, event, codes),
                )
            )
        return items

    def _unit_code(
        self, college_id: CollegeId, event: AuditEvent, codes: Mapping[UnitId, str]
    ) -> str | None:
        unit_id: UnitId | None = None
        named = event.after.get("unit_id") if isinstance(event.after, dict) else None
        if isinstance(named, str):
            try:
                unit_id = UnitId(UUID(named))
            except ValueError:
                unit_id = None
        if unit_id is None and event.booklet_id is not None:
            try:
                unit_id = self._booklets.get(college_id, event.booklet_id).unit_id
            except NotFoundError:
                unit_id = None
        return codes.get(unit_id) if unit_id is not None else None

    def queue_depth(self, college_id: CollegeId) -> QueueDepth:
        mine = [j for j in self._jobs.jobs if j.college_id == college_id]
        return QueueDepth(
            queued=sum(1 for j in mine if j.status == "queued"),
            running=sum(1 for j in mine if j.status == "running"),
        )

    def assignment_counts(
        self, college_id: CollegeId, owner_id: UserId, year: AcademicYear
    ) -> AssignmentCounts:
        mine = [u for u in self._units.list(college_id) if u.owner_id == owner_id]
        closed = [u for u in mine if u.state is not UnitState.OPEN]
        spans = [
            turnaround(
                received_at(u.created_at, u.owner_id, self._units.transfers(college_id, u.id)),
                latest_submission(self._units.submissions(college_id, u.id)),
            )
            for u in closed
        ]
        found = [s for s in spans if s is not None]
        return AssignmentCounts(
            lifetime=len(mine),
            current_year=sum(1 for u in mine if u.due_date in year),
            open=sum(1 for u in mine if u.state is UnitState.OPEN),
            submitted=sum(1 for u in mine if u.state is UnitState.SUBMITTED),
            frozen=sum(1 for u in mine if u.state is UnitState.FROZEN),
            turnaround_units=len(found),
            turnaround_seconds=sum(
                (
                    Decimal(s.days * 86_400 + s.seconds) + Decimal(s.microseconds) / 1_000_000
                    for s in found
                ),
                Decimal(0),
            ),
        )

    def report_counts(
        self,
        college_id: CollegeId,
        owner_id: UserId,
        year: AcademicYear,
        quarters: Sequence[Quarter],
    ) -> ReportCounts:
        mine = [u for u in self._units.list(college_id) if u.owner_id == owner_id]
        spans: list[tuple[date, timedelta | None, datetime]] = []
        for unit in mine:
            if unit.state is UnitState.OPEN:
                continue
            last = latest_submission(self._units.submissions(college_id, unit.id))
            if last is None:
                continue
            received = received_at(
                unit.created_at, unit.owner_id, self._units.transfers(college_id, unit.id)
            )
            spans.append((unit.due_date, turnaround(received, last), last))

        def averaged(rows: list[timedelta]) -> TurnaroundCounts:
            return TurnaroundCounts(
                units=len(rows),
                seconds=sum(
                    (
                        Decimal(r.days * 86_400 + r.seconds) + Decimal(r.microseconds) / 1_000_000
                        for r in rows
                    ),
                    Decimal(0),
                ),
            )

        timed = [(due, span) for due, span, _ in spans if span is not None]
        open_ids = {u.id for u in mine if u.state is UnitState.OPEN}
        by_unit = self._statuses_by_unit(college_id)
        first_sheet: dict[UnitId, list[datetime]] = defaultdict(list)
        mine_ids = {u.id for u in mine}
        for booklet in self._booklets.list(college_id):
            if booklet.unit_id in mine_ids and booklet.status in APPROVED_STATUSES:
                sheets = self._sheets.versions(college_id, booklet.id)
                if sheets:
                    first_sheet[booklet.unit_id].append(sheets[0].issued_at)
        approved_at = [at for times in first_sheet.values() for at in times]
        return ReportCounts(
            lifetime=averaged([span for _, span in timed]),
            current_year=averaged([span for due, span in timed if due in year]),
            handed_in=len(spans),
            on_time=sum(1 for due, _, last in spans if submitted_on_time(last, due)),
            quarters=[
                QuarterCounts(
                    quarter=q,
                    units_submitted=sum(1 for _, _, last in spans if q.start_at <= last < q.end_at),
                    booklets_approved=sum(1 for at in approved_at if q.start_at <= at < q.end_at),
                )
                for q in quarters
            ],
            open_units=len(open_ids),
            booklets_pending=sum(StageCounts.of(by_unit.get(u, [])).pending for u in open_ids),
        )
