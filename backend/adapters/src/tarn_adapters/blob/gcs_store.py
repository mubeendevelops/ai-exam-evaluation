"""``BlobStore`` on Cloud Storage (production, asia-south1). Keys are ``college/{id}/...`` or
``global/...`` object names in one bucket.

The client is injected (``google.cloud.storage.Client`` in production), so tests run on a fake.
Authentication is the service account of the Cloud Run service or job (Application Default
Credentials): no key file, no secret in the settings."""

from collections.abc import Iterable
from typing import Protocol

from tarn_core.domain.common import BlobKey
from tarn_core.errors import NotFoundError


class _Blob(Protocol):
    def upload_from_string(self, data: bytes, content_type: str) -> None: ...

    def download_as_bytes(self) -> bytes: ...

    def exists(self) -> bool: ...

    def delete(self) -> None: ...


class _Listed(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def size(self) -> int | None: ...


class _Bucket(Protocol):
    def blob(self, blob_name: str) -> _Blob: ...

    def exists(self) -> bool: ...


class GcsClient(Protocol):
    """The part of ``google.cloud.storage.Client`` used here."""

    def bucket(self, bucket_name: str) -> _Bucket: ...

    def list_blobs(
        self, bucket_or_name: str, *, prefix: str | None = None
    ) -> Iterable[_Listed]: ...


def make_gcs_client(project: str = "") -> GcsClient:
    import google.cloud.storage as storage  # lazily: development never needs it

    client: GcsClient = storage.Client(project=project or None)
    return client


def _not_found() -> type[Exception]:
    from google.api_core.exceptions import NotFound

    return NotFound


class GcsBlobStore:
    def __init__(self, client: GcsClient, bucket: str) -> None:
        self._client = client
        self._bucket_name = bucket
        self._bucket = client.bucket(bucket)

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None:
        self._bucket.blob(key.value).upload_from_string(data, content_type=media_type)

    def get(self, key: BlobKey) -> bytes:
        try:
            return self._bucket.blob(key.value).download_as_bytes()
        except _not_found():
            raise NotFoundError(f"blob {key.value}") from None

    def exists(self, key: BlobKey) -> bool:
        return self._bucket.blob(key.value).exists()

    def delete(self, key: BlobKey) -> None:
        try:
            self._bucket.blob(key.value).delete()
        except _not_found():
            pass  # absent keys are not an error

    def delete_prefix(self, prefix: BlobKey) -> int:
        """Every object under ``prefix/`` (the trailing slash keeps ``booklet/1`` from matching
        ``booklet/10``)."""
        names = [
            b.name for b in self._client.list_blobs(self._bucket_name, prefix=prefix.value + "/")
        ]
        for name in names:
            self.delete(BlobKey(name))
        return len(names)

    def bucket_exists(self) -> bool:
        return self._bucket.exists()
