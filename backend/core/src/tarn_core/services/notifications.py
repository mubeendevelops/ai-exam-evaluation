"""SME notifications (P37, D227, D241).

``Notices`` writes a notice in the caller's transaction, so a notice exists exactly when its
event committed: a unit assigned to you (created or transferred to you), transferred away,
returned (with the admin's note), your reassignment request done or declined. One notice per
person per event. ``NotificationService`` is the SME's inbox: list, unread count, mark read.
Payloads carry codes, dates and ids only (``tarn_core.domain.notifications``)."""

from collections.abc import Mapping
from datetime import UTC

from tarn_core.domain.notifications import (
    PAGE_MAX,
    Notification,
    NotificationKind,
    NotificationPage,
)
from tarn_core.domain.personas import Capability
from tarn_core.domain.unit import DelegationRequest, Unit
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, NotificationId, UserId
from tarn_core.ports.repositories import NotificationRepository, UserRepository
from tarn_core.services._support import Runtime
from tarn_core.services.access import require


class Notices:
    """The writer the unit services share."""

    def __init__(self, repository: NotificationRepository, runtime: Runtime) -> None:
        self._repo = repository
        self._rt = runtime

    def _add(
        self,
        unit: Unit,
        user_id: UserId,
        kind: NotificationKind,
        extra: Mapping[str, str] | None = None,
    ) -> None:
        now = self._rt.clock.now()
        day = now.astimezone(UTC).date()
        self._repo.add(
            unit.college_id,
            Notification(
                id=self._rt.new_id(NotificationId),
                college_id=unit.college_id,
                user_id=user_id,
                kind=kind,
                unit_id=unit.id,
                day=day,
                created_at=now,
                payload={"unit_code": unit.code, **(extra or {})},
            ),
        )

    def assigned(self, unit: Unit) -> None:
        """``unit.owner_id`` has just received the unit (created for them or moved to them)."""
        self._add(
            unit, unit.owner_id, NotificationKind.ASSIGNED, {"due_date": unit.due_date.isoformat()}
        )

    def moved_away(self, unit: Unit, former: UserId, request: DelegationRequest | None) -> None:
        """The unit left ``former``: ``request_done`` when it closed their own request, else
        ``transferred_away``."""
        if request is not None and request.requested_by == former:
            self._add(unit, former, NotificationKind.REQUEST_DONE, {"request_id": str(request.id)})
        else:
            self._add(unit, former, NotificationKind.TRANSFERRED_AWAY)

    def returned(self, unit: Unit, note: str) -> None:
        self._add(unit, unit.owner_id, NotificationKind.RETURNED, {"note": note})

    def declined(self, unit: Unit, request: DelegationRequest) -> None:
        """No note: the SME reads it in their request list."""
        self._add(
            unit,
            request.requested_by,
            NotificationKind.REQUEST_DECLINED,
            {"request_id": str(request.id)},
        )


class NotificationService:
    """The SME's own notices (``view_own_work``): never another user's."""

    def __init__(
        self, *, notifications: NotificationRepository, users: UserRepository, runtime: Runtime
    ) -> None:
        self._repo = notifications
        self._users = users
        self._rt = runtime

    def _me(self, college_id: CollegeId, actor_id: UserId) -> UserId:
        return require(self._users.get(college_id, actor_id), Capability.VIEW_OWN_WORK).id

    def page(
        self, college_id: CollegeId, actor_id: UserId, *, limit: int = 20, offset: int = 0
    ) -> NotificationPage:
        if not 1 <= limit <= PAGE_MAX or offset < 0:
            raise InvariantError(f"limit is 1 to {PAGE_MAX}, offset not negative")
        return self._repo.page(
            college_id, self._me(college_id, actor_id), limit=limit, offset=offset
        )

    def unread(self, college_id: CollegeId, actor_id: UserId) -> int:
        return self._repo.unread(college_id, self._me(college_id, actor_id))

    def mark_read(
        self, college_id: CollegeId, actor_id: UserId, notification_id: NotificationId
    ) -> Notification:
        return self._repo.mark_read(
            college_id, self._me(college_id, actor_id), notification_id, self._rt.clock.now()
        )

    def mark_all_read(self, college_id: CollegeId, actor_id: UserId) -> int:
        return self._repo.mark_all_read(
            college_id, self._me(college_id, actor_id), self._rt.clock.now()
        )


__all__ = ["Notices", "NotificationService"]
