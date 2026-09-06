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
import shutil
import tempfile
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
    """Builds a fresh boto3 S3 client pointed at MinIO, from MINIO_* env
    vars. No caching/pooling — a new client per call, same stateless-factory
    shape as core/db.py::get_connection(). Requires boto3 (only imported
    here, not at module load, so dummy mode works without it installed)."""
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


def ensure_bucket(client, bucket: str) -> None:
    """Creates `bucket` on the given S3 client if it doesn't already exist."""
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


def get_object_bytes(bucket_and_key: str) -> bytes:
    """Downloads a stored 'bucket/key' reference's raw bytes directly (no
    presigned URL, no HTTP round-trip through a browser) — for server-side
    processing that needs the file's content, e.g. running OCR/extraction
    over a scanned image. Real-storage mode only; a "dummy-storage/..."
    value has no object behind it to download."""
    client = get_client()
    bucket, key = bucket_and_key.split("/", 1)
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


# ─────────────────────── retrieval, and the dummy store ─────────────────────
#
# Everything above this line is WRITE-side. Ingestion needs the read side: the
# API stores an uploaded booklet in one process and a worker rasterizes it in
# another, minutes later, so the bytes have to be fetchable from the stable
# "bucket/key" ref alone.
#
# That works for "minio" (get_object_bytes). It did NOT work for "dummy",
# which by design does no I/O at all — the ref is a placeholder with nothing
# behind it. dummy_store() below is the narrow fix: a dummy ref whose bytes
# are kept on local disk, so the upload -> ingest -> evaluate loop runs on a
# bare checkout with no MinIO. It is OPT-IN and dummy_upload() is unchanged,
# because the loaders that call dummy_upload() are populating placeholder rows
# for content nobody will ever fetch back, and making them all write files
# would be a cost with no reader.

#: Where dummy_store() keeps its bytes. Under the system temp dir by default:
#: dummy mode is a development/test convenience, and a developer who wipes
#: /tmp has lost nothing that was not already labelled a placeholder.
DUMMY_STORAGE_ROOT = os.environ.get(
    "DUMMY_STORAGE_ROOT",
    os.path.join(tempfile.gettempdir(), "ai-eval-dummy-storage"),
)

DUMMY_PREFIX = "dummy-storage/"


def is_dummy_ref(bucket_and_key: str) -> bool:
    return str(bucket_and_key).startswith(DUMMY_PREFIX)


def _dummy_local_path(bucket_and_key: str) -> str:
    """Maps a 'dummy-storage/<prefix>/<id>.<ext>' ref onto a local file path.

    The ref's own path is reused verbatim under the root, so the mapping is
    reversible by inspection — `ls $DUMMY_STORAGE_ROOT/booklets/` shows
    exactly the keys the DB holds.
    """
    relative = str(bucket_and_key)[len(DUMMY_PREFIX):]
    # Refuse traversal: a blob_url is data from a DB column, and this function
    # turns it into a filesystem path.
    if not relative or relative.startswith("/") or ".." in relative.split("/"):
        raise ValueError(f"Refusing to resolve suspicious dummy ref {bucket_and_key!r}.")
    return os.path.join(DUMMY_STORAGE_ROOT, relative)


def dummy_store(local_path: str, key_prefix: str, asset_id: str) -> str:
    """dummy_upload(), but the bytes are actually kept, under
    DUMMY_STORAGE_ROOT, so fetch_to_path() can read them back later.

    Returns the same deterministic "dummy-storage/..." ref shape, so nothing
    downstream can tell the two apart — which is the point: a dummy ref means
    "no real object storage", not "a different kind of reference".
    """
    ref = dummy_upload(local_path, key_prefix, asset_id)
    destination = _dummy_local_path(ref)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copyfile(local_path, destination)
    return ref


def fetch_to_path(bucket_and_key: str, destination: str) -> str:
    """Materializes a stored ref as a local file at `destination`.

    The read side of store_file(). Both modes are handled here rather than by
    the caller so that "which storage mode is this ref?" is answered in one
    place — a ref carries its own answer in its prefix, and a worker that had
    to be told would be a worker that can be told wrong.
    """
    if is_dummy_ref(bucket_and_key):
        source = _dummy_local_path(bucket_and_key)
        if not os.path.exists(source):
            raise FileNotFoundError(
                f"No local bytes for dummy ref {bucket_and_key!r} (looked in "
                f"{source}). Dummy refs written by dummy_upload() have NOTHING "
                f"behind them — only dummy_store() keeps the file. If this is "
                f"an API upload, check STORAGE_MODE and DUMMY_STORAGE_ROOT "
                f"match between the API process and the worker process."
            )
        shutil.copyfile(source, destination)
        return destination

    with open(destination, "wb") as out:
        out.write(get_object_bytes(bucket_and_key))
    return destination


def store_file(local_path: str, key_prefix: str, *, storage_mode: str,
               asset_id: str | None = None, content_type: str | None = None) -> str:
    """Stores a file in whichever backend `storage_mode` names, and returns the
    stable ref. The write side that pairs with fetch_to_path().

    'dummy' routes to dummy_store(), NOT dummy_upload(): a caller that asks
    for a ref it can fetch back has to get one.
    """
    if storage_mode == "dummy":
        return dummy_store(local_path, key_prefix, asset_id or str(uuid.uuid4()))
    if storage_mode == "minio":
        return upload_file(local_path, key_prefix, content_type=content_type)
    raise ValueError(
        f"Unknown storage_mode {storage_mode!r} — expected 'dummy' or 'minio'."
    )
