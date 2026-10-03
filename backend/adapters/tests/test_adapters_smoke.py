"""Smoke tests for tarn_adapters: settings, device detection, logging, bucket helper."""

import pytest

import tarn_adapters
from tarn_adapters import compute
from tarn_adapters.blob.minio_client import ensure_bucket
from tarn_adapters.config import Settings
from tarn_adapters.logging_setup import configure_logging


def test_version() -> None:
    assert tarn_adapters.__version__ == "0.0.0"


def test_settings_defaults_keep_data_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TARN_DEVICE", raising=False)
    settings = Settings(_env_file=None)
    assert settings.blob_endpoint == "localhost:9000"
    assert settings.cloud_ocr_enabled is False
    assert settings.device == "auto"
    assert "tarn_dev_minio_password" not in repr(settings)  # secret is masked


def test_settings_read_tarn_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARN_DEVICE", "cpu")
    monkeypatch.setenv("TARN_BLOB_BUCKET", "other")
    settings = Settings(_env_file=None)
    assert (settings.device, settings.blob_bucket) == ("cpu", "other")


def test_cpu_preference_never_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise AssertionError("must not probe")

    monkeypatch.setattr(compute, "_torch_cuda", boom)
    assert compute.detect_device("cpu").kind == "cpu"


def test_auto_uses_cuda_when_torch_sees_it(monkeypatch: pytest.MonkeyPatch) -> None:
    gpu = compute.DeviceInfo("cuda", "GTX 1650", 4096, "CUDA via torch")
    monkeypatch.setattr(compute, "_torch_cuda", lambda: gpu)
    info = compute.detect_device("auto")
    assert info is gpu
    assert info.torch_device == "cuda:0"


def test_auto_falls_back_to_cpu_and_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compute, "_torch_cuda", lambda: None)
    monkeypatch.setattr(compute, "_nvidia_smi_gpu", lambda: "GTX 1650")
    info = compute.detect_device("auto")
    assert info.kind == "cpu"
    assert "GTX 1650" in info.detail

    monkeypatch.setattr(compute, "_nvidia_smi_gpu", lambda: None)
    assert compute.detect_device("auto").detail == "no CUDA device found"


def test_explicit_cuda_without_cuda_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compute, "_torch_cuda", lambda: None)
    with pytest.raises(RuntimeError, match="cuda"):
        compute.detect_device("cuda")


def test_configure_logging_runs() -> None:
    configure_logging("INFO", json=True)


class _FakeBuckets:
    def __init__(self, existing: set[str]) -> None:
        self.buckets = set(existing)

    def bucket_exists(self, bucket_name: str) -> bool:
        return bucket_name in self.buckets

    def make_bucket(self, bucket_name: str) -> None:
        self.buckets.add(bucket_name)


def test_ensure_bucket_is_idempotent() -> None:
    client = _FakeBuckets(set())
    assert ensure_bucket(client, "tarn") is True
    assert ensure_bucket(client, "tarn") is False
    assert client.buckets == {"tarn"}
