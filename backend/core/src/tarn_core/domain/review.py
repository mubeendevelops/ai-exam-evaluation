"""Teacher decisions and the result sheets they produce. College data."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tarn_core.domain.booklet import check_aware
from tarn_core.domain.common import BlobKey, check_marks
from tarn_core.errors import InvariantError
from tarn_core.ids import (
    AnswerId,
    AnswerScoreId,
    BookletId,
    CollegeId,
    ResultSheetId,
    ReviewId,
    UserId,
)


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
