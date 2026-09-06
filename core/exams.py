"""core/exams.py — filtered reads over one college's exams.

TENANCY: `exams` is one of migration 003's seven original RLS-protected
answer-schema tables (CLAUDE_CONTEXT.md §5/§6). Both layers apply here, for
the same reason core/jobs.py's header argues at length: RLS only actually
runs under the NOSUPERUSER/NOBYPASSRLS role migration 016 creates, so every
function here ALSO takes `college_id` and puts it in the WHERE clause
explicitly — that predicate is the only thing isolating tenants the moment
anyone points PGUSER back at a superuser.

ORDERING: `exams` has no `created_at` column (migration 001 defines only
`conducted_at`, which is nullable — a draft exam may not have a date yet).
`conducted_at DESC NULLS LAST, exam_id DESC` is still fully deterministic:
NULLS LAST places every undated exam at the same end every time, and exam_id
breaks any remaining tie (including two NULLs), so LIMIT/OFFSET paging cannot
skip or repeat a row.
"""
from __future__ import annotations

from typing import Any

import core.pagination

#: exam_status enum (migration 001 line 32).
VALID_STATUSES = ("draft", "live", "closed")


def list_exams(
    cur, *, college_id, status: str | None = None, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """A tenant-scoped, filtered page of exams, newest-dated first."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where = ["college_id = %s"]
    params: list[Any] = [str(college_id)]
    if status is not None:
        where.append("status = %s")
        params.append(status)

    cur.execute(
        f"""
        SELECT exam_id, name, conducted_at, status
        FROM   exams
        WHERE  {' AND '.join(where)}
        ORDER  BY conducted_at DESC NULLS LAST, exam_id DESC
        LIMIT  %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return [
        {"exam_id": r[0], "name": r[1], "conducted_at": r[2], "status": r[3]}
        for r in cur.fetchall()
    ]


def count_exams(cur, *, college_id, status: str | None = None) -> int:
    """Total matching `list_exams`'s filters, ignoring limit/offset."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")

    where = ["college_id = %s"]
    params: list[Any] = [str(college_id)]
    if status is not None:
        where.append("status = %s")
        params.append(status)

    cur.execute(f"SELECT COUNT(*) FROM exams WHERE {' AND '.join(where)}", tuple(params))
    (total,) = cur.fetchone()
    return int(total)
