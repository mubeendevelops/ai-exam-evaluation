"""Uploading a booklet: one PDF, or a set of JPEG/PNG images, becomes one booklet in the queue.

The files are judged by their first bytes (a name or a declared type proves nothing), stored
under ``college/{id}/booklet/{id}/source/``, and the booklet is registered and a page-preparation
job queued in the same unit of work, so a booklet is never left waiting without a job.

The limits are the teacher's queue (at most ``max_waiting`` booklets queued or in processing,
design decision 12), the total size, and the number of images; a PDF's page count is checked by
the pipeline when it splits the file."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.domain.booklet import SourceFile
from tarn_core.domain.common import BlobKey, college_blob_key
from tarn_core.errors import (
    DuplicateBookletError,
    InvariantError,
    QueueFullError,
    UnsupportedFileError,
    UploadTooLargeError,
)
from tarn_core.ids import BlueprintId, BookletId, CollegeId, StudentId, UserId
from tarn_core.ports.jobs import JOB_PREPARE_BOOKLET, JobQueue
from tarn_core.ports.repositories import BookletRepository
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.booklets import BookletService, Registration

_PDF = b"%PDF-"
_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"
_EXTENSION = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"}


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadLimits:
    max_waiting: int = 5
    """Booklets one teacher may have queued or in processing at once."""
    max_total_bytes: int = 100 * 1024 * 1024
    max_files: int = 40


def sniff_media_type(data: bytes) -> str:
    """``application/pdf``, ``image/jpeg`` or ``image/png`` from the first bytes."""
    if data[:5] == _PDF:
        return "application/pdf"
    if data[:3] == _JPEG:
        return "image/jpeg"
    if data[:8] == _PNG:
        return "image/png"
    raise UnsupportedFileError("Upload a PDF, or JPEG or PNG images.")


def combined_hash(file_hashes: Sequence[str]) -> str:
    """The booklet's file hash: the file's own SHA-256 for one file, and for an image set the
    SHA-256 of the images' hashes in upload order (the same pictures in the same order are the
    same booklet)."""
    if len(file_hashes) == 1:
        return file_hashes[0]
    return hashlib.sha256("\n".join(file_hashes).encode("ascii")).hexdigest()


class UploadService:
    def __init__(
        self,
        *,
        booklets: BookletService,
        repository: BookletRepository,
        blobs: BlobStore,
        jobs: JobQueue,
        runtime: Runtime,
        limits: UploadLimits | None = None,
    ) -> None:
        self._booklets = booklets
        self._repo = repository
        self._blobs = blobs
        self._jobs = jobs
        self._rt = runtime
        self._limits = limits or UploadLimits()

    def upload(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        student_id: StudentId,
        blueprint_id: BlueprintId,
        files: Sequence[bytes],
        allow_duplicate: bool = False,
    ) -> Registration:
        """Raises ``UnsupportedFileError``, ``UploadTooLargeError``, ``InvariantError`` (a mix
        of PDF and images, or several PDFs), ``QueueFullError``, ``DuplicateBookletError``."""
        types = self._check_files(files)
        waiting = self._repo.count_waiting(college_id, actor_id)
        if waiting >= self._limits.max_waiting:
            raise QueueFullError(
                f"You already have {waiting} booklets waiting. Wait for one to be processed "
                "before uploading another."
            )
        file_hash = combined_hash([hashlib.sha256(f).hexdigest() for f in files])
        duplicates = tuple(b.id for b in self._repo.find_by_hash(college_id, file_hash))
        if duplicates and not allow_duplicate:
            raise DuplicateBookletError(duplicates)

        booklet_id = self._rt.new_id(BookletId)
        sources = tuple(
            SourceFile(
                key=self._source_key(college_id, booklet_id, number, media_type),
                media_type=media_type,
                size_bytes=len(data),
            )
            for number, (data, media_type) in enumerate(zip(files, types, strict=True), start=1)
        )
        stored: list[BlobKey] = []
        try:
            for source, data in zip(sources, files, strict=True):
                self._blobs.put(source.key, data, source.media_type)
                stored.append(source.key)
            registration = self._booklets.register(
                college_id,
                actor_id,
                student_id=student_id,
                blueprint_id=blueprint_id,
                file_sha256=file_hash,
                booklet_id=booklet_id,
                sources=sources,
            )
            self._jobs.enqueue(
                college_id,
                JOB_PREPARE_BOOKLET,
                {"booklet_id": str(booklet_id)},
                key=f"{JOB_PREPARE_BOOKLET}:{booklet_id}",
            )
        except BaseException:
            for key in stored:
                self._blobs.delete(key)
            raise
        return registration

    def _check_files(self, files: Sequence[bytes]) -> list[str]:
        if not files:
            raise InvariantError("Choose a PDF, or the page images of the booklet.")
        if len(files) > self._limits.max_files:
            raise UploadTooLargeError(f"At most {self._limits.max_files} files per booklet.")
        if sum(len(f) for f in files) > self._limits.max_total_bytes:
            raise UploadTooLargeError(
                f"The upload is larger than {self._limits.max_total_bytes // (1024 * 1024)} MB."
            )
        if any(not f for f in files):
            raise InvariantError("A file in the upload is empty.")
        types = [sniff_media_type(f) for f in files]
        if "application/pdf" in types and len(files) > 1:
            raise InvariantError("Upload one PDF, or several images, not a mix.")
        return types

    @staticmethod
    def _source_key(
        college_id: CollegeId, booklet_id: BookletId, number: int, media_type: str
    ) -> BlobKey:
        return college_blob_key(
            college_id,
            "booklet",
            str(booklet_id),
            "source",
            f"{number:03d}.{_EXTENSION[media_type]}",
        )
