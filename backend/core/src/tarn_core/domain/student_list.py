"""The SME's student list of a unit (P32, D217): each booklet's row status, how many of its
answers are approved of those attempted, and the filter and page the list was asked for. The
store filters, pages and counts (one query); the core only names the rules."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from tarn_core.domain.booklet import Booklet, BookletStatus
from tarn_core.errors import InvariantError
from tarn_core.ids import BookletId

MAX_ROW_LIMIT = 100
MAX_ROW_TEXT = 80


class RowStatus(StrEnum):
    """The badge of a student's booklet in the list."""

    PROCESSING = "processing"
    NEEDS_RETAKE = "needs_retake"
    FAILED = "failed"
    READY = "ready"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"


_S = BookletStatus
ROW_STATUS_OF: Mapping[BookletStatus, RowStatus] = {
    _S.UPLOADED: RowStatus.PROCESSING,
    _S.PROCESSING: RowStatus.PROCESSING,
    _S.PAGES_READY: RowStatus.PROCESSING,
    _S.READING: RowStatus.PROCESSING,
    _S.TEXT_READY: RowStatus.PROCESSING,
    _S.SEGMENTED: RowStatus.PROCESSING,
    _S.NEEDS_RETAKE: RowStatus.NEEDS_RETAKE,
    _S.FAILED: RowStatus.FAILED,
    _S.SCORED: RowStatus.READY,
    _S.IN_REVIEW: RowStatus.IN_REVIEW,
    # A result sheet stands (an amendment draft does not undo it), as in the unit's counts.
    _S.APPROVED: RowStatus.COMPLETED,
    _S.AMENDMENT_IN_PROGRESS: RowStatus.COMPLETED,
    _S.APPROVED_AMENDED: RowStatus.COMPLETED,
}


def statuses_of(rows: frozenset[RowStatus]) -> frozenset[BookletStatus]:
    """The booklet statuses behind a set of row statuses."""
    return frozenset(s for s, r in ROW_STATUS_OF.items() if r in rows)


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerTally:
    """``attempted``: answers with text (they need a decision); ``approved``: those approved."""

    attempted: int = 0
    approved: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.approved <= self.attempted:
            raise InvariantError("approved answers are some of the attempted ones")


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitRowQuery:
    """``text`` matches a roll number from its start or a name anywhere in it (any case);
    ``rows`` keeps only those row statuses (empty: all); ``limit`` None: every row."""

    text: str = ""
    rows: frozenset[RowStatus] = frozenset()
    offset: int = 0
    limit: int | None = None

    def __post_init__(self) -> None:
        if len(self.text) > MAX_ROW_TEXT:
            raise InvariantError(f"the filter is at most {MAX_ROW_TEXT} characters")
        if self.offset < 0:
            raise InvariantError("offset is not negative")
        if self.limit is not None and not 1 <= self.limit <= MAX_ROW_LIMIT:
            raise InvariantError(f"limit is 1 to {MAX_ROW_LIMIT}")

    @property
    def needle(self) -> str:
        return self.text.strip().lower()


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitRows:
    """One page of a unit's booklets by roll number, their tallies, and how many match."""

    booklets: Sequence[Booklet]
    tallies: Mapping[BookletId, AnswerTally] = field(default_factory=dict)
    matching: int = 0

    def __post_init__(self) -> None:
        if self.matching < len(self.booklets):
            raise InvariantError("a page holds no more rows than match")

    def tally(self, booklet_id: BookletId) -> AnswerTally:
        return self.tallies.get(booklet_id, AnswerTally())
