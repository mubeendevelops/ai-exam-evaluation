"""MinIO / S3-compatible client helpers. The BlobStore port is implemented on top in P9."""

from typing import Protocol

from minio import Minio

from tarn_adapters.config import Settings


class BucketAdmin(Protocol):
    def bucket_exists(self, bucket_name: str) -> bool: ...

    def make_bucket(self, bucket_name: str) -> None: ...


def make_client(settings: Settings) -> Minio:
    return Minio(
        settings.blob_endpoint,
        access_key=settings.blob_access_key,
        secret_key=settings.blob_secret_key.get_secret_value(),
        secure=settings.blob_secure,
    )


def ensure_bucket(client: BucketAdmin, bucket: str) -> bool:
    """Create ``bucket`` if missing. Returns True when it was created."""
    if client.bucket_exists(bucket):
        return False
    client.make_bucket(bucket)
    return True
