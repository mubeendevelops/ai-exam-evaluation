"""A blob store bound to one college (P21).

Blob keys reach the stores from rows that row-level security already scopes, so a college
never *finds* another college's key. This wrapper makes the rule hold even when a key comes
from somewhere else (a bug, a crafted payload): inside college A's unit of work, any key under
``college/{B}/`` is refused like another college's row (``TenantViolationError``). Global
content (``global/...``) stays readable and writable: who may write it is the services' owner
check (``ensure_can_edit``). Bound to no college, only ``global/`` keys pass."""

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

    def _check(self, key: BlobKey) -> BlobKey:
        owner = key.college_id
        if owner is not None and owner != self._college_id:
            raise TenantViolationError("the blob belongs to another college")
        return key

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None:
        self._inner.put(self._check(key), data, media_type)

    def get(self, key: BlobKey) -> bytes:
        return self._inner.get(self._check(key))

    def exists(self, key: BlobKey) -> bool:
        return self._inner.exists(self._check(key))

    def delete(self, key: BlobKey) -> None:
        self._inner.delete(self._check(key))

    def delete_prefix(self, prefix: BlobKey) -> int:
        return self._inner.delete_prefix(self._check(prefix))


def scoped_blobs(blobs: BlobStore, college_id: CollegeId | None) -> BlobStore:
    """``blobs`` bound to ``college_id``; an already scoped store is re-bound, not nested."""
    if isinstance(blobs, CollegeScopedBlobStore):
        blobs = blobs._inner
    return CollegeScopedBlobStore(blobs, college_id)
