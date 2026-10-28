"""``OverviewRepository`` on PostgreSQL (P29, D167): every figure is one aggregate query
(``count(*) FILTER``, ``GROUP BY``, ``max``), never booklets, units or audit events loaded and
counted in Python. College row-level security applies like everywhere (the explicit
``college_id`` conditions are the second line). Stages are counted from ``STAGE_OF`` so the
core's table is the only definition."""

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, Connection, Text, and_, cast, exists, func, not_, select

from tarn_adapters.postgres import metadata as m
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import APPROVED_STATUSES
from tarn_core.domain.overview import (
    FEED_ACTIONS,
    ActivityItem,
    QueueDepth,
    SmeCounts,
    SmeProgress,
    Stage,
    StageCounts,
    UnitCounts,
    UnitProgress,
    sme_status,
    statuses_of,
)
from tarn_core.domain.tenancy import Role
from tarn_core.domain.unit import UnitState
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UnitId, UserId


def _in_stage(stage: Stage) -> ColumnElement[bool]:
    return m.booklets.c.status.in_(sorted(s.value for s in statuses_of(stage)))


_APPROVED = m.booklets.c.status.in_(sorted(s.value for s in APPROVED_STATUSES))


def _stage_columns() -> list[Any]:
    """``count(*)`` per stage over the booklets of the statement, labelled by stage name."""
    return [func.count(m.booklets.c.id).filter(_in_stage(s)).label(s.value) for s in Stage]


def _stages(r: Any) -> StageCounts:
    return StageCounts(**{s.value: int(getattr(r, s.value)) for s in Stage})


class PgOverviewRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def sme_counts(self, college_id: CollegeId) -> SmeCounts:
        u, n = m.users, m.units
        owns_open = exists().where(
            n.c.college_id == u.c.college_id, n.c.owner_id == u.c.id, n.c.state == "open"
        )
        row = self._conn.execute(
            select(
                func.count().filter(not_(u.c.active)).label("suspended"),
                func.count().filter(and_(u.c.active, not_(owns_open))).label("idle"),
                func.count().filter(and_(u.c.active, owns_open)).label("active"),
            ).where(
                u.c.college_id == college_id, u.c.role == Role.SME.value, u.c.removed_at.is_(None)
            )
        ).one()
        return SmeCounts(active=row.active, idle=row.idle, suspended=row.suspended)

    def unit_counts(self, college_id: CollegeId, today: date) -> UnitCounts:
        n = m.units
        row = self._conn.execute(
            select(
                func.count().filter(n.c.state == UnitState.OPEN.value).label("open"),
                func.count().filter(n.c.state == UnitState.SUBMITTED.value).label("submitted"),
                func.count().filter(n.c.state == UnitState.FROZEN.value).label("frozen"),
                func.count()
                .filter(and_(n.c.state == UnitState.OPEN.value, n.c.due_date < today))
                .label("overdue"),
            ).where(n.c.college_id == college_id)
        ).one()
        return UnitCounts(
            open=row.open, submitted=row.submitted, frozen=row.frozen, overdue=row.overdue
        )

    def booklet_stages(self, college_id: CollegeId) -> StageCounts:
        row = self._conn.execute(
            select(*_stage_columns()).where(m.booklets.c.college_id == college_id)
        ).one()
        return _stages(row)

    def sme_progress(self, college_id: CollegeId, today: date) -> Sequence[SmeProgress]:
        u, n, b, a = m.users, m.units, m.booklets, m.audit_events
        owned = (
            select(
                n.c.owner_id,
                func.count().label("units_owned"),
                func.count().filter(n.c.state == "open").label("open_units"),
                func.count()
                .filter(and_(n.c.state == "open", n.c.due_date < today))
                .label("overdue_units"),
            )
            .where(n.c.college_id == college_id)
            .group_by(n.c.owner_id)
            .subquery("owned")
        )
        work = (
            select(
                n.c.owner_id,
                func.count(b.c.id).label("booklets"),
                func.count(b.c.id).filter(_APPROVED).label("approved"),
                func.count(b.c.id)
                .filter(and_(n.c.state == "open", not_(_APPROVED)))
                .label("pending"),
            )
            .select_from(b.join(n, and_(n.c.college_id == b.c.college_id, n.c.id == b.c.unit_id)))
            .where(b.c.college_id == college_id)
            .group_by(n.c.owner_id)
            .subquery("work")
        )
        acted = (
            select(a.c.actor_id, func.max(a.c.at).label("last_activity"))
            .where(
                a.c.college_id == college_id,
                a.c.actor_id.is_not(None),
                not_(a.c.action.startswith("auth.")),
            )
            .group_by(a.c.actor_id)
            .subquery("acted")
        )
        rows = self._conn.execute(
            select(
                u.c.id,
                u.c.display_name,
                u.c.active,
                func.coalesce(owned.c.units_owned, 0).label("units_owned"),
                func.coalesce(owned.c.open_units, 0).label("open_units"),
                func.coalesce(owned.c.overdue_units, 0).label("overdue_units"),
                func.coalesce(work.c.booklets, 0).label("booklets"),
                func.coalesce(work.c.approved, 0).label("approved"),
                func.coalesce(work.c.pending, 0).label("pending"),
                acted.c.last_activity,
            )
            .select_from(
                u.outerjoin(owned, owned.c.owner_id == u.c.id)
                .outerjoin(work, work.c.owner_id == u.c.id)
                .outerjoin(acted, acted.c.actor_id == u.c.id)
            )
            .where(
                u.c.college_id == college_id, u.c.role == Role.SME.value, u.c.removed_at.is_(None)
            )
            .order_by(func.lower(u.c.display_name), u.c.id)
        )
        return [
            SmeProgress(
                user_id=UserId(r.id),
                display_name=r.display_name,
                status=sme_status(active=r.active, open_units=r.open_units),
                units_owned=r.units_owned,
                open_units=r.open_units,
                overdue_units=r.overdue_units,
                booklets=r.booklets,
                approved=r.approved,
                pending=r.pending,
                last_activity=r.last_activity,
            )
            for r in rows
        ]

    def unit_progress(self, college_id: CollegeId, today: date) -> Sequence[UnitProgress]:
        n, b = m.units, m.booklets
        overdue = and_(n.c.state == "open", n.c.due_date < today).label("overdue")
        rows = self._conn.execute(
            select(
                n.c.id,
                n.c.code,
                n.c.year,
                n.c.paper_id,
                n.c.subject_id,
                n.c.owner_id,
                n.c.due_date,
                n.c.state,
                n.c.seq,
                overdue,
                *_stage_columns(),
            )
            .select_from(
                n.outerjoin(b, and_(b.c.college_id == n.c.college_id, b.c.unit_id == n.c.id))
            )
            .where(n.c.college_id == college_id)
            .group_by(
                n.c.college_id,
                n.c.id,
                n.c.code,
                n.c.year,
                n.c.paper_id,
                n.c.subject_id,
                n.c.owner_id,
                n.c.due_date,
                n.c.state,
                n.c.seq,
            )
            .order_by(n.c.seq.desc())
        )
        return [
            UnitProgress(
                unit_id=UnitId(r.id),
                code=r.code,
                year=r.year,
                paper_id=BlueprintId(r.paper_id),
                subject_id=SubjectId(r.subject_id),
                owner_id=UserId(r.owner_id),
                due_date=r.due_date,
                state=UnitState(r.state),
                overdue=bool(r.overdue),
                stages=_stages(r),
            )
            for r in rows
        ]

    def owners_of_open_units(self, college_id: CollegeId) -> frozenset[UserId]:
        n = m.units
        rows = self._conn.execute(
            select(n.c.owner_id).where(n.c.college_id == college_id, n.c.state == "open").distinct()
        )
        return frozenset(UserId(r.owner_id) for r in rows)

    def activity(self, college_id: CollegeId, limit: int) -> Sequence[ActivityItem]:
        a, u, b, n = m.audit_events, m.users, m.booklets, m.units
        latest = (
            select(a.c.at, a.c.seq, a.c.action, a.c.actor_id, a.c.booklet_id, a.c.after)
            .where(
                a.c.college_id == college_id,
                a.c.action.in_(sorted(x.value for x in FEED_ACTIONS)),
            )
            .order_by(a.c.at.desc(), a.c.seq.desc())
            .limit(limit)
            .subquery("latest")
        )
        # Compared as text: an event's unit id comes out of JSON and is never cast.
        unit_text = func.coalesce(latest.c.after["unit_id"].astext, cast(b.c.unit_id, Text))
        rows = self._conn.execute(
            select(latest.c.at, latest.c.action, u.c.display_name, n.c.code)
            .select_from(
                latest.outerjoin(u, and_(u.c.college_id == college_id, u.c.id == latest.c.actor_id))
                .outerjoin(b, and_(b.c.college_id == college_id, b.c.id == latest.c.booklet_id))
                .outerjoin(n, and_(n.c.college_id == college_id, cast(n.c.id, Text) == unit_text))
            )
            .order_by(latest.c.at.desc(), latest.c.seq.desc())
        )
        return [
            ActivityItem(
                at=r.at, action=AuditAction(r.action), actor_name=r.display_name, unit_code=r.code
            )
            for r in rows
        ]

    def queue_depth(self, college_id: CollegeId) -> QueueDepth:
        j = m.jobs
        row = self._conn.execute(
            select(
                func.count().filter(j.c.status == "queued").label("queued"),
                func.count().filter(j.c.status == "running").label("running"),
            ).where(j.c.college_id == college_id)
        ).one()
        return QueueDepth(queued=row.queued, running=row.running)
