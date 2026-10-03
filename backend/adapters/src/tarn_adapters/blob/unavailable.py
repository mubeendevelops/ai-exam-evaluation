"""A blob store for processes that must not touch blobs (auth requests, operator commands)."""

from tarn_core.domain.common import BlobKey


class NoBlobStore:
    def _refuse(self) -> RuntimeError:
        return RuntimeError("blob storage is not available here")

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None:
        raise self._refuse()

    def get(self, key: BlobKey) -> bytes:
        raise self._refuse()

    def exists(self, key: BlobKey) -> bool:
        raise self._refuse()

    def delete(self, key: BlobKey) -> None:
        raise self._refuse()

    def delete_prefix(self, prefix: BlobKey) -> int:
        raise self._refuse()
