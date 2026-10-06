"""The page pipeline (design.md "Booklet pipeline", stages 1 to 3): split the upload into pages,
clean each page, run the quality gate. The image work is behind ``PageSplitter`` and
``PageCleaner``; the core decides the order, stores the results and judges the quality.

``PagePipeline.step`` does exactly one unit of work and is safe to repeat, so the worker can run
every step in its own transaction and a crash resumes from the last finished one:

1. *prepare + split*: UPLOADED → PROCESSING and the page rows with their originals
   (skipped when the pages exist);
2. *clean*: one page per step, the first not yet cleaned (stored under ``clean/``);
3. *gate*: every page cleaned → PAGES_READY, or NEEDS_RETAKE when a page is flagged and the
   teacher has not chosen to use it anyway. PAGES_READY queues the OCR job (``booklet.read``,
   P10) in the same unit of work, as does the teacher's last "use anyway".

A file that cannot be read ends the booklet as FAILED (a retry cannot help); any other error
propagates to the caller, which retries with backoff."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import (
    Booklet,
    BookletStatus,
    Page,
    PageMetrics,
    RetakeReason,
)
from tarn_core.domain.common import BlobKey, college_blob_key
from tarn_core.errors import InvariantError, NotFoundError, UnreadableFileError
from tarn_core.ids import BookletId, CollegeId, PageId, UserId
from tarn_core.ports.jobs import JOB_READ_BOOKLET, JobQueue
from tarn_core.ports.pages import PageCleaner, PageSplitter
from tarn_core.ports.repositories import BookletRepository
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime

FAILED_UNREADABLE = "unreadable_file"
FAILED_TOO_MANY_PAGES = "too_many_pages"
FAILED_PROCESSING = "processing_failed"

_EXTENSION = {"image/jpeg": "jpg", "image/png": "png"}


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityPolicy:
    """Thresholds of the quality gate. Placeholders until teacher-marked ground truth exists
    (CLAUDE.md "Known gaps"); they are set from what the sample booklets measure and from
    synthetic blur."""

    min_sharpness: float = 130.0
    max_glare_share: float = 0.06
    min_page_edge_px: int = 500
    """The shorter side of the cleaned page, in pixels."""

    def reasons(self, metrics: PageMetrics, width: int, height: int) -> tuple[RetakeReason, ...]:
        found: list[RetakeReason] = []
        if not metrics.page_found:
            found.append(RetakeReason.NO_PAGE_FOUND)
        if metrics.sharpness is not None and metrics.sharpness < self.min_sharpness:
            found.append(RetakeReason.BLURRY)
        if metrics.glare_share > self.max_glare_share:
            found.append(RetakeReason.GLARE)
        if min(width, height) < self.min_page_edge_px:
            found.append(RetakeReason.LOW_RESOLUTION)
        return tuple(found)


def booklet_key(college_id: CollegeId, booklet_id: BookletId, *parts: str) -> BlobKey:
    return college_blob_key(college_id, "booklet", str(booklet_id), *parts)


def queue_reading(jobs: JobQueue, college_id: CollegeId, booklet_id: BookletId) -> None:
    """Queue the OCR job of a booklet whose pages are ready (idempotent per booklet)."""
    jobs.enqueue(
        college_id,
        JOB_READ_BOOKLET,
        {"booklet_id": str(booklet_id)},
        key=f"{JOB_READ_BOOKLET}:{booklet_id}",
    )


class PagePipeline:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        blobs: BlobStore,
        splitter: PageSplitter,
        cleaner: PageCleaner,
        runtime: Runtime,
        jobs: JobQueue,
        policy: QualityPolicy | None = None,
        max_pages: int = 40,
    ) -> None:
        self._booklets = booklets
        self._jobs = jobs
        self._blobs = blobs
        self._splitter = splitter
        self._cleaner = cleaner
        self._rt = runtime
        self._policy = policy or QualityPolicy()
        self._max_pages = max_pages

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        """One unit of work; True when there is nothing more to do for this booklet."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status not in (BookletStatus.UPLOADED, BookletStatus.PROCESSING):
            return True
        pages = self._booklets.pages(college_id, booklet_id)
        try:
            if not pages:
                self._split(booklet)
                return False
            pending = next((p for p in pages if not p.cleaned), None)
            if pending is not None:
                self._clean(booklet, pending)
                return False
        except UnreadableFileError:
            self._fail(booklet, FAILED_UNREADABLE)
            return True
        except _TooManyPagesError:
            self._fail(booklet, FAILED_TOO_MANY_PAGES)
            return True
        self._gate(booklet, pages)
        return True

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """The job ran out of attempts: end the booklet as FAILED so it stops waiting."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status in (BookletStatus.UPLOADED, BookletStatus.PROCESSING):
            self._fail(booklet, FAILED_PROCESSING)

    # -- stages -----------------------------------------------------------------------------

    def _split(self, booklet: Booklet) -> None:
        if not booklet.sources:
            raise UnreadableFileError("the booklet has no stored files")
        if booklet.status is BookletStatus.UPLOADED:
            booklet = self._save(booklet.moved_to(BookletStatus.PROCESSING))
        pages: list[Page] = []
        for source in booklet.sources:
            data = self._read(source.key)
            room = self._max_pages - len(pages)
            if source.media_type == "application/pdf":
                try:
                    images = self._splitter.split(data, source.media_type, room)
                except InvariantError:
                    raise _TooManyPagesError from None
                keys: list[BlobKey] = []
                for image in images:
                    key = booklet_key(
                        booklet.college_id,
                        booklet.id,
                        "original",
                        f"{len(pages) + len(keys) + 1:03d}.{_EXTENSION[image.media_type]}",
                    )
                    self._blobs.put(key, image.data, image.media_type)
                    keys.append(key)
            else:
                keys = [source.key]  # an image upload is its own original
            for key in keys:
                if len(pages) >= self._max_pages:
                    raise _TooManyPagesError
                pages.append(self._page(booklet, len(pages), key))
        if not pages:
            raise UnreadableFileError("the files contain no pages")
        for page in pages:
            self._booklets.save_page(booklet.college_id, page)

    def _read(self, key: BlobKey) -> bytes:
        try:
            return self._blobs.get(key)
        except NotFoundError:
            # A stored file that is gone cannot be fetched again by retrying.
            raise UnreadableFileError("a stored file is missing") from None

    def _page(self, booklet: Booklet, index: int, original: BlobKey) -> Page:
        # Width and height are unknown until the cleaner has decoded it: 1 x 1 stands in.
        return Page(
            id=self._rt.new_id(PageId),
            college_id=booklet.college_id,
            booklet_id=booklet.id,
            index=index,
            image=original,
            original=original,
            width=1,
            height=1,
            cleaned=False,
        )

    def _clean(self, booklet: Booklet, page: Page) -> None:
        if page.original is None:
            raise InvariantError("a page to clean needs its original")
        cleaned = self._cleaner.clean(self._read(page.original))
        key = booklet_key(booklet.college_id, booklet.id, "clean", f"{page.index + 1:03d}.jpg")
        self._blobs.put(key, cleaned.image, cleaned.media_type)
        reasons = self._policy.reasons(cleaned.metrics, cleaned.width, cleaned.height)
        self._booklets.save_page(
            booklet.college_id,
            replace(
                page,
                image=key,
                width=cleaned.width,
                height=cleaned.height,
                cleaned=True,
                metrics=cleaned.metrics,
                retake_reasons=reasons,
                use_anyway=False,
            ),
        )

    def _gate(self, booklet: Booklet, pages: Sequence[Page]) -> None:
        flagged = [p for p in pages if p.needs_retake]
        status = BookletStatus.NEEDS_RETAKE if flagged else BookletStatus.PAGES_READY
        booklet = self._save(booklet.moved_to(status))
        if status is BookletStatus.PAGES_READY:
            queue_reading(self._jobs, booklet.college_id, booklet.id)
        self._rt.record(
            booklet.college_id,
            None,
            AuditAction.BOOKLET_PROCESSED,
            booklet_id=booklet.id,
            after={
                "status": status.value,
                "pages": len(pages),
                "flagged_pages": [p.index + 1 for p in flagged],
                "cleaner": f"{self._cleaner.ref.name} {self._cleaner.ref.version}",
            },
        )

    def _fail(self, booklet: Booklet, reason: str) -> None:
        booklet = self._save(booklet.moved_to(BookletStatus.FAILED, failure_reason=reason))
        self._rt.record(
            booklet.college_id,
            None,
            AuditAction.BOOKLET_FAILED,
            booklet_id=booklet.id,
            after={"reason": reason},
        )

    def _save(self, updated: Booklet) -> Booklet:
        self._booklets.save(updated.college_id, updated)
        return updated


class _TooManyPagesError(Exception):
    """Internal: more pages than allowed (the splitter or the page count raised it)."""


class PageDecisions:
    """What a teacher decides about flagged pages."""

    def __init__(self, *, booklets: BookletRepository, runtime: Runtime, jobs: JobQueue) -> None:
        self._booklets = booklets
        self._rt = runtime
        self._jobs = jobs

    def use_anyway(
        self, college_id: CollegeId, actor_id: UserId, booklet_id: BookletId, page_index: int
    ) -> Booklet:
        """Go on with a flagged page (page numbers count from 0). When no flagged page is left
        the booklet moves from NEEDS_RETAKE to PAGES_READY."""
        booklet = self._booklets.get(college_id, booklet_id)
        pages = self._booklets.pages(college_id, booklet_id)
        page = next((p for p in pages if p.index == page_index), None)
        if page is None:
            raise InvariantError(f"the booklet has no page {page_index + 1}")
        if not page.retake_reasons:
            raise InvariantError("this page was not flagged")
        if booklet.status is not BookletStatus.NEEDS_RETAKE:
            raise InvariantError("the booklet is not waiting for a retake decision")
        self._booklets.save_page(college_id, replace(page, use_anyway=True))
        remaining = [p for p in pages if p.index != page_index and p.needs_retake]
        status = BookletStatus.NEEDS_RETAKE if remaining else BookletStatus.PAGES_READY
        updated = (
            replace(booklet, version=booklet.version + 1)
            if status is booklet.status
            else booklet.moved_to(status)
        )
        self._booklets.save(college_id, updated)
        if status is BookletStatus.PAGES_READY:
            queue_reading(self._jobs, college_id, booklet_id)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.PAGE_USED_ANYWAY,
            booklet_id=booklet_id,
            after={
                "page": page_index + 1,
                "reasons": [r.value for r in page.retake_reasons],
                "booklet_status": status.value,
            },
        )
        return updated
