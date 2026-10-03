"""A student's booklet and everything extracted from it. All college data."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tarn_core.domain.common import (
    BlobKey,
    Box,
    ContentKind,
    ContentRef,
    EngineRef,
    check_text,
    check_unit_interval,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import (
    AnswerId,
    BookletId,
    CollegeId,
    PageId,
    RegionId,
    SegmentId,
    StudentId,
    UserId,
)


class BookletStatus(StrEnum):
    """States from design.md "Workflow engine"; transitions arrive in P15."""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    NEEDS_RETAKE = "needs_retake"
    SCORED = "scored"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    AMENDMENT_IN_PROGRESS = "amendment_in_progress"


def check_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise InvariantError(f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class Booklet:
    id: BookletId
    college_id: CollegeId
    student_id: StudentId
    blueprint: ContentRef
    file_sha256: str
    uploaded_by: UserId
    uploaded_at: datetime
    status: BookletStatus = BookletStatus.UPLOADED
    version: int = 1  # optimistic-lock counter: every write carries it

    def __post_init__(self) -> None:
        if self.blueprint.kind is not ContentKind.BLUEPRINT:
            raise InvariantError("a booklet must point at a blueprint")
        if len(self.file_sha256) != 64 or set(self.file_sha256) - set("0123456789abcdef"):
            raise InvariantError("file_sha256 must be 64 lower-case hex characters")
        check_aware("uploaded_at", self.uploaded_at)
        if self.version < 1:
            raise InvariantError("booklet version starts at 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    id: PageId
    college_id: CollegeId
    booklet_id: BookletId
    index: int
    image: BlobKey
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.index < 0 or self.width <= 0 or self.height <= 0:
            raise InvariantError("page index must be >= 0 and size positive")
        if self.image.college_id != self.college_id:
            raise InvariantError("page image must live under its own college's prefix")


class RegionKind(StrEnum):
    TEXT_LINE = "text_line"
    TEXT_BLOCK = "text_block"
    DIAGRAM = "diagram"
    TABLE = "table"
    LABEL = "label"


@dataclass(frozen=True, slots=True, kw_only=True)
class LineReading:
    """One engine's reading of one line (design.md "OCR framework")."""

    engine: EngineRef
    text: str
    box: Box
    confidence: float
    char_confidences: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        check_unit_interval("reading confidence", self.confidence)
        if self.char_confidences is not None:
            if len(self.char_confidences) != len(self.text):
                raise InvariantError("one character confidence per character")
            for c in self.char_confidences:
                check_unit_interval("character confidence", c)


@dataclass(frozen=True, slots=True, kw_only=True)
class Region:
    """A detected area of a page. Text lines keep every engine's reading (best-of-N),
    the index of the chosen one, and the teacher's correction if any."""

    id: RegionId
    college_id: CollegeId
    page_id: PageId
    kind: RegionKind
    box: Box
    readings: tuple[LineReading, ...] = ()
    chosen: int | None = None
    teacher_text: str | None = None

    def __post_init__(self) -> None:
        if self.chosen is not None and not 0 <= self.chosen < len(self.readings):
            raise InvariantError("chosen reading index out of range")

    @property
    def text(self) -> str | None:
        if self.teacher_text is not None:
            return self.teacher_text
        return None if self.chosen is None else self.readings[self.chosen].text


class SegmentSource(StrEnum):
    RULE = "rule"
    SIMILARITY = "similarity"
    TEACHER = "teacher"


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentSpan:
    page_id: PageId
    box: Box


@dataclass(frozen=True, slots=True, kw_only=True)
class Segment:
    """A piece of a booklet assigned to a question slot; ``slot_label`` None = unassigned tray."""

    id: SegmentId
    college_id: CollegeId
    booklet_id: BookletId
    slot_label: str | None
    spans: tuple[SegmentSpan, ...]
    source: SegmentSource
    match_score: float | None = None

    def __post_init__(self) -> None:
        if not self.spans:
            raise InvariantError("a segment covers at least one page area")
        if self.match_score is not None:
            check_unit_interval("match score", self.match_score)


class AnswerStatus(StrEnum):
    SUGGESTED = "suggested"
    SKIPPED = "skipped"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True, kw_only=True)
class Answer:
    """A booklet's answer to one question slot: the unit the teacher approves."""

    id: AnswerId
    college_id: CollegeId
    booklet_id: BookletId
    slot_label: str
    segment_ids: tuple[SegmentId, ...]
    status: AnswerStatus = AnswerStatus.SUGGESTED
    version: int = 1

    def __post_init__(self) -> None:
        check_text("answer slot label", self.slot_label)
        if self.version < 1:
            raise InvariantError("answer version starts at 1")
