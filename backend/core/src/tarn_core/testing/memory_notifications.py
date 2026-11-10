"""``NotificationRepository`` in memory (P37), like the ``notifications`` table (0022): partitioned
by college, the (unit, kind, day) of a due notice unique."""

from dataclasses import replace
from datetime import datetime

from tarn_core.domain.notifications import DUE_KINDS, Notification, NotificationPage
from tarn_core.errors import NotFoundError, TenantViolationError
from tarn_core.ids import CollegeId, NotificationId, UserId
from tarn_core.testing.memory_repositories import TenantLog


class MemoryNotificationRepository:
    def __init__(self, log: TenantLog) -> None:
        self._log = log
        self._rows: dict[CollegeId, dict[NotificationId, Notification]] = {}

    def _of(self, college_id: CollegeId) -> dict[NotificationId, Notification]:
        self._log.touch(college_id)
        return self._rows.setdefault(college_id, {})

    def add(self, college_id: CollegeId, notification: Notification) -> bool:
        if notification.college_id != college_id:
            raise TenantViolationError("notification of another college")
        rows = self._of(college_id)
        if notification.kind in DUE_KINDS and any(
            n.unit_id == notification.unit_id
            and n.kind is notification.kind
            and n.day == notification.day
            for n in rows.values()
        ):
            return False
        rows[notification.id] = notification
        return True

    def _mine(self, college_id: CollegeId, user_id: UserId) -> list[Notification]:
        found = [n for n in self._of(college_id).values() if n.user_id == user_id]
        found.sort(key=lambda n: (n.created_at, str(n.id)), reverse=True)
        return found

    def page(
        self, college_id: CollegeId, user_id: UserId, *, limit: int, offset: int = 0
    ) -> NotificationPage:
        mine = self._mine(college_id, user_id)
        return NotificationPage(
            items=tuple(mine[offset : offset + limit]),
            total=len(mine),
            unread=sum(1 for n in mine if n.read_at is None),
        )

    def unread(self, college_id: CollegeId, user_id: UserId) -> int:
        return sum(1 for n in self._mine(college_id, user_id) if n.read_at is None)

    def mark_read(
        self, college_id: CollegeId, user_id: UserId, notification_id: NotificationId, at: datetime
    ) -> Notification:
        rows = self._of(college_id)
        found = rows.get(notification_id)
        if found is None or found.user_id != user_id:
            raise NotFoundError(f"notification {notification_id}")
        if found.read_at is None:
            found = replace(found, read_at=at)
            rows[found.id] = found
        return found

    def mark_all_read(self, college_id: CollegeId, user_id: UserId, at: datetime) -> int:
        rows = self._of(college_id)
        changed = 0
        for n in list(rows.values()):
            if n.user_id == user_id and n.read_at is None:
                rows[n.id] = replace(n, read_at=at)
                changed += 1
        return changed

    def all(self, college_id: CollegeId) -> list[Notification]:
        """Every notice of the college, oldest first (tests)."""
        return sorted(self._of(college_id).values(), key=lambda n: (n.created_at, str(n.id)))
