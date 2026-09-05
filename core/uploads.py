"""core/uploads.py — the booklet_uploads table, as a library.

Sits in core/ for the same reason core/jobs.py does: the API writes uploads,
the API reads them back to resolve POST /api/v1/evaluate's upload_id, and a
CLI will eventually want to register one too. All of them call these functions
rather than re-deriving the SQL.

CONNECTION / RLS CONTRACT (identical to core/jobs.py and
core/plugins/persistence.py): the CALLER owns the connection and MUST have set
the RLS context on the transaction already. Nothing here sets it.

EXPLICIT college_id PREDICATES: get_upload() filters on college_id in its own
WHERE clause even though the RLS policy filters the same column, because
Postgres exempts SUPERUSER and BYPASSRLS roles from row-level security
entirely — with PGUSER=postgres (the .env.example default) migration 015's
policies are inert and this predicate is the only thing isolating tenants.
Same argument, in full, at the top of core/jobs.py.
"""
from __future__ import annotations

from typing import Any

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
