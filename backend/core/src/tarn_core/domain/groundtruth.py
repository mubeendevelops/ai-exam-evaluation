"""Ground truth for the OCR benchmark (design.md "Benchmark before launch"): hand-transcribed
lines of real pages, with the kind of writing and how the page was captured.

A ground-truth set is student data (the text is what students wrote): it lives under a local,
git-ignored folder in development and under the college's own data when it comes from the review
UI (P16). Only error rates and counts leave it (CLAUDE.md "samples/")."""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID

from tarn_core.domain.common import Box
from tarn_core.domain.ocr import ContentClass
from tarn_core.errors import InvariantError

SCHEMA_VERSION = 1

_PAGE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_IMAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.(?:jpg|jpeg|png)$")


class CaptureType(StrEnum):
    """How a page was captured (requirements C11, C18: teachers' phones in production)."""

    SCANNING_APP = "scanning_app"
    """A phone scanning app (CamScanner, Adobe Scan): cropped, flattened, often enhanced."""
    CLEAN_SCAN = "clean_scan"
    """A flatbed or office scanner: flat, evenly lit."""
    PHONE_PHOTO = "phone_photo"
    """A plain phone photo: perspective, shadows, curled pages."""


class TruthStatus(StrEnum):
    PREFILLED = "prefilled"
    """The text is an engine's reading, not yet checked by a person: never used as truth."""
    VERIFIED = "verified"
    """A person checked or typed the text and the class: this is the ground truth."""
    IGNORED = "ignored"
    """Not a line of text (a stray mark, a ruling) or unreadable: left out of every measure."""


class TruthOrigin(StrEnum):
    TRANSCRIPTION = "transcription"
    """Typed or confirmed in the transcription tool."""
    TEACHER_CORRECTION = "teacher_correction"
    """A teacher's edit of a reading in the review UI (P16). The class is the OCR's, not a
    person's judgement."""
    PUBLIC_DATASET = "public_dataset"
    """From a public dataset's own transcription."""


@dataclass(frozen=True, slots=True, kw_only=True)
class TruthLine:
    box: Box
    text: str
    content_class: ContentClass
    status: TruthStatus = TruthStatus.PREFILLED
    origin: TruthOrigin = TruthOrigin.TRANSCRIPTION
    prefill: str | None = None
    """The reading shown to the person when the line was created, kept so the benchmark can say
    how much of the truth is just the pre-fill confirmed (anchoring)."""
    region_id: UUID | None = None
    """The OCR region a teacher corrected (P16), if any."""

    def __post_init__(self) -> None:
        for text in (self.text, self.prefill or ""):
            if "\n" in text or "\r" in text:
                raise InvariantError("a line of ground truth is one line of text")
        if self.status is TruthStatus.VERIFIED and not self.text.strip():
            raise InvariantError("a verified line has text (an empty line is ignored instead)")

    @property
    def counts(self) -> bool:
        """The benchmark measures verified lines only."""
        return self.status is TruthStatus.VERIFIED


@dataclass(frozen=True, slots=True, kw_only=True)
class TruthPage:
    id: str
    capture: CaptureType
    image: str
    """File name of the cleaned page image next to the lines."""
    width: int
    height: int
    lines: tuple[TruthLine, ...]
    source: str = ""
    """A neutral label for where the page comes from (a sample code, a dataset name)."""
    college_id: UUID | None = None
    """Set for pages that come from a college's review UI; those stay in the college's data."""

    def __post_init__(self) -> None:
        if not _PAGE_ID.match(self.id):
            raise InvariantError("a page id is lower-case letters, digits, '.', '_' or '-'")
        if not _IMAGE_NAME.match(self.image):
            raise InvariantError("a page image is a plain .jpg or .png file name")
        if self.width <= 0 or self.height <= 0:
            raise InvariantError("a page has a size")
        if not self.lines:
            raise InvariantError("a ground-truth page has at least one line")
        if len(self.source) > 80 or "/" in self.source or "\\" in self.source:
            raise InvariantError("a source label is a short name, not a path")
        for line in self.lines:
            if line.box.x1 > self.width or line.box.y1 > self.height:
                raise InvariantError("a line lies outside its page")

    def verified(self) -> tuple[TruthLine, ...]:
        return tuple(ln for ln in self.lines if ln.counts)

    def with_lines(self, lines: tuple[TruthLine, ...]) -> "TruthPage":
        return replace(self, lines=lines)
