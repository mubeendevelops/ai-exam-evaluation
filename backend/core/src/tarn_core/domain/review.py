"""Teacher decisions and the result sheets they produce. College data."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from tarn_core.domain.booklet import check_aware
from tarn_core.domain.common import BlobKey, check_marks
from tarn_core.errors import InvariantError
from tarn_core.ids import (
    AmendmentId,
    AnswerId,
    AnswerScoreId,
    BookletId,
    CollegeId,
    RegionEditId,
    RegionId,
    ResultSheetId,
    ReviewId,
    UserId,
)

MAX_TAGS = 10
MAX_TAG_CHARS = 40
MAX_REMARKS_CHARS = 2000
MAX_REASON_CHARS = 500


@dataclass(frozen=True, slots=True, kw_only=True)
class Review:
    """An approval. Stores both the AI mark and the teacher's mark (rule 7).
    ``ai_mark`` is None when no AI score exists (guidance-only key)."""

    id: ReviewId
    college_id: CollegeId
    answer_id: AnswerId
    answer_score_id: AnswerScoreId | None
    ai_mark: Decimal | None
    teacher_mark: Decimal
    reviewer: UserId
    reviewed_at: datetime
    tags: tuple[str, ...] = ()
    remarks: str = ""

    def __post_init__(self) -> None:
        if (self.ai_mark is None) != (self.answer_score_id is None):
            raise InvariantError("ai_mark and answer_score_id go together")
        if self.ai_mark is not None and not self.ai_mark.is_finite():
            raise InvariantError("ai_mark must be finite")
        if not self.teacher_mark.is_finite():
            raise InvariantError("teacher_mark must be finite")
        if len(self.tags) > MAX_TAGS:
            raise InvariantError(f"at most {MAX_TAGS} tags")
        for tag in self.tags:
            if not tag.strip() or len(tag) > MAX_TAG_CHARS or tag != tag.strip():
                raise InvariantError(f"a tag has 1 to {MAX_TAG_CHARS} characters, trimmed")
        if len(set(self.tags)) != len(self.tags):
            raise InvariantError("a tag appears twice")
        if len(self.remarks) > MAX_REMARKS_CHARS:
            raise InvariantError(f"remarks hold at most {MAX_REMARKS_CHARS} characters")
        check_aware("reviewed_at", self.reviewed_at)

    @property
    def overridden(self) -> bool:
        return self.ai_mark is not None and self.ai_mark != self.teacher_mark


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultLine:
    """One question slot on the sheet. Dropped answers stay visible as "not counted" (D9)."""

    section_label: str
    slot_label: str
    mark: Decimal | None
    counted: bool
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResultSheet:
    """Issued on booklet approval. Amendments issue version n+1; earlier versions stay valid."""

    id: ResultSheetId
    college_id: CollegeId
    booklet_id: BookletId
    version: int
    lines: tuple[ResultLine, ...]
    total: Decimal
    max_marks: Decimal
    issued_by: UserId
    issued_at: datetime
    pdf: BlobKey | None = None
    note: str = ""
    """What changed since the previous version: the amended questions and the reasons
    given (empty on version 1)."""

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantError("result sheet version starts at 1")
        check_marks("max marks", self.max_marks, allow_zero=False)
        counted = sum(
            (ln.mark for ln in self.lines if ln.counted and ln.mark is not None), Decimal(0)
        )
        if counted != self.total:
            raise InvariantError(f"total {self.total} != sum of counted lines {counted}")
        if self.pdf is not None and self.pdf.college_id != self.college_id:
            raise InvariantError("result PDF must live under its own college's prefix")
        check_aware("issued_at", self.issued_at)

    @property
    def supersedes(self) -> int | None:
        return self.version - 1 if self.version > 1 else None


@dataclass(frozen=True, slots=True, kw_only=True)
class BookletLock:
    """One teacher holds a booklet open (design.md "Concurrency"). Every write by the holder
    pushes ``expires_at`` on; after it passes, anyone may take the booklet over."""

    college_id: CollegeId
    booklet_id: BookletId
    holder: UserId
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        check_aware("acquired_at", self.acquired_at)
        check_aware("expires_at", self.expires_at)
        if self.expires_at <= self.acquired_at:
            raise InvariantError("a lock expires after it is taken")

    def live(self, now: datetime) -> bool:
        return now < self.expires_at


class AmendmentOutcome(StrEnum):
    APPROVED = "approved"
    """The changed answer was approved: it goes on the next result sheet."""
    WITHDRAWN = "withdrawn"
    """The teacher withdrew the draft: the previous approval stands."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Amendment:
    """An approved answer reopened after its booklet was approved: a draft until the changed
    answer is approved (or the draft withdrawn). The reason is optional (design decision 7)."""

    id: AmendmentId
    college_id: CollegeId
    booklet_id: BookletId
    answer_id: AnswerId
    base_review: ReviewId
    """The approval being amended (restored if the draft is withdrawn)."""
    opened_by: UserId
    opened_at: datetime
    reason: str = ""
    closed_by: UserId | None = None
    closed_at: datetime | None = None
    outcome: AmendmentOutcome | None = None
    sheet_version: int | None = None
    """The result sheet version that carries the amended answer."""

    def __post_init__(self) -> None:
        check_aware("opened_at", self.opened_at)
        if len(self.reason) > MAX_REASON_CHARS:
            raise InvariantError(f"a reason holds at most {MAX_REASON_CHARS} characters")
        closed = (self.closed_by, self.closed_at, self.outcome)
        if any(v is None for v in closed) and any(v is not None for v in closed):
            raise InvariantError("a closed amendment names who closed it, when and how")
        if self.closed_at is not None:
            check_aware("closed_at", self.closed_at)
        if self.sheet_version is not None and self.outcome is not AmendmentOutcome.APPROVED:
            raise InvariantError("only an approved amendment goes on a result sheet")

    @property
    def open(self) -> bool:
        return self.outcome is None


@dataclass(frozen=True, slots=True, kw_only=True)
class RegionEdit:
    """A teacher's correction of one OCR line: the text and struck-out mark before and after.
    Kept in college data (deleted with the booklet), never in the audit log (D112)."""

    id: RegionEditId
    college_id: CollegeId
    booklet_id: BookletId
    region_id: RegionId
    actor: UserId
    at: datetime
    before_text: str | None
    after_text: str | None
    before_struck_out: bool
    after_struck_out: bool

    def __post_init__(self) -> None:
        check_aware("edit time", self.at)
        if self.before_text == self.after_text and self.before_struck_out == self.after_struck_out:
            raise InvariantError("an edit changes the text or the struck-out mark")
