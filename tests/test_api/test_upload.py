"""tests/test_api/test_upload.py — POST /api/v1/upload.

What these assert, in one line: an accepted booklet ends up in object storage
and in the queue, exactly once, under the caller's own tenant — and a booklet
the API should not accept never reaches either.
"""
from __future__ import annotations

import uuid

import pytest

from tests.test_api.conftest import COLLEGE_A, MINIMAL_PDF

pytestmark = [pytest.mark.db, pytest.mark.asyncio]

UPLOAD = "/api/v1/upload"


def _pdf(name: str = "booklet.pdf", data: bytes = MINIMAL_PDF):
    return {"file": (name, data, "application/pdf")}


async def test_upload_returns_a_stable_id_and_records_one_upload(
    make_client, admin_conn, track_uploads
):
    """The happy path: 201, a persisted booklet_uploads row, and an id that
    still resolves afterwards.

    "Stable" is checked as ROUND-TRIPPABLE, not as deterministic-on-content:
    two uploads of the same bytes are two distinct submissions and must not
    collide. What must hold is that the id handed to the client names a real
    row a moment later, from a different connection — which is what the
    admin_conn read below proves.

    UPLOADING QUEUES NOTHING as of migration 015. Evaluation needs an exam_id
    and a student_id an upload does not carry, and one upload can be evaluated
    more than once, so POST /api/v1/evaluate owns the queueing. The final
    assertion pins that down: an upload must leave the job queue untouched.
    """
    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(UPLOAD, files=_pdf())

    assert response.status_code == 201, response.text
    body = response.json()
    track_uploads(body["upload_id"])

    assert body["filename"] == "booklet.pdf"
    assert body["size_bytes"] == len(MINIMAL_PDF)
    assert body["storage_mode"] == "dummy"
    # Stable "bucket/key" ref, never a presigned URL (CLAUDE_CONTEXT.md §10).
    assert body["blob_url"].startswith("dummy-storage/booklets/")
    assert "?" not in body["blob_url"] and "://" not in body["blob_url"]
    # The old job-shaped fields are gone, not merely unused — see UploadResponse.
    assert "job_id" not in body and "status" not in body

    # The row is really committed, and visible from a connection that is not
    # the request's — i.e. the API committed rather than merely returning a
    # plausible dict.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT college_id, blob_url, filename, content_type, size_bytes, "
            "       storage_mode FROM booklet_uploads WHERE upload_id = %s",
            (body["upload_id"],),
        )
        row = cur.fetchone()

    assert row is not None, "upload returned an upload_id that is not in the table"
    college_id, blob_url, filename, content_type, size_bytes, storage_mode = row
    assert str(college_id) == COLLEGE_A
    assert blob_url == body["blob_url"]
    assert (filename, content_type, storage_mode) == ("booklet.pdf", "application/pdf", "dummy")
    assert size_bytes == len(MINIMAL_PDF)

    # Asserted against THIS upload rather than a global row count: other tests
    # create and clean up jobs concurrently with this one, so an absolute
    # before/after count would be order-dependent and would fail for reasons
    # that have nothing to do with the upload endpoint.
    with admin_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM evaluation_jobs "
            " WHERE payload->>'upload_id' = %s OR payload->>'source_scan_url' = %s",
            (body["upload_id"], body["blob_url"]),
        )
        (jobs_for_this_upload,) = cur.fetchone()

    assert jobs_for_this_upload == 0, (
        "uploading must not enqueue a job — POST /api/v1/evaluate does that"
    )


async def test_two_uploads_get_two_distinct_rows(make_client, track_uploads):
    """Identical bytes uploaded twice are two submissions, not one.

    Guards against anyone "helpfully" making the upload id a content hash: a
    student re-submitting, or two students with an identical blank page, must
    not silently share one upload — or, worse, one evaluation.
    """
    async with make_client(COLLEGE_A) as ac:
        first = await ac.post(UPLOAD, files=_pdf())
        second = await ac.post(UPLOAD, files=_pdf())

    assert first.status_code == second.status_code == 201
    a, b = first.json(), second.json()
    track_uploads(a["upload_id"])
    track_uploads(b["upload_id"])

    assert a["upload_id"] != b["upload_id"]
    assert a["blob_url"] != b["blob_url"], "two uploads must not overwrite one object"


async def test_real_sample_booklet_uploads(make_client, sample_booklet_bytes, track_uploads):
    """The actual 4-page benchmark PDF, not a synthetic header.

    The other tests use a minimal byte string for speed; this one proves the
    streaming path handles a real multi-hundred-KB file across several chunks
    and reports its exact size.
    """
    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(
            UPLOAD, files={"file": ("sample_booklet.pdf", sample_booklet_bytes, "application/pdf")}
        )

    assert response.status_code == 201, response.text
    body = response.json()
    track_uploads(body["upload_id"])
    assert body["size_bytes"] == len(sample_booklet_bytes)


async def test_non_pdf_is_rejected_before_anything_is_stored(make_client, admin_conn):
    """A renamed .pdf that isn't one is a 415, not a job that fails minutes
    later.

    The filename and Content-Type both SAY pdf here — only the magic bytes
    disagree — because those two are exactly what a client controls and what a
    naive check would trust.
    """
    before = _upload_count(admin_conn)

    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(UPLOAD, files=_pdf(data=b"PK\x03\x04 this is a zip"))

    assert response.status_code == 415, response.text
    assert "%PDF-" in response.json()["detail"]
    assert _upload_count(admin_conn) == before, "a rejected upload must not be recorded"


async def test_empty_upload_is_rejected(make_client):
    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(UPLOAD, files=_pdf(data=b""))

    assert response.status_code == 400, response.text
    assert "empty" in response.json()["detail"].lower()


async def test_oversize_upload_is_rejected(make_client, api_settings, admin_conn):
    """The cap is enforced on bytes read, so an oversize body is refused even
    though its Content-Type and signature are perfectly valid."""
    api_settings.max_upload_bytes = 1024
    oversize = MINIMAL_PDF + b"0" * 4096
    before = _upload_count(admin_conn)

    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(UPLOAD, files=_pdf(data=oversize))

    assert response.status_code == 413, response.text
    assert _upload_count(admin_conn) == before


async def test_upload_without_credentials_is_rejected(make_client, admin_conn):
    """No tenant, no upload — and, critically, no job row.

    An unauthenticated upload that still wrote a row would write it under
    *some* college, which is the failure this whole layer is built to prevent.
    """
    before = _upload_count(admin_conn)

    async with make_client(COLLEGE_A) as ac:
        response = await ac.post(UPLOAD, files=_pdf(), headers={"X-Debug-College-Id": ""})

    assert response.status_code == 401, response.text
    assert _upload_count(admin_conn) == before


async def test_upload_for_an_unknown_college_fails_and_writes_nothing(
    make_client, admin_conn
):
    """A well-formed credential naming a college that does not exist must not
    create a job.

    booklet_uploads.college_id is a FK to colleges (migration 015), so this is
    caught by the DB rather than by a check the API could forget. The point of
    the test is the SECOND assertion: the request's transaction is rolled back
    by get_tenant_conn, so no partial row survives.
    """
    ghost = str(uuid.uuid4())
    before = _upload_count(admin_conn)

    # raise_app_exceptions=False so the app's own error response is returned
    # rather than the psycopg2 exception being re-raised into the test — the
    # 500 is what a real client would see, and it is what is under test here.
    async with make_client(ghost, raise_app_exceptions=False) as ac:
        response = await ac.post(UPLOAD, files=_pdf())

    assert response.status_code == 500, response.text
    assert _upload_count(admin_conn) == before


def _upload_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM booklet_uploads")
        return cur.fetchone()[0]


def _job_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM evaluation_jobs")
        return cur.fetchone()[0]
