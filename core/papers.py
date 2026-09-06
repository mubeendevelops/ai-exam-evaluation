"""core/papers.py — filtered reads over the shared `generated_papers` table.

TENANCY: there is none here, deliberately — same reasoning as
core/question_bank.py's header. `generated_papers` has no `college_id` and is
not under RLS (migration 008 line 15: "Papers are SHARED across all colleges
→ NO college_id, NO RLS. Same scope as paper_patterns and questions."). No
function in this module takes or filters on a college_id: adding one would
look like isolation while providing none. GET /api/v1/papers still requires a
tenant-scoped connection (get_tenant_conn) — that authenticates the caller,
it does not scope the query, exactly as api/routers/papers.py's header
explains for POST /papers/generate.

ORDERING: `generated_at DESC, paper_id DESC` — `generated_at` is NOT NULL
(migration 008's DEFAULT NOW()), so, as with core/results.py, ties only ever
need the id tiebreak.
"""
from __future__ import annotations

from typing import Any

import core.pagination

#: paper_status enum (migration 008).
VALID_STATUSES = ("draft", "finalized")


def list_papers(
    cur, *, status: str | None = None, pattern_id=None, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """A filtered page of generated papers, newest first."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where, params = _filters(status, pattern_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(
        f"""
        SELECT paper_id, pattern_id, name, status, generated_at, generated_by
        FROM   generated_papers
        {where_sql}
        ORDER  BY generated_at DESC, paper_id DESC
        LIMIT  %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return [
        {
            "paper_id": r[0], "pattern_id": r[1], "name": r[2], "status": r[3],
            "generated_at": r[4], "generated_by": r[5],
        }
        for r in cur.fetchall()
    ]


def count_papers(cur, *, status: str | None = None, pattern_id=None) -> int:
    """Total matching `list_papers`'s filters, ignoring limit/offset."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")

    where, params = _filters(status, pattern_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"SELECT COUNT(*) FROM generated_papers {where_sql}", tuple(params))
    (total,) = cur.fetchone()
    return int(total)


def _filters(status, pattern_id):
    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        where.append("status = %s")
        params.append(status)
    if pattern_id is not None:
        where.append("pattern_id = %s")
        params.append(str(pattern_id))
    return where, params
