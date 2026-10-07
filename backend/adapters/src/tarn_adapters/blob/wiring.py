"""Pick the blob store from the settings: MinIO (development), Cloud Storage (production) or
S3 (the portability adapter, configuration only)."""

from minio import Minio

from tarn_adapters.blob.gcs_store import GcsBlobStore, make_gcs_client
from tarn_adapters.blob.minio_client import ensure_bucket, make_client
from tarn_adapters.blob.minio_store import MinioBlobStore
from tarn_adapters.config import Settings
from tarn_core.ports.storage import BlobStore


def build_blob_store(settings: Settings) -> BlobStore:
    if settings.blob_backend == "gcs":
        return GcsBlobStore(make_gcs_client(settings.gcp_project), settings.blob_bucket)
    # MinIO and S3 speak the same protocol: the S3 set differs only in the settings
    # (endpoint s3.<region>.amazonaws.com, TLS, region, AWS credentials).
    return MinioBlobStore(make_client(settings), settings.blob_bucket)


def bucket_exists(settings: Settings) -> bool:
    """Whether the configured bucket is there (``tarn doctor --services``)."""
    if settings.blob_backend == "gcs":
        return GcsBlobStore(
            make_gcs_client(settings.gcp_project), settings.blob_bucket
        ).bucket_exists()
    client: Minio = make_client(settings)
    return client.bucket_exists(settings.blob_bucket)


def init_bucket(settings: Settings) -> str:
    """``tarn storage-init``: create the development bucket. Cloud Storage and S3 buckets are
    created by Terraform (versioning, encryption, access rules), never by the application."""
    if settings.blob_backend != "minio":
        found = bucket_exists(settings)
        return "exists (managed outside the application)" if found else "MISSING"
    created = ensure_bucket(make_client(settings), settings.blob_bucket)
    return "created" if created else "already exists"
