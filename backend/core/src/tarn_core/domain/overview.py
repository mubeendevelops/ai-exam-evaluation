"""What the admin sees of the college's work (P29, D163, D167): counts of SMEs, units and
booklets, never a mark and never a speed. Plain data; the figures are computed by the college
repositories (SQL aggregates on PostgreSQL), the words are the screens'.

Credit follows the unit's *current* owner (D163): every per-SME figure groups the units by
``owner_id`` as it stands now, so a transfer moves the unit's booklets, progress and overdue
state to the new owner and leaves the old one nothing."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Self

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.unit import UnitState
from tarn_core.ids import BlueprintId, SubjectId, UnitId, UserId

HEAVY_LOAD_PENDING = 100
"""An SME with more pending booklets than this carries a "Heavy load" flag (the mock's rule)."""

ACTIVITY_LIMIT = 10
"""How many recent events the dashboard lists."""


class SmeStatus(StrEnum):
    """D163: *Idle* is computed (an active SME who owns no open unit); *Suspended* is the
    admin's switch; every other live SME is *Active*."""

    ACTIVE = "active"
    IDLE = "idle"
    SUSPENDED = "suspended"


def sme_status(*, active: bool, open_units: int) -> SmeStatus:
    """The status of a live SME (a removed account is not listed at all): ``active`` is the
    account's switch, ``open_units`` how many open units it owns."""
    if not active:
        return SmeStatus.SUSPENDED
    return SmeStatus.IDLE if open_units == 0 else SmeStatus.ACTIVE


class Stage(StrEnum):
    """Where a booklet is, in the admin's words."""

    PROCESSING = "processing"
    """Uploaded, cleaned, read, segmented or being scored: the machine's steps."""
    READY = "ready"
    """Scored and waiting for the SME to open it."""
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    """Approved by the SME, including a booklet reopened for an amendment (its sheet stays
    valid, D108)."""
    ATTENTION = "attention"
    """Needs a retake or failed: the admin's to act on (D189). Not one of the four stages of
    the mock, but without it the counts would not add up."""


_S = BookletStatus
STAGE_OF: Mapping[BookletStatus, Stage] = {
    _S.UPLOADED: Stage.PROCESSING,
    _S.PROCESSING: Stage.PROCESSING,
    _S.PAGES_READY: Stage.PROCESSING,
    _S.READING: Stage.PROCESSING,
    _S.TEXT_READY: Stage.PROCESSING,
    _S.SEGMENTED: Stage.PROCESSING,
    _S.SCORED: Stage.READY,
    _S.IN_REVIEW: Stage.IN_REVIEW,
    _S.APPROVED: Stage.APPROVED,
    _S.AMENDMENT_IN_PROGRESS: Stage.APPROVED,
    _S.APPROVED_AMENDED: Stage.APPROVED,
    _S.NEEDS_RETAKE: Stage.ATTENTION,
    _S.FAILED: Stage.ATTENTION,
}
"""Every booklet status in exactly one stage; the SQL counts are built from this table."""


def statuses_of(stage: Stage) -> frozenset[BookletStatus]:
    return frozenset(s for s, st in STAGE_OF.items() if st is stage)


@dataclass(frozen=True, slots=True, kw_only=True)
class StageCounts:
    processing: int = 0
    ready: int = 0
    in_review: int = 0
    approved: int = 0
    attention: int = 0

    @property
    def total(self) -> int:
        return self.processing + self.ready + self.in_review + self.approved + self.attention

    @property
    def pending(self) -> int:
        """Not approved yet."""
        return self.total - self.approved

    @classmethod
    def of(cls, statuses: Iterable[BookletStatus]) -> Self:
        found = [STAGE_OF[s] for s in statuses]
        return cls(
            processing=found.count(Stage.PROCESSING),
            ready=found.count(Stage.READY),
            in_review=found.count(Stage.IN_REVIEW),
            approved=found.count(Stage.APPROVED),
            attention=found.count(Stage.ATTENTION),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SmeCounts:
    active: int = 0
    idle: int = 0
    suspended: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitCounts:
    open: int = 0
    submitted: int = 0
    frozen: int = 0
    overdue: int = 0
    """Open units whose due date has passed."""


def is_overdue(state: UnitState, due_date: date, today: date) -> bool:
    """Only an open unit can be late: a submitted one is in the admin's hands."""
    return state is UnitState.OPEN and due_date < today


@dataclass(frozen=True, slots=True, kw_only=True)
class SmeProgress:
    """One SME's work, as units stand now (D163). ``booklets`` and ``approved`` cover every
    unit owned; ``pending`` only the open ones (a submitted unit is handed in, a frozen one
    is closed), so a "Heavy load" flag is about work still to do."""

    user_id: UserId
    display_name: str
    status: SmeStatus
    units_owned: int
    open_units: int
    overdue_units: int
    booklets: int
    approved: int
    pending: int
    last_activity: datetime | None
    """The SME's latest audited action other than signing in; None if there is none."""

    @property
    def heavy_load(self) -> bool:
        return self.pending > HEAVY_LOAD_PENDING


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitProgress:
    unit_id: UnitId
    code: str
    year: str
    paper_id: BlueprintId
    subject_id: SubjectId
    owner_id: UserId
    due_date: date
    state: UnitState
    overdue: bool
    stages: StageCounts


FEED_ACTIONS: frozenset[AuditAction] = frozenset(
    {
        AuditAction.UNIT_CREATED,
        AuditAction.UNIT_UPLOADED,
        AuditAction.UNIT_TRANSFERRED,
        AuditAction.UNIT_SUBMITTED,
        AuditAction.UNIT_RETURNED,
        AuditAction.UNIT_FROZEN,
        AuditAction.BOOKLET_APPROVED,
        AuditAction.BOOKLET_REPLACED,
        AuditAction.DELEGATION_REQUESTED,
        AuditAction.DELEGATION_DECLINED,
        AuditAction.DELEGATION_WITHDRAWN,
        AuditAction.ACCOUNT_CREATED,
        AuditAction.ACCOUNT_DISABLED,
        AuditAction.ACCOUNT_ENABLED,
        AuditAction.ACCOUNT_REMOVED,
        AuditAction.SUBJECT_GRANTED,
        AuditAction.SUBJECT_REVOKED,
        AuditAction.ROSTER_IMPORTED,
    }
)
"""The audit actions the dashboard lists: what happens to units, people and subjects. The
feed shows the action, who did it, when and the unit's code; never an event's before/after
(they may hold roll numbers or reasons)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ActivityItem:
    at: datetime
    action: AuditAction
    actor_name: str | None
    unit_code: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class QueueDepth:
    """The college's jobs waiting for the worker, and the one it is running."""

    queued: int = 0
    running: int = 0
