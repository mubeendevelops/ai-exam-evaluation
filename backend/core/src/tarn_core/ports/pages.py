"""Ports for turning an uploaded file into clean page images (design.md "Booklet pipeline").

The core decides *what to do* with a page (quality policy, retake reasons, stage order); an
adapter does the image work and reports what it measured."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tarn_core.domain.booklet import PageMetrics
from tarn_core.domain.common import EngineRef
from tarn_core.ports.storage import PageImage


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanedPage:
    image: bytes
    media_type: str
    width: int
    height: int
    metrics: PageMetrics


class PageSplitter(Protocol):
    """One uploaded file → page images in reading order. A PDF becomes one image per page
    (the scanned image itself when there is one); an image file is one page, unchanged."""

    def split(self, data: bytes, media_type: str, max_pages: int) -> Sequence[PageImage]:
        """Raises ``UnreadableFileError`` for a file that is not a readable PDF or image, and
        ``InvariantError`` when it has more than ``max_pages`` pages."""
        ...


class PageCleaner(Protocol):
    """Orientation, deskew, crop to the main page, perspective flattening, shadow reduction
    and compression, plus the measurements the quality gate needs."""

    @property
    def ref(self) -> EngineRef: ...

    def clean(self, data: bytes) -> CleanedPage:
        """Raises ``UnreadableFileError`` when the bytes are not a decodable image."""
        ...
