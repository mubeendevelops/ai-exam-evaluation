"""Needs the Compose stack (``make up``); run with ``make test-integration``."""

import pytest

from tarn_adapters.blob.minio_client import ensure_bucket, make_client
from tarn_adapters.config import Settings
from tarn_adapters.postgres.health import check_database

pytestmark = pytest.mark.integration


def test_postgres_has_pgvector() -> None:
    status = check_database(Settings().database_url)
    assert status.server_version.startswith("16")
    assert status.pgvector_version is not None


def test_minio_bucket_can_be_ensured() -> None:
    settings = Settings()
    client = make_client(settings)
    ensure_bucket(client, settings.blob_bucket)
    assert client.bucket_exists(settings.blob_bucket)
