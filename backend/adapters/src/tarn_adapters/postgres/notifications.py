"""``NotificationRepository`` on PostgreSQL (P37): the ``notifications`` table (0022).

College RLS scopes every statement; each method also names the college and the user. A due
notice is inserted with ``ON CONFLICT DO NOTHING`` on the partial unique index
``notifications_due_once``, so the sweep's rerun adds nothing. The app role may update
``read_at`` only."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row, func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from tarn_adapters.postgres import metadata as m
from tarn_core.domain.notifications import (
    DUE_KINDS,
    Notification,
    NotificationKind,
    NotificationPage,
)
from tarn_core.errors import NotFoundError, TenantViolationError
from tarn_core.ids import CollegeId, NotificationId, UnitId, UserId

_DUE_PREDICATE = "kind IN (" + ", ".join(f"'{k}'" for k in sorted(DUE_KINDS)) + ")"


def _notification(r: Row[Any]) -> Notification:
    payload: Mapping[str, Any] = r.payload or {}
    return Notification(
        id=NotificationId(r.id),
        college_id=CollegeId(r.college_id),
        user_id=UserId(r.user_id),
        kind=NotificationKind(r.kind),
        unit_id=UnitId(r.unit_id),
        day=r.day,
        created_at=r.created_at,
        read_at=r.read_at,
        payload={str(k): str(v) for k, v in payload.items()},
    )


class PgNotificationRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def add(self, college_id: CollegeId, notification: Notification) -> bool:
        if notification.college_id != college_id:
            raise TenantViolationError("notification of another college")
        n = m.notifications
        stmt = insert(n).values(
            college_id=college_id,
            id=notification.id,
            user_id=notification.user_id,
            kind=notification.kind.value,
            unit_id=notification.unit_id,
            day=notification.day,
            payload=dict(notification.payload),
            created_at=notification.created_at,
            read_at=notification.read_at,
        )
        if notification.kind in DUE_KINDS:
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["college_id", "unit_id", "kind", "day"],
                # The partial index's predicate, written as in 0022 (a bound parameter
                # would not match it).
                index_where=text(_DUE_PREDICATE),
            )
        return self._conn.execute(stmt.returning(n.c.id)).first() is not None

    def page(
        self, college_id: CollegeId, user_id: UserId, *, limit: int, offset: int = 0
    ) -> NotificationPage:
        n = m.notifications
        mine = (n.c.college_id == college_id, n.c.user_id == user_id)
        rows = self._conn.execute(
            select(n)
            .where(*mine)
            .order_by(n.c.created_at.desc(), n.c.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        counts = self._conn.execute(
            select(
                func.count().label("total"),
                func.count().filter(n.c.read_at.is_(None)).label("unread"),
            ).where(*mine)
        ).one()
        return NotificationPage(
            items=tuple(_notification(r) for r in rows),
            total=int(counts.total),
            unread=int(counts.unread),
        )

    def unread(self, college_id: CollegeId, user_id: UserId) -> int:
        n = m.notifications
        found = self._conn.execute(
            select(func.count()).where(
                n.c.college_id == college_id, n.c.user_id == user_id, n.c.read_at.is_(None)
            )
        ).scalar_one()
        return int(found)

    def mark_read(
        self, college_id: CollegeId, user_id: UserId, notification_id: NotificationId, at: datetime
    ) -> Notification:
        n = m.notifications
        mine = (n.c.college_id == college_id, n.c.user_id == user_id, n.c.id == notification_id)
        self._conn.execute(update(n).where(*mine, n.c.read_at.is_(None)).values(read_at=at))
        row = self._conn.execute(select(n).where(*mine)).first()
        if row is None:
            raise NotFoundError(f"notification {notification_id}")
        return _notification(row)

    def mark_all_read(self, college_id: CollegeId, user_id: UserId, at: datetime) -> int:
        n = m.notifications
        result = self._conn.execute(
            update(n)
            .where(n.c.college_id == college_id, n.c.user_id == user_id, n.c.read_at.is_(None))
            .values(read_at=at)
        )
        return int(result.rowcount)

    def all(self, college_id: CollegeId) -> list[Notification]:
        """Every notice of the college, oldest first (tests)."""
        n = m.notifications
        rows = self._conn.execute(
            select(n).where(n.c.college_id == college_id).order_by(n.c.created_at, n.c.id)
        )
        return [_notification(r) for r in rows]
