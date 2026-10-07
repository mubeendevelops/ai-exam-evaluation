"""``PageSource`` for Cloud Storage events: a booklet is uploaded as files under
``incoming/<college_id>/<booklet_id>/`` and finished by writing the zero-byte object
``_complete`` last. The "object finalized" event of that marker (Eventarc, CloudEvents data)
names the booklet; this source then reads the page files of that folder in name order.

One event per booklet (the marker) rather than one per page file keeps a half-uploaded booklet
from being picked up. The ids come from the object name, and the source refuses to serve any
other college or booklet than the event's: a call for another college is a tenancy violation,
not an empty result. Who may write under ``incoming/<college_id>/`` is a bucket permission
(Terraform), not something this adapter can check (P21: only the API and worker service
accounts may write to the bucket). The adapter checks what it can: the event must name the
configured bucket, and a folder may hold at most ``max_files`` page files of ``max_bytes`` in
all (sizes from the listing, before anything is downloaded)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from tarn_adapters.blob.gcs_store import GcsClient
from tarn_adapters.sources.ordering import EXTENSIONS, media_type_of, natural_key
from tarn_core.errors import InvariantError, TenantViolationError, UploadTooLargeError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports.storage import PageImage

PREFIX = "incoming"
COMPLETE_MARKER = "_complete"


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageEvent:
    bucket: str
    folder: str
    """``incoming/<college_id>/<booklet_id>``."""
    college_id: CollegeId
    booklet_id: BookletId


def incoming_folder(college_id: CollegeId, booklet_id: BookletId) -> str:
    return f"{PREFIX}/{college_id}/{booklet_id}"


def parse_storage_event(data: Mapping[str, Any]) -> StorageEvent:
    """The booklet a "finalized" event is about. ``InvariantError`` for an event that is not
    the ``_complete`` marker of an incoming booklet folder."""
    bucket, name = data.get("bucket"), data.get("name")
    if not isinstance(bucket, str) or not isinstance(name, str) or not bucket:
        raise InvariantError("the storage event names no object")
    parts = name.split("/")
    if len(parts) != 4 or parts[0] != PREFIX or parts[3] != COMPLETE_MARKER:
        raise InvariantError("the storage event is not the completion marker of a booklet")
    try:
        college_id = CollegeId(UUID(parts[1]))
        booklet_id = BookletId(UUID(parts[2]))
    except ValueError:
        raise InvariantError("the storage event names no valid college and booklet") from None
    return StorageEvent(
        bucket=bucket,
        folder="/".join(parts[:3]),
        college_id=college_id,
        booklet_id=booklet_id,
    )


class GcsEventPageSource:
    def __init__(
        self,
        event: StorageEvent,
        client: GcsClient,
        *,
        bucket: str | None = None,
        max_files: int = 40,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if bucket is not None and event.bucket != bucket:
            raise InvariantError("the storage event is for another bucket")
        self._event = event
        self._client = client
        self._max_files = max_files
        self._max_bytes = max_bytes

    @classmethod
    def from_event(
        cls,
        data: Mapping[str, Any],
        client: GcsClient,
        *,
        bucket: str | None = None,
        max_files: int = 40,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> "GcsEventPageSource":
        """``bucket``: the bucket booklets are uploaded to; an event naming any other bucket
        is refused."""
        return cls(
            parse_storage_event(data),
            client,
            bucket=bucket,
            max_files=max_files,
            max_bytes=max_bytes,
        )

    @property
    def event(self) -> StorageEvent:
        return self._event

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[PageImage]:
        if (college_id, booklet_id) != (self._event.college_id, self._event.booklet_id):
            raise TenantViolationError("the event is for another college or booklet")
        folder = self._event.folder + "/"
        listed = [
            blob
            for blob in self._client.list_blobs(self._event.bucket, prefix=folder)
            if "/" not in blob.name.removeprefix(folder)
            and blob.name.lower().endswith(tuple(EXTENSIONS))
        ]
        if not listed:
            raise InvariantError("the booklet folder holds no PDF, JPEG or PNG files")
        if len(listed) > self._max_files:
            raise UploadTooLargeError(f"At most {self._max_files} files per booklet.")
        if sum(blob.size or 0 for blob in listed) > self._max_bytes:
            raise UploadTooLargeError(
                f"The booklet is larger than {self._max_bytes // (1024 * 1024)} MB."
            )
        names = sorted(
            (blob.name for blob in listed), key=lambda n: natural_key(n.removeprefix(folder))
        )
        bucket = self._client.bucket(self._event.bucket)
        pages = []
        total = 0
        for index, name in enumerate(names):
            data = bucket.blob(name).download_as_bytes()
            total += len(data)  # the listing's sizes may be stale: count what arrived too
            if total > self._max_bytes:
                raise UploadTooLargeError(
                    f"The booklet is larger than {self._max_bytes // (1024 * 1024)} MB."
                )
            pages.append(PageImage(index=index, data=data, media_type=media_type_of(data)))
        return pages
