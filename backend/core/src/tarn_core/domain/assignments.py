"""The SME's own figures on My Assignments (P31, D216, provisional) and later My Reports (P36).

Counted per unit, over the units the SME owns *now* (credit follows the current owner, D163).
The store counts (SQL aggregates on PostgreSQL); the arithmetic that turns counts into what
the page shows lives here: the academic year, when a unit reached its owner, its turnaround,
the average in days and the completion rate."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from tarn_core.domain.unit import UnitSubmission, UnitTransfer
from tarn_core.errors import InvariantError
from tarn_core.ids import UserId

ACADEMIC_YEAR_START = (7, 1)
"""(month, day) the academic year starts on: 1 July (D216). A college setting may replace it
with the Provisioner."""

SECONDS_PER_DAY = Decimal(86_400)
_TENTH = Decimal("0.1")


@dataclass(frozen=True, slots=True, kw_only=True)
class AcademicYear:
    """``start`` included, ``end`` excluded (the next year's start)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise InvariantError("an academic year ends after it starts")

    @property
    def label(self) -> str:
        """``2026-27``"""
        return f"{self.start.year}-{self.end.year % 100:02d}"

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day < self.end


def academic_year_of(day: date) -> AcademicYear:
    """The academic year ``day`` falls in. A unit belongs to the year of its due date."""
    month, first = ACADEMIC_YEAR_START
    start_year = day.year if (day.month, day.day) >= (month, first) else day.year - 1
    return AcademicYear(
        start=date(start_year, month, first), end=date(start_year + 1, month, first)
    )


def received_at(
    created_at: datetime, owner_id: UserId, transfers: Iterable[UnitTransfer]
) -> datetime:
    """When the current owner got the unit: the last transfer to them, else its creation."""
    to_owner = [t.at for t in transfers if t.to_owner == owner_id]
    return max(to_owner, default=created_at)


def latest_submission(submissions: Iterable[UnitSubmission]) -> datetime | None:
    """The last time the unit was handed in (a returned unit counts its resubmission)."""
    return max((s.submitted_at for s in submissions), default=None)


def turnaround(received: datetime, submitted: datetime | None) -> timedelta | None:
    """From receipt to the latest submission; None without one (or one before the receipt,
    which a closed unit cannot have: a transfer needs an open unit)."""
    if submitted is None or submitted < received:
        return None
    return submitted - received


def average_days(total_seconds: Decimal, count: int) -> Decimal | None:
    """The mean of ``count`` turnarounds summing to ``total_seconds``, in days to one decimal
    (half up); None when there are none."""
    if count < 0 or total_seconds < 0:
        raise InvariantError("turnaround figures cannot be negative")
    if count == 0:
        return None
    return (total_seconds / SECONDS_PER_DAY / count).quantize(_TENTH, rounding=ROUND_HALF_UP)


def completion_percent(completed: int, total: int) -> Decimal | None:
    """Completed units as a percent of every unit owned, to one decimal; None with no units."""
    if total < 0 or not 0 <= completed <= total:
        raise InvariantError("completed units lie between 0 and the units owned")
    if total == 0:
        return None
    return (Decimal(completed) * 100 / total).quantize(_TENTH, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True, kw_only=True)
class AssignmentCounts:
    """What the store counts over one SME's current units."""

    lifetime: int = 0
    current_year: int = 0
    """Units whose due date falls in the academic year asked for."""
    open: int = 0
    submitted: int = 0
    frozen: int = 0
    turnaround_units: int = 0
    """Submitted and frozen units with a turnaround."""
    turnaround_seconds: Decimal = Decimal(0)
    """Their turnarounds summed."""

    def __post_init__(self) -> None:
        counts = (
            self.lifetime,
            self.current_year,
            self.open,
            self.submitted,
            self.frozen,
            self.turnaround_units,
        )
        if any(c < 0 for c in counts) or self.turnaround_seconds < 0:
            raise InvariantError("counts cannot be negative")
        if self.open + self.submitted + self.frozen != self.lifetime:
            raise InvariantError("every unit is open, submitted or frozen")
        if self.current_year > self.lifetime:
            raise InvariantError("the year's units are some of the lifetime's")
        if self.turnaround_units > self.submitted + self.frozen:
            raise InvariantError("only submitted and frozen units have a turnaround")


@dataclass(frozen=True, slots=True, kw_only=True)
class AssignmentSummary:
    year: AcademicYear
    counts: AssignmentCounts

    @property
    def completed(self) -> int:
        """Submitted or frozen (D216)."""
        return self.counts.submitted + self.counts.frozen

    @property
    def active(self) -> int:
        return self.counts.open

    @property
    def completion_rate(self) -> Decimal | None:
        return completion_percent(self.completed, self.counts.lifetime)

    @property
    def average_turnaround_days(self) -> Decimal | None:
        return average_days(self.counts.turnaround_seconds, self.counts.turnaround_units)
