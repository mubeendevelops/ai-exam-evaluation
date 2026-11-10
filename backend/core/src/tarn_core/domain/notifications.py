"""In-app notifications for SMEs (P37, D227, D241). College data: one row per notice, for one
user, about one unit. A notice carries ids and codes only (the unit's code, its due date, a
request id); the one piece of free text is the admin's note on a returned unit. Never student
data, never marks.

Event notices (assigned, transferred away, returned, request done or declined) are written in
the same transaction as their event. Due notices come from the daily sweep (``notify.due``) and
have a key (unit, kind, day): a second sweep on the same day adds nothing."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType

from tarn_core.domain.unit import REASON_MAX
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, NotificationId, UnitId, UserId


class NotificationKind(StrEnum):
    ASSIGNED = "assigned"
    """A unit is now yours: created for you, or transferred to you."""
    TRANSFERRED_AWAY = "transferred_away"
    """Your unit was moved to another SME (you did not ask for it)."""
    RETURNED = "returned"
    """Your admin returned your submitted unit, with a note."""
    REQUEST_DONE = "request_done"
    """Your reassignment request was done: the unit moved to another SME."""
    REQUEST_DECLINED = "request_declined"
    """Your reassignment request was declined (the note is in your request list)."""
    DUE_SOON = "due_soon"
    """An open unit of yours is due within three days (once per unit and due date)."""
    OVERDUE = "overdue"
    """An open unit of yours is past its due date (once a day while it stays open)."""


DUE_KINDS = frozenset({NotificationKind.DUE_SOON, NotificationKind.OVERDUE})
"""The kinds the sweep writes; their (unit, kind, day) is unique."""

PAYLOAD_KEYS = frozenset({"unit_code", "due_date", "request_id", "note"})
"""Everything a notice may carry: codes, dates and ids, and the admin's return note."""

PAGE_MAX = 50


@dataclass(frozen=True, slots=True, kw_only=True)
class Notification:
    id: NotificationId
    college_id: CollegeId
    user_id: UserId
    kind: NotificationKind
    unit_id: UnitId
    day: date
    """The day the notice is about: the event's UTC date; for ``due_soon`` the due date (so it
    is written once per due date), for ``overdue`` the sweep's day."""
    created_at: datetime
    payload: Mapping[str, str]
    read_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or (
            self.read_at is not None and self.read_at.tzinfo is None
        ):
            raise InvariantError("notification times must be timezone-aware")
        unknown = set(self.payload) - PAYLOAD_KEYS
        if unknown:
            raise InvariantError("a notification carries codes, dates and ids only")
        if any(not isinstance(v, str) for v in self.payload.values()):
            raise InvariantError("notification payload values are text")
        note = self.payload.get("note")
        if note is not None:
            if self.kind is not NotificationKind.RETURNED:
                raise InvariantError("only a returned unit's notice carries a note")
            if not 1 <= len(note) <= REASON_MAX:
                raise InvariantError(f"the note is 1 to {REASON_MAX} characters")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    @property
    def read(self) -> bool:
        return self.read_at is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class NotificationPage:
    items: tuple[Notification, ...]
    total: int
    unread: int


__all__ = [
    "DUE_KINDS",
    "PAGE_MAX",
    "PAYLOAD_KEYS",
    "Notification",
    "NotificationKind",
    "NotificationPage",
]
