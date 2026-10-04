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
from tarn_core.domain.ocr import ContentClass, ReadingScore
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
    """States from design.md "Workflow engine"; transitions after page cleaning arrive in P15.

    P9 moves a booklet UPLOADED → PROCESSING → PAGES_READY or NEEDS_RETAKE (a teacher's "use
    anyway" on every flagged page moves NEEDS_RETAKE → PAGES_READY), or → FAILED when the file
    cannot be read. PAGES_READY is where OCR (P10) takes over: PAGES_READY → READING →
    TEXT_READY (where segmentation, P12, takes over)."""

    UPLOADED = "uploaded"
    PROCESSING = "processing"
    NEEDS_RETAKE = "needs_retake"
    PAGES_READY = "pages_ready"
    READING = "reading"
    TEXT_READY = "text_ready"
    FAILED = "failed"
    SCORED = "scored"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    AMENDMENT_IN_PROGRESS = "amendment_in_progress"


def check_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise InvariantError(f"{name} must be timezone-aware")


# Statuses in which a booklet still counts against its teacher's queue limit: waiting for, or
# in, the machine stages (page cleaning, then OCR). NEEDS_RETAKE waits for the teacher.
WAITING_STATUSES = frozenset(
    {
        BookletStatus.UPLOADED,
        BookletStatus.PROCESSING,
        BookletStatus.PAGES_READY,
        BookletStatus.READING,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFile:
    """One file the teacher uploaded for a booklet, as stored (never its name: file names can
    carry personal data)."""

    key: BlobKey
    media_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        if self.media_type not in SOURCE_MEDIA_TYPES:
            raise InvariantError(f"unsupported source type {self.media_type!r}")
        if self.size_bytes < 1:
            raise InvariantError("a source file is not empty")


SOURCE_MEDIA_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png"})


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
    sources: tuple[SourceFile, ...] = ()
    """The uploaded files, in upload order (page order = upload order, P9)."""
    failure_reason: str | None = None
    """Content-free reason code for FAILED: ``unreadable_file`` or ``too_many_pages``."""

    def __post_init__(self) -> None:
        if self.blueprint.kind is not ContentKind.BLUEPRINT:
            raise InvariantError("a booklet must point at a blueprint")
        if (self.status is BookletStatus.FAILED) != (self.failure_reason is not None):
            raise InvariantError("a failure reason goes with the failed status, and only with it")
        if len(self.file_sha256) != 64 or set(self.file_sha256) - set("0123456789abcdef"):
            raise InvariantError("file_sha256 must be 64 lower-case hex characters")
        check_aware("uploaded_at", self.uploaded_at)
        if self.version < 1:
            raise InvariantError("booklet version starts at 1")


class RetakeReason(StrEnum):
    """Why the quality gate asks for a retake of a page (design.md "Reliability")."""

    BLURRY = "blurry"
    GLARE = "glare"
    LOW_RESOLUTION = "low_resolution"
    NO_PAGE_FOUND = "no_page_found"


@dataclass(frozen=True, slots=True, kw_only=True)
class PageMetrics:
    """What the page cleaner measured and did for one page."""

    sharpness: float | None
    """Edge strength of the handwriting at a fixed scale (higher = sharper); None when the page
    has too little writing to judge."""
    glare_share: float
    """Share of the page lost to blown-out highlights, 0..1."""
    page_found: bool
    """False when no page could be told apart from its surroundings (and no content found)."""
    page_area_share: float
    """Share of the photo the page covered before cropping, 0..1."""
    source_width: int
    source_height: int
    rotation_degrees: int
    """Clockwise quarter turns applied to make the text upright: 0, 90, 180 or 270."""
    rotation_guessed: bool
    """The direction of that quarter turn is a default, not a finding (OCR decides in P10)."""
    skew_degrees: float
    """Small rotation applied after that to level the text lines."""
    cropped: bool
    perspective_corrected: bool
    neighbour_removed: bool
    """A neighbouring page in the shot (a notebook spread) was cut away."""

    def __post_init__(self) -> None:
        if self.rotation_degrees not in (0, 90, 180, 270):
            raise InvariantError("rotation must be 0, 90, 180 or 270 degrees")
        check_unit_interval("glare share", self.glare_share)
        check_unit_interval("page area share", self.page_area_share)
        if self.source_width <= 0 or self.source_height <= 0:
            raise InvariantError("source size must be positive")


@dataclass(frozen=True, slots=True, kw_only=True)
class Page:
    """One page of a booklet. Before cleaning ``image`` is the original; afterwards the cleaned
    page (``original`` always keeps what was uploaded)."""

    id: PageId
    college_id: CollegeId
    booklet_id: BookletId
    index: int
    image: BlobKey
    width: int
    height: int
    original: BlobKey | None = None
    cleaned: bool = True
    metrics: PageMetrics | None = None
    retake_reasons: tuple[RetakeReason, ...] = ()
    use_anyway: bool = False
    """The teacher chose to go on with this page although the gate flagged it."""
    text_read: bool = False
    """OCR stage marker (P10): the page's regions and readings are stored."""
    needs_text: bool = False
    """Every OCR engine failed on this page: the teacher types it or retakes it."""
    ocr_failures: tuple[str, ...] = ()
    """Engines that failed on this page, as ``engine:reason`` (reason: timeout or error)."""

    def __post_init__(self) -> None:
        if self.index < 0 or self.width <= 0 or self.height <= 0:
            raise InvariantError("page index must be >= 0 and size positive")
        for key in (self.image, self.original):
            if key is not None and key.college_id != self.college_id:
                raise InvariantError("page image must live under its own college's prefix")
        if self.use_anyway and not self.retake_reasons:
            raise InvariantError("only a flagged page can be used anyway")
        if self.needs_text and not self.text_read:
            raise InvariantError("only a page that went through OCR can need text")
        for failure in self.ocr_failures:
            engine, _, reason = failure.partition(":")
            if not engine or reason not in ("timeout", "error"):
                raise InvariantError(f"malformed OCR failure {failure!r}")

    @property
    def needs_retake(self) -> bool:
        return bool(self.retake_reasons) and not self.use_anyway


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
    """A detected area of a page. Text lines keep every engine's reading (best-of-N), the
    selector's score of each, the index of the chosen one, and the teacher's correction if any.

    A table is one TABLE region; its cells are TEXT_LINE regions with ``parent_id`` pointing at
    it and their ``row``/``col`` (from 0)."""

    id: RegionId
    college_id: CollegeId
    page_id: PageId
    kind: RegionKind
    box: Box
    readings: tuple[LineReading, ...] = ()
    chosen: int | None = None
    teacher_text: str | None = None
    content_class: ContentClass | None = None
    scores: tuple[ReadingScore, ...] = ()
    """One per reading, same order (empty for regions that were not read)."""
    line_score: float | None = None
    """The winner's score over the best score the line could have had (0..1)."""
    flagged: bool = False
    """The best reading is below the line threshold: highlighted for the teacher."""
    calibrations: tuple[ContentRef, ...] = ()
    """The calibration versions the selector used (rule 11)."""
    parent_id: RegionId | None = None
    row: int | None = None
    col: int | None = None

    def __post_init__(self) -> None:
        if self.chosen is not None and not 0 <= self.chosen < len(self.readings):
            raise InvariantError("chosen reading index out of range")
        if self.scores and len(self.scores) != len(self.readings):
            raise InvariantError("one selector score per reading")
        if self.chosen is not None and self.scores and not self.scores[self.chosen].competing:
            raise InvariantError("the chosen reading must be one that competed")
        if self.line_score is not None:
            check_unit_interval("line score", self.line_score)
        if (self.row is None) != (self.col is None):
            raise InvariantError("a table cell has both a row and a column")
        if self.row is not None and (self.row < 0 or self.col is None or self.col < 0):
            raise InvariantError("row and column count from 0")
        if self.row is not None and self.parent_id is None:
            raise InvariantError("a table cell points at its table")
        if self.parent_id == self.id:
            raise InvariantError("a region cannot contain itself")
        for ref in self.calibrations:
            if ref.kind is not ContentKind.OCR_CALIBRATION:
                raise InvariantError("region calibrations must be OCR calibrations")

    @property
    def read_by(self) -> tuple[str, ...]:
        """The engines that read this region, in reading order."""
        return tuple(r.engine.name for r in self.readings)

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
