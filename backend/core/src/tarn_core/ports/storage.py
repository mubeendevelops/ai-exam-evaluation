"""Ports for page input and blob storage."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from tarn_core.domain.common import BlobKey
from tarn_core.ids import BookletId, CollegeId


@dataclass(frozen=True, slots=True, kw_only=True)
class PageImage:
    index: int
    data: bytes
    media_type: str


class PageSource(Protocol):
    """Page images and metadata for one booklet (HTTP upload, bucket event, local folder)."""

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[PageImage]: ...


class BlobStore(Protocol):
    """Images and PDFs, under ``college/{id}/...`` or ``global/...`` keys."""

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None: ...

    def get(self, key: BlobKey) -> bytes:
        """Raises NotFoundError if absent."""
        ...

    def exists(self, key: BlobKey) -> bool: ...

    def delete(self, key: BlobKey) -> None: ...
