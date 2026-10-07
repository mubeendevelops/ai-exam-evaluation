"""The platform adapters (P20) without any cloud account: page sources (folder, stream, Cloud
Storage event), the Cloud Storage and S3 blob stores, AWS KMS, the Secret Manager key of the
LLM scorer and the settings that choose between them. Cloud clients are fakes."""

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from google.api_core.exceptions import NotFound

from tarn_adapters.auth.crypto import AwsKmsKeyManager, RoutingKeyManager
from tarn_adapters.blob import wiring
from tarn_adapters.blob.gcs_store import GcsBlobStore
from tarn_adapters.blob.minio_store import MinioBlobStore
from tarn_adapters.config import Settings
from tarn_adapters.llm.wiring import LlmConfigError, build_llm_scorer
from tarn_adapters.sources import FolderPageSource, StreamPageSource
from tarn_adapters.sources.gcs_event import (
    COMPLETE_MARKER,
    GcsEventPageSource,
    incoming_folder,
    parse_storage_event,
)
from tarn_adapters.sources.ordering import natural_key
from tarn_adapters.testing import PRODUCTION_CONNECTIONS
from tarn_core.domain.common import BlobKey, college_blob_key
from tarn_core.errors import (
    InvariantError,
    NotFoundError,
    TenantViolationError,
    UnsupportedFileError,
    UploadTooLargeError,
)
from tarn_core.ids import BookletId, CollegeId
from tarn_core.testing import fake_image, fake_pdf

COLLEGE = CollegeId(UUID(int=1))
BOOKLET = BookletId(UUID(int=2))
PNG = b"\x89PNG\r\n\x1a\n" + b"x"


# --- folder and stream page sources ---------------------------------------------------------------


def test_names_sort_naturally() -> None:
    names = ["page10.jpg", "page2.jpg", "Page1.jpg", "page2b.jpg"]
    assert sorted(names, key=natural_key) == ["Page1.jpg", "page2.jpg", "page2b.jpg", "page10.jpg"]


def test_a_folder_gives_its_pages_in_reading_order(tmp_path: Path) -> None:
    for name, data in {
        "page10.jpg": fake_image("c"),
        "page2.JPG": fake_image("b"),
        "page1.png": PNG,
        "booklet.json": b"{}",
        ".hidden.jpg": fake_image("x"),
    }.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "sub").mkdir()
    pages = FolderPageSource(tmp_path).pages(COLLEGE, BOOKLET)
    assert [(p.index, p.media_type) for p in pages] == [
        (0, "image/png"),
        (1, "image/jpeg"),
        (2, "image/jpeg"),
    ]
    assert [p.data for p in pages][1:] == [fake_image("b"), fake_image("c")]


def test_a_folder_may_hold_one_pdf(tmp_path: Path) -> None:
    (tmp_path / "scan.pdf").write_bytes(fake_pdf(3))
    (page,) = FolderPageSource(tmp_path).pages(COLLEGE, BOOKLET)
    assert page.media_type == "application/pdf"


def test_a_bad_folder_is_reported_as_such(tmp_path: Path) -> None:
    with pytest.raises(NotFoundError):
        FolderPageSource(tmp_path / "missing").pages(COLLEGE, BOOKLET)
    with pytest.raises(InvariantError, match="no PDF, JPEG or PNG"):
        FolderPageSource(tmp_path).pages(COLLEGE, BOOKLET)
    (tmp_path / "scan.pdf").write_bytes(b"not a pdf at all")  # judged by content, not the name
    with pytest.raises(UnsupportedFileError):
        FolderPageSource(tmp_path).pages(COLLEGE, BOOKLET)


def _tar(members: dict[str, bytes], *, directory: bool = False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        if directory:
            archive.addfile(tarfile.TarInfo("folder/"))  # a regular-file-less entry
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_a_stream_holds_one_file() -> None:
    source = StreamPageSource(io.BytesIO(fake_pdf(2)))
    (page,) = source.pages(COLLEGE, BOOKLET)
    assert page.media_type == "application/pdf" and page.data == fake_pdf(2)
    assert source.pages(COLLEGE, BOOKLET) == [page]  # read once, then remembered


def test_a_stream_may_hold_a_tar_archive_of_pages() -> None:
    archive = _tar(
        {"b/p10.jpg": fake_image("10"), "b/p2.png": PNG, "b/notes.txt": b"no", "b/.x.jpg": b"x"},
        directory=True,
    )
    pages = StreamPageSource(io.BytesIO(archive)).pages(COLLEGE, BOOKLET)
    assert [p.media_type for p in pages] == ["image/png", "image/jpeg"]
    assert [p.index for p in pages] == [0, 1]


def test_stream_problems_are_named() -> None:
    with pytest.raises(InvariantError, match="empty"):
        StreamPageSource(io.BytesIO(b"")).pages(COLLEGE, BOOKLET)
    with pytest.raises(UnsupportedFileError):
        StreamPageSource(io.BytesIO(b"plain text, not a booklet")).pages(COLLEGE, BOOKLET)
    with pytest.raises(UploadTooLargeError):
        StreamPageSource(io.BytesIO(fake_pdf(1, "x" * 100)), max_bytes=50).pages(COLLEGE, BOOKLET)
    with pytest.raises(InvariantError, match="no PDF, JPEG or PNG"):
        StreamPageSource(io.BytesIO(_tar({"notes.txt": b"x"}))).pages(COLLEGE, BOOKLET)
    broken = _tar({"a.jpg": fake_image()})[:257] + b"ustar" + b"\x00" * 600
    with pytest.raises(UnsupportedFileError, match="tar"):
        StreamPageSource(io.BytesIO(broken)).pages(COLLEGE, BOOKLET)


def test_an_archive_member_name_never_reaches_the_file_system(tmp_path: Path) -> None:
    sneaky = _tar({"../../escape.jpg": fake_image("e")})
    (page,) = StreamPageSource(io.BytesIO(sneaky)).pages(COLLEGE, BOOKLET)
    assert page.data == fake_image("e")  # read in memory; nothing was extracted anywhere


# --- Cloud Storage -------------------------------------------------------------------------------


class FakeGcs:
    """``google.cloud.storage.Client`` as far as the adapters use it, over a dict."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.content_types: dict[str, str] = {}
        self.buckets: list[str] = []

    def bucket(self, name: str) -> "FakeBucket":
        self.buckets.append(name)
        return FakeBucket(self)

    def list_blobs(
        self, bucket_or_name: str, *, prefix: str | None = None
    ) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(name=n, size=len(self.objects[n]))
            for n in sorted(self.objects)
            if n.startswith(prefix or "")
        ]


class FakeBucket:
    def __init__(self, client: FakeGcs) -> None:
        self._client = client

    def blob(self, name: str) -> "FakeBlob":
        return FakeBlob(self._client, name)

    def exists(self) -> bool:
        return True


class FakeBlob:
    def __init__(self, client: FakeGcs, name: str) -> None:
        self._client, self._name = client, name

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self._client.objects[self._name] = data
        self._client.content_types[self._name] = content_type

    def download_as_bytes(self) -> bytes:
        try:
            return self._client.objects[self._name]
        except KeyError:
            raise NotFound("no such object") from None  # type: ignore[no-untyped-call]

    def exists(self) -> bool:
        return self._name in self._client.objects

    def delete(self) -> None:
        if self._client.objects.pop(self._name, None) is None:
            raise NotFound("no such object")  # type: ignore[no-untyped-call]


def test_gcs_blob_store_round_trip_and_absence() -> None:
    client = FakeGcs()
    store = GcsBlobStore(client, "tarn-bucket")
    key = college_blob_key(COLLEGE, "booklet", "b1", "page.jpg")
    assert client.buckets == ["tarn-bucket"] and not store.exists(key)
    store.put(key, b"pixels", "image/jpeg")
    assert store.exists(key) and store.get(key) == b"pixels"
    assert client.content_types[key.value] == "image/jpeg"
    with pytest.raises(NotFoundError):
        store.get(BlobKey("global/missing.pdf"))
    store.delete(key)
    store.delete(key)  # absent keys are not an error
    assert not store.exists(key) and store.bucket_exists()


def test_gcs_delete_prefix_keeps_the_neighbouring_folder() -> None:
    folder = college_blob_key(COLLEGE, "booklet", "1")
    client = FakeGcs(
        {
            f"{folder.value}/a.jpg": b"a",
            f"{folder.value}/sheets/v1.pdf": b"b",
            f"{folder.value}0/a.jpg": b"other booklet 10",
        }
    )
    store = GcsBlobStore(client, "b")
    assert store.delete_prefix(folder) == 2
    assert list(client.objects) == [f"{folder.value}0/a.jpg"]


def _event(college: CollegeId, booklet: BookletId, name: str = COMPLETE_MARKER) -> dict[str, str]:
    return {"bucket": "tarn-incoming", "name": f"{incoming_folder(college, booklet)}/{name}"}


def test_the_completion_marker_names_the_booklet() -> None:
    event = parse_storage_event(_event(COLLEGE, BOOKLET))
    assert (event.bucket, event.college_id, event.booklet_id) == ("tarn-incoming", COLLEGE, BOOKLET)
    assert event.folder == f"incoming/{COLLEGE}/{BOOKLET}"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"bucket": "b"},
        {"bucket": "b", "name": f"incoming/{COLLEGE}/{BOOKLET}/001.jpg"},  # not the marker
        {"bucket": "b", "name": f"elsewhere/{COLLEGE}/{BOOKLET}/_complete"},
        {"bucket": "b", "name": f"incoming/not-a-uuid/{BOOKLET}/_complete"},
        {"bucket": "b", "name": f"incoming/{COLLEGE}/{BOOKLET}/sub/_complete"},
        {"bucket": "", "name": f"incoming/{COLLEGE}/{BOOKLET}/_complete"},
    ],
)
def test_events_that_are_not_a_booklet_completion_are_refused(data: dict[str, str]) -> None:
    with pytest.raises(InvariantError):
        parse_storage_event(data)


def test_the_event_source_reads_the_booklets_folder_only() -> None:
    folder = incoming_folder(COLLEGE, BOOKLET)
    other = incoming_folder(COLLEGE, BookletId(uuid4()))
    client = FakeGcs(
        {
            f"{folder}/page10.jpg": fake_image("10"),
            f"{folder}/page2.png": PNG,
            f"{folder}/{COMPLETE_MARKER}": b"",
            f"{folder}/notes.txt": b"no",
            f"{folder}/nested/page3.jpg": fake_image("n"),
            f"{other}/page1.jpg": fake_image("other"),
        }
    )
    source = GcsEventPageSource.from_event(_event(COLLEGE, BOOKLET), client)
    pages = source.pages(COLLEGE, BOOKLET)
    assert [(p.index, p.media_type) for p in pages] == [(0, "image/png"), (1, "image/jpeg")]
    assert pages[1].data == fake_image("10")


def test_the_event_source_refuses_another_college_or_booklet() -> None:
    client = FakeGcs({f"{incoming_folder(COLLEGE, BOOKLET)}/p.jpg": fake_image()})
    source = GcsEventPageSource.from_event(_event(COLLEGE, BOOKLET), client)
    with pytest.raises(TenantViolationError):
        source.pages(CollegeId(UUID(int=99)), BOOKLET)
    with pytest.raises(TenantViolationError):
        source.pages(COLLEGE, BookletId(UUID(int=99)))
    empty = GcsEventPageSource.from_event(_event(COLLEGE, BOOKLET), FakeGcs())
    with pytest.raises(InvariantError, match="no PDF, JPEG or PNG"):
        empty.pages(COLLEGE, BOOKLET)


def test_the_event_source_refuses_another_bucket_and_oversized_folders() -> None:
    folder = incoming_folder(COLLEGE, BOOKLET)
    with pytest.raises(InvariantError, match="another bucket"):
        GcsEventPageSource.from_event(_event(COLLEGE, BOOKLET), FakeGcs(), bucket="tarn-data")
    many = FakeGcs({f"{folder}/p{i}.jpg": fake_image(str(i)) for i in range(5)})
    source = GcsEventPageSource.from_event(
        _event(COLLEGE, BOOKLET), many, bucket="tarn-incoming", max_files=4
    )
    with pytest.raises(UploadTooLargeError, match="At most 4 files"):
        source.pages(COLLEGE, BOOKLET)
    big = FakeGcs({f"{folder}/p1.jpg": fake_image("1") + b"x" * 5000})
    capped = GcsEventPageSource.from_event(_event(COLLEGE, BOOKLET), big, max_bytes=4096)
    with pytest.raises(UploadTooLargeError):
        capped.pages(COLLEGE, BOOKLET)
    assert big.buckets == []  # refused from the listing: nothing was downloaded


# --- which store the settings choose, and S3 -----------------------------------------------------


def test_the_blob_backend_setting_picks_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGcs()
    monkeypatch.setattr(wiring, "make_gcs_client", lambda project: fake)
    assert isinstance(wiring.build_blob_store(Settings(_env_file=None)), MinioBlobStore)
    gcs = wiring.build_blob_store(Settings(_env_file=None, blob_backend="gcs", blob_bucket="tb"))
    assert isinstance(gcs, GcsBlobStore) and fake.buckets == ["tb"]
    s3 = wiring.build_blob_store(
        Settings(
            _env_file=None,
            blob_backend="s3",
            blob_endpoint="s3.ap-south-1.amazonaws.com",
            blob_secure=True,
            blob_region="ap-south-1",
            blob_access_key="AKIAEXAMPLE",
        )
    )
    assert isinstance(s3, MinioBlobStore)  # S3 speaks the MinIO client's protocol
    assert wiring.init_bucket(Settings(_env_file=None, blob_backend="gcs")).startswith("exists")


def test_s3_without_keys_uses_the_instance_role() -> None:
    from minio.credentials.providers import IamAwsProvider

    from tarn_adapters.blob.minio_client import make_client

    client = make_client(
        Settings(
            _env_file=None,
            blob_backend="s3",
            blob_endpoint="s3.ap-south-1.amazonaws.com",
            blob_secure=True,
            blob_access_key="",
        )
    )
    assert isinstance(client._provider, IamAwsProvider)


def test_production_refuses_minio_as_the_object_store() -> None:
    kms = "gcp-kms:projects/p/locations/asia-south1/keyRings/r/cryptoKeys/k"
    smtp = {"mailer": "smtp", "smtp_host": "smtp.example.test", "smtp_from": "t@example.test"}
    with pytest.raises(ValueError, match="BLOB_BACKEND=minio"):
        Settings(
            _env_file=None,
            env="production",
            kms_key_ref=kms,
            secrets_backend="gcp",
            **smtp,  # type: ignore[arg-type]
            **PRODUCTION_CONNECTIONS,
        )
    ok = Settings(
        _env_file=None,
        env="production",
        kms_key_ref=kms,
        secrets_backend="gcp",
        blob_backend="gcs",
        **smtp,  # type: ignore[arg-type]
        **PRODUCTION_CONNECTIONS,
    )
    assert ok.gcp_region == "asia-south1"  # the default region


# --- AWS KMS (configuration-only portability adapter) --------------------------------------------


class FakeAwsKms:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def generate_data_key(self, **kwargs: object) -> dict[str, bytes]:
        self.calls.append(kwargs)
        return {"Plaintext": b"k" * 32, "CiphertextBlob": b"wrapped:" + b"k" * 32}

    def decrypt(self, **kwargs: object) -> dict[str, bytes]:
        self.calls.append(kwargs)
        if kwargs["EncryptionContext"] != self.calls[0]["EncryptionContext"]:
            raise RuntimeError("InvalidCiphertextException")
        blob = kwargs["CiphertextBlob"]
        assert isinstance(blob, bytes)
        return {"Plaintext": blob[len(b"wrapped:") :]}


def test_aws_kms_wraps_the_data_key_with_the_tenant_context() -> None:
    kms = FakeAwsKms()
    keys = AwsKmsKeyManager(kms)
    ref = "aws-kms:arn:aws:kms:ap-south-1:123456789012:key/abc"
    data_key = keys.generate_data_key(ref, b"ctx\x00bytes")
    assert kms.calls[0]["KeyId"] == "arn:aws:kms:ap-south-1:123456789012:key/abc"
    assert kms.calls[0]["KeySpec"] == "AES_256"
    assert keys.unwrap(ref, data_key.wrapped, b"ctx\x00bytes") == data_key.plaintext
    with pytest.raises(ValueError, match="AWS KMS refused"):
        keys.unwrap(ref, data_key.wrapped, b"other tenant")
    with pytest.raises(ValueError):
        keys.generate_data_key("gcp-kms:projects/p", b"ctx")


def test_routing_reaches_aws_by_prefix() -> None:
    routing = RoutingKeyManager(local=None, gcp=None, aws=AwsKmsKeyManager(FakeAwsKms()))
    key = routing.generate_data_key("aws-kms:alias/tarn", b"c")
    assert routing.unwrap("aws-kms:alias/tarn", key.wrapped, b"c") == key.plaintext
    with pytest.raises(ValueError, match="no key manager"):
        RoutingKeyManager(local=None, gcp=None).generate_data_key("aws-kms:alias/tarn", b"c")


# --- the LLM key from Secret Manager (O95) -------------------------------------------------------


class _Secrets:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.asked: list[str] = []

    def get(self, name: str) -> bytes:
        self.asked.append(name)
        return self.value


def _llm_settings(**values: Any) -> Settings:
    return Settings(
        _env_file=None,
        llm_scorer_enabled=True,
        llm_allow_in_development=True,
        secrets_backend="gcp",
        gcp_project="p",
        **values,
    )


def test_the_llm_key_comes_from_secret_manager_when_the_environment_has_none() -> None:
    secrets = _Secrets(b"gsk_paid_key\n")
    built = build_llm_scorer(_llm_settings(), secrets=secrets)
    assert built is not None and secrets.asked == ["groq-api-key"]


def test_the_environment_key_wins_and_a_missing_key_is_an_error() -> None:
    secrets = _Secrets(b"never read")
    assert build_llm_scorer(_llm_settings(groq_api_keys="env-key"), secrets=secrets) is not None
    assert secrets.asked == []
    with pytest.raises(LlmConfigError, match="tarn-groq-api-key"):
        build_llm_scorer(_llm_settings(), secrets=_Secrets(b" "))


def test_production_refuses_several_keys_from_secret_manager() -> None:
    kms = "gcp-kms:projects/p/locations/asia-south1/keyRings/r/cryptoKeys/k"
    settings = Settings(
        _env_file=None,
        env="production",
        kms_key_ref=kms,
        blob_backend="gcs",
        secrets_backend="gcp",
        gcp_project="p",
        llm_scorer_enabled=True,
        mailer="smtp",
        smtp_host="smtp.example.test",
        smtp_from="t@example.test",
        **PRODUCTION_CONNECTIONS,
    )
    with pytest.raises(LlmConfigError, match="several keys"):
        build_llm_scorer(settings, secrets=_Secrets(b"a,b"))
    assert build_llm_scorer(settings, secrets=_Secrets(b"one")) is not None
