"""api/routers/upload.py — POST /api/v1/upload: accept a booklet PDF; GET
/api/v1/uploads: list this college's uploaded booklets.

The endpoint does three things and deliberately not a fourth:

  1. validates and streams the upload to disk (bounded),
  2. stores it via core/storage.py, getting back a stable "bucket/key" ref,
  3. records ONE booklet_uploads row holding that ref,

and it does NOT evaluate anything, or queue anything. POST /api/v1/evaluate is
what queues work, because evaluating needs an exam_id and a student_id that an
upload does not carry — and because one uploaded booklet can be evaluated more
than once (a corrected reference answer, a re-ingestion, a different binding).

Until migration 015 this endpoint enqueued an evaluation_jobs row and returned
its id as `upload_id`. That stopped being tenable the moment /evaluate existed:
the upload's own job row had no worker that handled it, so it would sit
'queued' forever while GET /jobs/{id} told the client it was "waiting for a
worker to pick this up".

Order matters: the file is stored BEFORE the row is inserted, and the insert
shares the request's transaction. So the two failure modes are
  - store succeeds, insert fails  -> an orphan object in the bucket, no row.
    Wasted bytes, nothing incorrect. Recoverable by a sweep over unreferenced
    keys.
  - store fails                   -> no object, no row, a 5xx to the client.
Never the reverse (a row pointing at a file that was never written), which
would be an upload guaranteed to fail at evaluation time for a reason the
client could not have acted on at request time.
"""
from __future__ import annotations

import os
import tempfile
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status

import core.storage
import core.uploads
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, require_college_user
from api.deps.pagination import Pagination, get_pagination
from api.deps.quota import rate_limit
from api.schemas.pagination import Page
from api.schemas.upload import UploadResponse, UploadSummary, upload_to_summary
from api.settings import Settings, get_settings

router = APIRouter(prefix="/api/v1", tags=["upload"])


@router.get(
    "/uploads",
    response_model=Page[UploadSummary],
    summary="List and filter this college's uploaded booklets",
)
def list_uploads(
    bound: bool | None = Query(
        default=None,
        description="Exam-binding state: true = ingested against at least "
                    "one exam/student; false = uploaded but not yet acted "
                    "on. Null does not filter. See core/uploads.py.",
    ),
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[UploadSummary]:
    """This college's uploaded booklets, newest first — the "unbound
    uploads" review queue is `?bound=false`."""
    with conn.cursor() as cur:
        uploads = core.uploads.list_uploads(
            cur, college_id=user.college_id, bound=bound,
            limit=pagination.limit, offset=pagination.offset,
        )
        total = core.uploads.count_uploads(cur, college_id=user.college_id, bound=bound)

    return Page(
        items=[upload_to_summary(u) for u in uploads],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )

#: PDF files begin with this signature. Checked because a filename extension
#: and a client-supplied Content-Type are both trivially wrong — the worker
#: would otherwise discover it minutes later inside pypdfium2, as a failed job
#: instead of a 400.
PDF_MAGIC = b"%PDF-"

#: Streaming chunk size. The body is written to a temp file in pieces rather
#: than read into memory, because core/storage.py::upload_file takes a PATH
#: (it is shared with the CLI, which always has one) and because a 50 MB
#: request should not become 50 MB of resident process memory per concurrent
#: upload.
CHUNK_BYTES = 1024 * 1024


def _settings(request: Request) -> Settings:
    """Prefers the settings the app was built with, so a test app created via
    create_app(Settings(...)) is honoured instead of the process-wide cache."""
    return getattr(request.app.state, "settings", None) or get_settings()


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a scanned answer booklet (PDF)",
    responses={
        429: {
            "description": (
                "This college's request rate for this endpoint is at its "
                "configured cap (UPLOAD_RATE_LIMIT_*). `Retry-After` says "
                "how long to wait — see api/deps/quota.py."
            )
        },
    },
    dependencies=[Depends(rate_limit("upload"))],
)
def upload_booklet(
    request: Request,
    file: UploadFile = File(..., description="The scanned answer booklet, as a PDF."),
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
) -> UploadResponse:
    """Stores the booklet and records it. Returns 201 with the upload_id.

    The tenant comes from the authenticated user via get_tenant_conn — never
    from the request — so a caller cannot upload into another college by any
    means available to them.
    """
    settings = _settings(request)

    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The uploaded part has no filename.",
        )

    tmp_path, size_bytes = _stream_to_tempfile(file, settings.max_upload_bytes)
    try:
        blob_url = _store(tmp_path, settings.storage_mode)
    finally:
        # The temp file is ours regardless of how storage went; the object
        # store (or the dummy path) now owns the only copy we care about.
        os.unlink(tmp_path)

    with conn.cursor() as cur:
        upload = core.uploads.record_upload(
            cur,
            college_id=user.college_id,
            blob_url=blob_url,
            filename=file.filename,
            content_type=file.content_type,
            size_bytes=size_bytes,
            storage_mode=settings.storage_mode,
        )
    # No commit here — get_tenant_conn commits when the request completes
    # cleanly and rolls back on any exception, so a later failure in this
    # handler cannot leave an upload row behind.

    return UploadResponse(
        upload_id=upload["upload_id"],
        filename=upload["filename"],
        size_bytes=upload["size_bytes"],
        blob_url=upload["blob_url"],
        storage_mode=upload["storage_mode"],
        uploaded_at=upload["uploaded_at"],
    )


def _stream_to_tempfile(file: UploadFile, max_bytes: int) -> tuple[str, int]:
    """Writes the upload to a temp file in chunks, enforcing the size cap and
    the PDF signature. Returns (path, bytes_written).

    The cap is enforced on BYTES ACTUALLY READ, not on Content-Length: that
    header is client-supplied, so trusting it means the cap can be bypassed by
    lying about it. Reading is aborted the moment the limit is exceeded rather
    than after the whole body has landed on disk.

    Cleans up its own temp file on every failure path — a rejected upload must
    not leave anything behind, or a stream of 400s becomes a disk-space
    incident.
    """
    fd, tmp_path = tempfile.mkstemp(prefix="booklet-upload-", suffix=".pdf")
    size = 0
    first_chunk = True
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = file.file.read(CHUNK_BYTES)
                if not chunk:
                    break

                if first_chunk:
                    first_chunk = False
                    if not chunk.startswith(PDF_MAGIC):
                        raise HTTPException(
                            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail=(
                                "Uploaded file is not a PDF: it does not begin "
                                "with the %PDF- signature. Booklet ingestion "
                                "rasterizes pages with pypdfium2 "
                                "(core/booklet_ingest.py) and accepts PDFs "
                                "only. Checked here rather than trusting the "
                                "filename or Content-Type, both of which the "
                                "client controls."
                            ),
                        )

                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"Upload exceeds the {max_bytes} byte limit "
                            f"(API_MAX_UPLOAD_BYTES / max_upload_bytes)."
                        ),
                    )
                out.write(chunk)

        if size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )
    except Exception:
        os.unlink(tmp_path)
        raise

    return tmp_path, size


def _store(tmp_path: str, storage_mode: str) -> str:
    """Hands the file to core/storage.py and returns the stable ref.

    Both modes go through core/storage.py rather than the API talking to boto3
    itself — same reason every script does: the "bucket/key, never a presigned
    URL" rule (CLAUDE_CONTEXT.md §10) is enforced in that one module, and a
    second uploader would be a second place to get it wrong.
    """
    try:
        # store_file, not dummy_upload/upload_file directly: this booklet is
        # FETCHED BACK, minutes later and in another process, by the
        # booklet_ingest job that rasterizes it. dummy_upload returns a
        # placeholder with no bytes behind it, which would make the whole
        # upload -> ingest -> evaluate loop impossible in dummy mode — i.e. in
        # exactly the mode a developer runs it in. store_file's dummy branch
        # keeps the file under DUMMY_STORAGE_ROOT; the ref it returns is
        # identical in shape, so nothing downstream can tell the difference.
        return core.storage.store_file(
            tmp_path, "booklets",
            storage_mode=storage_mode,
            asset_id=str(uuid.uuid4()),
            content_type="application/pdf",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"{exc} Set STORAGE_MODE to 'dummy' or 'minio' (see "
                f"api/settings.py and .env.example)."
            ),
        )
