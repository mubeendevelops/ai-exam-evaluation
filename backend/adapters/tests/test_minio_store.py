"""The MinIO blob store: error mapping with a fake client, and a round trip on the dev MinIO
(``make up``; integration)."""

import io
from uuid import uuid4

import pytest
from minio.error import S3Error

from tarn_adapters.blob.minio_client import make_client
from tarn_adapters.blob.minio_store import MinioBlobStore
from tarn_adapters.config import Settings
from tarn_core.domain.common import BlobKey, global_blob_key
from tarn_core.errors import NotFoundError


def _s3(code: str) -> S3Error:
    return S3Error(code, code, "/x", "req", "host", None)  # type: ignore[arg-type]


class _Fake:
    def __init__(self, code: str) -> None:
        self.code = code

    def put_object(self, *args: object, **kwargs: object) -> None: ...

    def get_object(self, bucket_name: str, object_name: str) -> io.BytesIO:
        raise _s3(self.code)

    def stat_object(self, bucket_name: str, object_name: str) -> None:
        raise _s3(self.code)

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...


def test_missing_objects_are_not_found_and_other_errors_are_not_hidden() -> None:
    key = BlobKey("global/a/b.pdf")
    missing = MinioBlobStore(_Fake("NoSuchKey"), "tarn")  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        missing.get(key)
    assert missing.exists(key) is False
    broken = MinioBlobStore(_Fake("AccessDenied"), "tarn")  # type: ignore[arg-type]
    with pytest.raises(S3Error):
        broken.get(key)
    with pytest.raises(S3Error):
        broken.exists(key)


@pytest.mark.integration
def test_round_trip_on_the_dev_minio() -> None:
    settings = Settings()
    store = MinioBlobStore(make_client(settings), settings.blob_bucket)
    key = global_blob_key("tests", uuid4().hex, "key.pdf")
    assert store.exists(key) is False
    store.put(key, b"%PDF-1.7 synthetic", "application/pdf")
    try:
        assert store.exists(key) is True
        assert store.get(key) == b"%PDF-1.7 synthetic"
    finally:
        store.delete(key)
    assert store.exists(key) is False
    with pytest.raises(NotFoundError):
        store.get(key)
