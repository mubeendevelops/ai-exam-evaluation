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


@pytest.mark.integration
def test_a_booklets_ground_truth_lives_in_its_folder_and_goes_with_it() -> None:
    """A teacher's corrections are kept under the booklet (P16) and deleted with it."""
    from tarn_core.domain.common import Box, college_blob_key
    from tarn_core.domain.groundtruth import CaptureType
    from tarn_core.domain.ocr import ContentClass
    from tarn_core.ids import BookletId, CollegeId
    from tarn_core.services.groundtruth import GroundTruthService, NewPage
    from tarn_core.services.workflow import BookletTruthStore

    settings = Settings()
    blobs = MinioBlobStore(make_client(settings), settings.blob_bucket)
    college, booklet = CollegeId(uuid4()), BookletId(uuid4())
    store = BookletTruthStore(blobs, college, booklet, ["p-one"])
    folder = college_blob_key(college, "booklet", str(booklet))
    try:
        GroundTruthService(store).record_correction(
            page_id="p-one",
            box=Box(x0=10, y0=10, x1=200, y1=40),
            text="synthetic text",
            content_class=ContentClass.CURSIVE,
            new_page=NewPage(
                image=b"\x89PNG synthetic",
                image_name="p-one.png",
                capture=CaptureType.PHONE_PHOTO,
                width=300,
                height=400,
                source="review",
                college_id=college,
            ),
        )
        assert store.page_ids() == ["p-one"]
        assert store.get("p-one").verified()[0].text == "synthetic text"
        assert store.image("p-one") == b"\x89PNG synthetic"
    finally:
        assert blobs.delete_prefix(folder) == 2
    assert store.page_ids() == []
