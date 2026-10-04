"""In-memory ground-truth store for tests."""

from tarn_core.domain.groundtruth import TruthPage
from tarn_core.errors import InvariantError, NotFoundError


class MemoryGroundTruthStore:
    def __init__(self) -> None:
        self._pages: dict[str, TruthPage] = {}
        self._images: dict[str, bytes] = {}

    def page_ids(self) -> list[str]:
        return sorted(self._pages)

    def get(self, page_id: str) -> TruthPage:
        try:
            return self._pages[page_id]
        except KeyError:
            raise NotFoundError(f"page {page_id} is not in the set") from None

    def image(self, page_id: str) -> bytes:
        try:
            return self._images[page_id]
        except KeyError:
            raise NotFoundError(f"page {page_id} is not in the set") from None

    def save(self, page: TruthPage, image: bytes | None = None) -> None:
        if image is not None:
            self._images[page.id] = image
        elif page.id not in self._images:
            raise InvariantError("the first save of a page needs its image")
        self._pages[page.id] = page
