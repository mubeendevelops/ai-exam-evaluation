"""
storage.py — where content_assets.blob_url / answer_blocks.blob_url /
answers.source_scan_url values come from.

Two modes, chosen by the caller (see load_exam_bank.py --storage):

  - "dummy"  (default): no object storage involved at all. Returns a
    deterministic placeholder string, no network call, no MinIO/boto3
    needed. Lets you populate and query the schema today and swap in real
    blob storage later without re-running the whole import — every dummy
    value is deterministic (based on asset_id), so once you do stand up
    MinIO you can find every placeholder row with:
        SELECT asset_id, blob_url FROM content_assets WHERE blob_url LIKE 'dummy-storage/%';
    and re-run the loader with --storage minio to replace them.

  - "minio": uploads for real to a MinIO (S3-compatible) bucket. Needs
    `pip install boto3` and MINIO_ENDPOINT / MINIO_ACCESS_KEY /
    MINIO_SECRET_KEY / MINIO_BUCKET set (see .env.example). boto3 is only
    imported inside this mode's functions, so dummy mode works even
    without boto3 installed.

Design choice that applies to BOTH modes: `blob_url` in the DB stores a
STABLE reference ("bucket/key" or the dummy equivalent), never a presigned
URL. Presigned URLs expire; a value baked into a DB column has to stay
valid indefinitely. Whatever eventually serves the file to a
browser/frontend should generate a short-lived signed URL at request time
(see `presigned_get_url`, real-storage mode only) and never persist it.
"""
import os
import uuid
from pathlib import Path


def dummy_upload(local_path: str, key_prefix: str, asset_id: str) -> str:
    """No I/O at all — returns a deterministic placeholder 'blob_url' so the
    schema can be populated and queried before object storage exists.
    Deterministic on asset_id (not a random uuid) so re-running the loader
    against the same data always produces the same placeholder."""
    suffix = Path(local_path).suffix
    return f"dummy-storage/{key_prefix.rstrip('/')}/{asset_id}{suffix}"


def get_client():
    import boto3
    from botocore.client import Config

    endpoint = os.environ["MINIO_ENDPOINT"]           # e.g. http://localhost:9000
    access_key = os.environ["MINIO_ACCESS_KEY"]
    secret_key = os.environ["MINIO_SECRET_KEY"]
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",  # MinIO ignores this but boto3 requires it
    )


def ensure_bucket(client, bucket: str):
    existing = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    if bucket not in existing:
        client.create_bucket(Bucket=bucket)


def upload_file(local_path: str, key_prefix: str, content_type: str | None = None) -> str:
    """Uploads local_path to MinIO under a generated key, returns the
    stable 'bucket/key' reference to store in a *_url / blob_url column."""
    client = get_client()
    bucket = os.environ["MINIO_BUCKET"]
    ensure_bucket(client, bucket)

    suffix = Path(local_path).suffix
    key = f"{key_prefix.rstrip('/')}/{uuid.uuid4()}{suffix}"

    extra_args = {"ContentType": content_type} if content_type else {}
    client.upload_file(local_path, bucket, key, ExtraArgs=extra_args)

    return f"{bucket}/{key}"


def presigned_get_url(bucket_and_key: str, expires_in: int = 3600) -> str:
    """Given a stored 'bucket/key' reference, produce a short-lived signed
    URL for the app/API layer to hand to a client. NOT for storing in the DB.
    Only meaningful for real ("minio") blob_url values, not dummy ones."""
    client = get_client()
    bucket, key = bucket_and_key.split("/", 1)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )
