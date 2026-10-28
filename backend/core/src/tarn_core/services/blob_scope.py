"""A blob store bound to one college (P21, D152; content since P23, D165).

Blob keys reach the stores from rows that row-level security already scopes, so a college
never *finds* another college's key. This wrapper makes the rule hold even when a key comes
from somewhere else (a bug, a crafted payload): inside college A's unit of work, any key under
``college/{B}/`` is refused like another college's row (``TenantViolationError``). That covers
content files too, which live under ``college/{id}/content/`` since P23.

``global/`` keys are Tarn-wide: a college may read them (legacy content files until
``tarn content relocate-blobs`` has moved them; a future Tarn question bank) but never write
or delete them. Bound to no college, only ``global/`` keys pass."""

from tarn_core.domain.common import BlobKey
from tarn_core.errors import TenantViolationError
from tarn_core.ids import CollegeId
from tarn_core.ports.storage import BlobStore


class CollegeScopedBlobStore:
    def __init__(self, inner: BlobStore, college_id: CollegeId | None) -> None:
        self._inner = inner
        self._college_id = college_id

    @property
    def college_id(self) -> CollegeId | None:
        return self._college_id

    def _check(self, key: BlobKey, *, write: bool = False) -> BlobKey:
        owner = key.college_id
        if owner is None:
            if write and self._college_id is not None:
                raise TenantViolationError("a college cannot change Tarn-wide files")
            return key
        if owner != self._college_id:
            raise TenantViolationError("the blob belongs to another college")
        return key

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None:
        self._inner.put(self._check(key, write=True), data, media_type)

    def get(self, key: BlobKey) -> bytes:
        return self._inner.get(self._check(key))

    def exists(self, key: BlobKey) -> bool:
        return self._inner.exists(self._check(key))

    def delete(self, key: BlobKey) -> None:
        self._inner.delete(self._check(key, write=True))

    def delete_prefix(self, prefix: BlobKey) -> int:
        return self._inner.delete_prefix(self._check(prefix, write=True))

    def usage(self, college_id: CollegeId) -> int:
        if college_id != self._college_id:
            raise TenantViolationError("the blobs belong to another college")
        return self._inner.usage(college_id)


def scoped_blobs(blobs: BlobStore, college_id: CollegeId | None) -> BlobStore:
    """``blobs`` bound to ``college_id``; an already scoped store is re-bound, not nested."""
    if isinstance(blobs, CollegeScopedBlobStore):
        blobs = blobs._inner
    return CollegeScopedBlobStore(blobs, college_id)
