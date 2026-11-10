"""The SME's own reports (P36, D226, D239): My Reports.

Counted per unit over the units the SME owns *now* (credit follows the current owner, D163),
like My Assignments (``assignments``). The store counts (SQL aggregates on PostgreSQL); the
arithmetic that turns counts into what the page shows lives here: calendar quarters, the
on-time rate and the averages. No accuracy score, no speed per hour, no marks (D167).

Rules (D239):

- *Turnaround*: as D216, from when the SME received the unit to its latest submission, over
  submitted and frozen units; the year's figure takes the units due in the academic year.
- *On time*: a submitted or frozen unit whose latest submission, as a UTC date, is on or before
  its due date. The rate is those over every submitted or frozen unit with a submission.
- *Per quarter*: calendar quarters in UTC, the last four ending with the one holding today.
  A unit counts once, in the quarter of its latest submission; a booklet counts once, in the
  quarter its first result sheet was issued (a later amendment does not move it).
- *Pending*: booklets not approved in the SME's open units (D209)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from tarn_core.domain.assignments import AcademicYear, average_days
from tarn_core.errors import InvariantError

QUARTERS_SHOWN = 4
"""How many calendar quarters My Reports lists, the current one last."""

_TENTH = Decimal("0.1")


@dataclass(frozen=True, slots=True, order=True)
class Quarter:
    """A calendar quarter: Q1 = January to March."""

    year: int
    number: int

    def __post_init__(self) -> None:
        if not 1 <= self.number <= 4:
            raise InvariantError("a quarter is numbered 1 to 4")
        if not 1 <= self.year <= 9998:
            raise InvariantError("the quarter's year is out of range")

    @property
    def start(self) -> date:
        """First day, included."""
        return date(self.year, 3 * self.number - 2, 1)

    @property
    def end(self) -> date:
        """First day of the next quarter, excluded."""
        return (
            Quarter(self.year, self.number + 1).start
            if self.number < 4
            else date(self.year + 1, 1, 1)
        )

    @property
    def start_at(self) -> datetime:
        """``start`` as midnight UTC: the instant a timestamp is compared to."""
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=UTC)

    @property
    def end_at(self) -> datetime:
        return datetime(self.end.year, self.end.month, self.end.day, tzinfo=UTC)

    @property
    def label(self) -> str:
        """``2026 Q4``"""
        return f"{self.year} Q{self.number}"

    def previous(self) -> "Quarter":
        return Quarter(self.year, self.number - 1) if self.number > 1 else Quarter(self.year - 1, 4)

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and self.start <= day < self.end


def quarter_of(day: date) -> Quarter:
    return Quarter(day.year, (day.month - 1) // 3 + 1)


def last_quarters(today: date, count: int = QUARTERS_SHOWN) -> tuple[Quarter, ...]:
    """The ``count`` calendar quarters ending with the one that holds ``today``, oldest first."""
    if count < 1:
        raise InvariantError("at least one quarter is listed")
    found = [quarter_of(today)]
    while len(found) < count:
        found.append(found[-1].previous())
    return tuple(reversed(found))


def submitted_on_time(submitted_at: datetime, due_date: date) -> bool:
    """The latest submission, as a UTC date, is on or before the due date (D239)."""
    if submitted_at.tzinfo is None:
        raise InvariantError("submitted_at must be timezone-aware")
    return submitted_at.astimezone(UTC).date() <= due_date


def on_time_percent(on_time: int, submitted: int) -> Decimal | None:
    """On-time units as a percent of the units handed in, to one decimal; None with none."""
    if submitted < 0 or not 0 <= on_time <= submitted:
        raise InvariantError("on-time units lie between 0 and the units handed in")
    if submitted == 0:
        return None
    return (Decimal(on_time) * 100 / submitted).quantize(_TENTH, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True, kw_only=True)
class QuarterCounts:
    quarter: Quarter
    units_submitted: int = 0
    booklets_approved: int = 0

    def __post_init__(self) -> None:
        if self.units_submitted < 0 or self.booklets_approved < 0:
            raise InvariantError("counts cannot be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class TurnaroundCounts:
    """Submitted and frozen units that have a turnaround, and their turnarounds summed."""

    units: int = 0
    seconds: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.units < 0 or self.seconds < 0:
            raise InvariantError("counts cannot be negative")

    @property
    def average_days(self) -> Decimal | None:
        return average_days(self.seconds, self.units)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportCounts:
    """What the store counts over one SME's current units."""

    lifetime: TurnaroundCounts
    current_year: TurnaroundCounts
    """The same over units due in the academic year asked for."""
    handed_in: int
    """Submitted and frozen units with a submission."""
    on_time: int
    quarters: Sequence[QuarterCounts]
    """One row per quarter asked for, oldest first."""
    open_units: int
    booklets_pending: int

    def __post_init__(self) -> None:
        if min(self.handed_in, self.on_time, self.open_units, self.booklets_pending) < 0:
            raise InvariantError("counts cannot be negative")
        if self.on_time > self.handed_in:
            raise InvariantError("on-time units are some of the units handed in")
        if self.lifetime.units > self.handed_in:
            raise InvariantError("only units handed in have a turnaround")
        if self.current_year.units > self.lifetime.units:
            raise InvariantError("the year's turnarounds are some of the lifetime's")


@dataclass(frozen=True, slots=True, kw_only=True)
class MyReport:
    year: AcademicYear
    counts: ReportCounts

    @property
    def on_time_rate(self) -> Decimal | None:
        return on_time_percent(self.counts.on_time, self.counts.handed_in)
