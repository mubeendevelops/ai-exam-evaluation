"""Where ground-truth pages are kept (a folder in development, a college's blobs for the review
UI feed, memory in tests)."""

from typing import Protocol

from tarn_core.domain.groundtruth import TruthPage


class GroundTruthStore(Protocol):
    def page_ids(self) -> list[str]:
        """Ids of every stored page, sorted."""
        ...

    def get(self, page_id: str) -> TruthPage:
        """Raises ``NotFoundError``."""
        ...

    def image(self, page_id: str) -> bytes:
        """The page image the lines refer to. Raises ``NotFoundError``."""
        ...

    def save(self, page: TruthPage, image: bytes | None = None) -> None:
        """Writes the page (and its image, when given; the first save needs one)."""
        ...
