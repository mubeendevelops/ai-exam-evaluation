"""``BlobStore`` on MinIO / any S3-compatible bucket (the dev store; Cloud Storage and S3
adapters come in P20). Keys are ``college/{id}/...`` or ``global/...``."""

import io
from typing import Protocol

from minio.error import S3Error

from tarn_core.domain.common import BlobKey
from tarn_core.errors import NotFoundError

_MISSING = {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}


class _Client(Protocol):
    """The part of ``minio.Minio`` used here (so tests can pass a fake)."""

    def put_object(
        self, bucket_name: str, object_name: str, data: io.BytesIO, length: int, content_type: str
    ) -> object: ...

    def get_object(self, bucket_name: str, object_name: str) -> "_Response": ...

    def stat_object(self, bucket_name: str, object_name: str) -> object: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...


class _Response(Protocol):
    def read(self) -> bytes: ...

    def close(self) -> None: ...

    def release_conn(self) -> None: ...


class MinioBlobStore:
    def __init__(self, client: _Client, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def put(self, key: BlobKey, data: bytes, media_type: str) -> None:
        self._client.put_object(
            self._bucket, key.value, io.BytesIO(data), len(data), content_type=media_type
        )

    def get(self, key: BlobKey) -> bytes:
        try:
            response = self._client.get_object(self._bucket, key.value)
        except S3Error as error:
            if error.code in _MISSING:
                raise NotFoundError(f"blob {key.value}") from None
            raise
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def exists(self, key: BlobKey) -> bool:
        try:
            self._client.stat_object(self._bucket, key.value)
        except S3Error as error:
            if error.code in _MISSING:
                return False
            raise
        return True

    def delete(self, key: BlobKey) -> None:
        self._client.remove_object(self._bucket, key.value)  # absent keys are not an error
