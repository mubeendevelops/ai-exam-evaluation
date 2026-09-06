"""core/uploads.py — the booklet_uploads table, as a library.

Sits in core/ for the same reason core/jobs.py does: the API writes uploads,
the API reads them back to resolve POST /api/v1/evaluate's upload_id, and a
CLI will eventually want to register one too. All of them call these functions
rather than re-deriving the SQL.

CONNECTION / RLS CONTRACT (identical to core/jobs.py and
core/plugins/persistence.py): the CALLER owns the connection and MUST have set
the RLS context on the transaction already. Nothing here sets it.

EXPLICIT college_id PREDICATES: get_upload() filters on college_id in its own
WHERE clause even though the RLS policy filters the same column. Migration 016
gave the deployment a NOBYPASSRLS application role, so the policy runs now —
but Postgres exempts SUPERUSER and BYPASSRLS roles from row-level security
entirely, and pointing PGUSER back at one (it was the .env.example default
until 016) makes this predicate the only thing isolating tenants again, with no
visible symptom. Same argument, in full, at the top of core/jobs.py.
"""
from __future__ import annotations

from typing import Any

import core.pagination

UPLOAD_COLUMNS = (
    "upload_id", "college_id", "blob_url", "filename", "content_type",
    "size_bytes", "storage_mode", "uploaded_at",
)

_SELECT_COLUMNS = ", ".join(UPLOAD_COLUMNS)


def _row_to_dict(row) -> dict[str, Any]:
    upload = dict(zip(UPLOAD_COLUMNS, row))
    upload["upload_id"] = str(upload["upload_id"])
    upload["college_id"] = str(upload["college_id"])
    return upload


def record_upload(
    cur,
    *,
    college_id,
    blob_url: str,
    filename: str,
    size_bytes: int,
    storage_mode: str,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Inserts one booklet_uploads row and returns it.

    Does NOT commit — the caller's transaction owns that, so a failure later
    in the request leaves no upload row pointing at nothing.

    college_id is passed explicitly rather than taken from the RLS GUC: an
    INSERT that reads its tenant from ambient session state writes to the
    wrong college the first time this function is called under a
    platform-admin context, where the policy's WITH CHECK permits anything.
    """
    cur.execute(
        f"""
        INSERT INTO booklet_uploads
            (college_id, blob_url, filename, content_type, size_bytes, storage_mode)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING {_SELECT_COLUMNS}
        """,
        (str(college_id), blob_url, filename, content_type, int(size_bytes), storage_mode),
    )
    return _row_to_dict(cur.fetchone())


def get_upload(cur, *, upload_id, college_id) -> dict[str, Any] | None:
    """Reads one upload scoped to one college, or None.

    None rather than an exception because the caller has to answer 404 for
    both "no such upload" and "another college's upload", and distinguishing
    them at this layer only tempts someone to report the difference — which is
    an existence oracle over other tenants' upload ids.
    """
    cur.execute(
        f"""
        SELECT {_SELECT_COLUMNS}
        FROM booklet_uploads
        WHERE upload_id = %s AND college_id = %s
        """,
        (str(upload_id), str(college_id)),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


#: The predicate an upload's blob_url is "bound" (exam-binding state) by:
#: at least one `answers` row was ingested from it, for ANY exam/student —
#: booklet ingestion is what writes that row (core/booklet_pipeline.py), and
#: it is the only thing that connects an upload to an exam at all (there is
#: no exams.paper_id / no booklets table — CLAUDE_CONTEXT.md §7C). An upload
#: can be bound more than once (a re-ingestion under a different
#: exam/student), so this is existence, not a count.
_BOUND_EXISTS = """
    EXISTS (
        SELECT 1 FROM answers a
        WHERE a.source_scan_url = u.blob_url AND a.college_id = u.college_id
    )
"""


def list_uploads(
    cur, *, college_id, bound: bool | None = None, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """A tenant-scoped, filtered page of uploads, newest first.

    `bound` filters by exam-binding state: True for uploads that have been
    ingested against at least one exam/student, False for uploads still
    sitting unbound (POST /upload happened, nothing has evaluated them yet —
    the review queue for "uploads nobody has acted on").
    """
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where, params = _list_filters(college_id, bound)

    cur.execute(
        f"""
        SELECT u.upload_id, u.college_id, u.blob_url, u.filename, u.content_type,
               u.size_bytes, u.storage_mode, u.uploaded_at, {_BOUND_EXISTS} AS bound
        FROM   booklet_uploads u
        WHERE  {' AND '.join(where)}
        ORDER  BY u.uploaded_at DESC, u.upload_id DESC
        LIMIT  %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    rows = []
    for r in cur.fetchall():
        upload = _row_to_dict(r[:8])
        upload["bound"] = r[8]
        rows.append(upload)
    return rows


def count_uploads(cur, *, college_id, bound: bool | None = None) -> int:
    """Total matching `list_uploads`'s filters, ignoring limit/offset."""
    where, params = _list_filters(college_id, bound)
    cur.execute(
        f"SELECT COUNT(*) FROM booklet_uploads u WHERE {' AND '.join(where)}",
        tuple(params),
    )
    (total,) = cur.fetchone()
    return int(total)


def _list_filters(college_id, bound):
    where = ["u.college_id = %s"]
    params: list[Any] = [str(college_id)]
    if bound is True:
        where.append(_BOUND_EXISTS)
    elif bound is False:
        where.append(f"NOT {_BOUND_EXISTS}")
    return where, params
