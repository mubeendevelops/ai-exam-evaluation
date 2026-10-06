"""A teacher's OCR correction as ground truth for the benchmark (P16, design.md "Learning loop").

Every text correction in the review is stored as a verified line of a ground-truth page in the
booklet's own folder (``college/{id}/booklet/{id}/groundtruth/``). That is college data and
student data: it is deleted with the booklet (``BookletService.delete`` removes the folder) and
never leaves the college; only the fitted calibrations, which hold numbers, are shared. The
page's image is the cleaned page the teacher saw, so the line's box fits it."""

from collections.abc import Sequence

from tarn_core.domain.booklet import Page, Region
from tarn_core.domain.common import BlobKey, college_blob_key
from tarn_core.domain.groundtruth import CaptureType, TruthPage
from tarn_core.domain.ocr import ContentClass
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.repositories import BookletRepository
from tarn_core.ports.storage import BlobStore
from tarn_core.services.groundtruth import (
    GroundTruthService,
    NewPage,
    page_from_jsonl,
    page_to_jsonl,
)

SOURCE = "review"
_MEDIA = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}


def truth_page_id(page: Page) -> str:
    """``p-<page id>``: stable, unique, and a valid ground-truth page id."""
    return f"p-{page.id.hex}"


class BookletTruthStore:
    """A ``GroundTruthStore`` over one booklet's blobs. The blob store cannot list, so the
    booklet's page ids are given (a page has at most one ground-truth file)."""

    def __init__(
        self,
        blobs: BlobStore,
        college_id: CollegeId,
        booklet_id: BookletId,
        page_ids: Sequence[str],
    ) -> None:
        self._blobs = blobs
        self._college = college_id
        self._booklet = booklet_id
        self._known = sorted(page_ids)

    def _key(self, name: str) -> BlobKey:
        return college_blob_key(self._college, "booklet", str(self._booklet), "groundtruth", name)

    def page_ids(self) -> list[str]:
        return [p for p in self._known if self._blobs.exists(self._key(f"{p}.jsonl"))]

    def get(self, page_id: str) -> TruthPage:
        if page_id not in self._known:
            raise NotFoundError(f"page {page_id} is not in the set")
        return page_from_jsonl(self._blobs.get(self._key(f"{page_id}.jsonl")).decode("utf-8"))

    def image(self, page_id: str) -> bytes:
        return self._blobs.get(self._key(self.get(page_id).image))

    def save(self, page: TruthPage, image: bytes | None = None) -> None:
        if page.id not in self._known:
            raise InvariantError("the page does not belong to this booklet")
        if image is not None:
            media = _MEDIA[page.image.rsplit(".", 1)[-1].lower()]
            self._blobs.put(self._key(page.image), image, media)
        elif not self._blobs.exists(self._key(page.image)):
            raise InvariantError("the first save of a page needs its image")
        self._blobs.put(
            self._key(f"{page.id}.jsonl"),
            page_to_jsonl(page).encode("utf-8"),
            "application/x-ndjson",
        )


class CorrectionTruth:
    """Records the teacher's text of a corrected region as ground truth."""

    def __init__(self, *, booklets: BookletRepository, blobs: BlobStore) -> None:
        self._booklets = booklets
        self._blobs = blobs

    def record(
        self, college_id: CollegeId, booklet_id: BookletId, region: Region, text: str
    ) -> None:
        pages = self._booklets.pages(college_id, booklet_id)
        page = next((p for p in pages if p.id == region.page_id), None)
        if page is None:
            raise NotFoundError(f"page {region.page_id}")
        store = BookletTruthStore(
            self._blobs, college_id, booklet_id, [truth_page_id(p) for p in pages]
        )
        extension = page.image.value.rsplit(".", 1)[-1].lower()
        GroundTruthService(store).record_correction(
            page_id=truth_page_id(page),
            box=region.box,
            text=text,
            content_class=region.content_class or ContentClass.CURSIVE,
            region_id=region.id,
            new_page=NewPage(
                image=self._blobs.get(page.image),
                image_name=f"{truth_page_id(page)}.{'jpg' if extension == 'jpeg' else extension}",
                capture=CaptureType.PHONE_PHOTO,
                width=page.width,
                height=page.height,
                source=SOURCE,
                college_id=college_id,
            ),
        )
