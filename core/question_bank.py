"""core/question_bank.py — filtered reads over the shared question bank.

WHY THIS EXISTS AND WHY IT IS IN core/ RATHER THAN IN api/services/

scripts/review_question.py already owns every WRITE against a question's
status (the two-gate flow) plus two reads shaped for the CLI:
`list_draft_questions` (drafts only, oldest first — a review queue) and
`show_question` (one question in full). Neither answers "give me this bank's
questions filtered by status/style/source", which is what GET
/api/v1/questions needs.

That query lands here, not in api/services/questions.py, because the service
layer in this API is translation only (see that file's header) and because
CLAUDE_CONTEXT.md §11 rule 1 says both front doors call the same functions.
A `--status` flag on review_question.py would want exactly this function; it
should find it already written rather than a second, subtly-different SELECT
living inside the web layer.

TENANCY: there is none here, deliberately. questions and everything hanging
off it are SHARED across colleges — migration 003 line 5 ("Question schema
(12 tables) stays UNCHANGED — shared question bank, no tenant column,
readable/usable by every college") and line 341 name these tables explicitly
as excluded from RLS. So no function in this module takes or filters on a
college_id: adding one would look like isolation while providing none, which
is worse than the honest absence. If the bank is ever tenanted, it is a
migration plus a change here, not a predicate bolted on at a call site.
"""
from __future__ import annotations

from typing import Any

import core.pagination

#: Every value of the question_status enum (migration 001 line 30). Callers
#: validate against this rather than hardcoding a subset, so a new status
#: added by a migration is a one-line change here.
VALID_STATUSES = ("draft", "confirmed", "rejected", "live", "superseded")

#: question_style enum (migration 002 line 32). Same list as
#: core/llm.py::VALID_QUESTION_STYLES, which is the generation side of it.
VALID_STYLES = ("long", "short", "one_word", "mcq")

#: question_source_type enum (migration 002 line 28).
VALID_SOURCE_TYPES = ("sentence", "paragraph", "diagram", "table", "formula", "manual")

#: Hard ceiling on rows per call. The bank is unbounded and shared, so an
#: un-capped LIMIT is a way for one request to pull the entire question bank
#: into memory (twice: once in psycopg2, once in the response model).
#: Re-exported from core/pagination.py, which is now the single source of
#: truth for this number across every list_*() in core/ — kept as a module
#: attribute here too since this was the first module to define it and other
#: code may still read `core.question_bank.MAX_LIMIT`.
MAX_LIMIT = core.pagination.MAX_LIMIT


def list_questions(
    cur,
    *,
    status: str | None = None,
    style: str | None = None,
    source_type: str | None = None,
    is_ai_generated: bool | None = None,
    paper_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Filtered page of the question bank, newest first.

    Every filter is optional and ANDed. None means "do not filter on this",
    which is why each predicate is appended conditionally rather than written
    as `(%s IS NULL OR col = %s)` — the latter reads tidier but defeats the
    partial indexes on status/style.

    `paper_id` filters to the questions actually assigned to one generated
    paper. It is the nearest available answer to "questions for exam X":
    there is no link from a question to an exam anywhere in this schema
    (exams has no paper_id column — CLAUDE_CONTEXT.md §7C's open gap), so a
    question reaches an exam only through
    paper_questions -> paper_sections -> generated_papers. Filtering on the
    paper is real; filtering on an exam would have to be invented.

    Ordering is created_at DESC, question_id DESC. The tiebreak is not
    decoration: created_at is only now()-precise, bulk generation writes
    several rows inside one transaction with identical timestamps, and
    without a stable tiebreak LIMIT/OFFSET paging can show one row twice and
    skip another.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    if style is not None and style not in VALID_STYLES:
        raise ValueError(f"style must be one of {VALID_STYLES}, got {style!r}")
    if source_type is not None and source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"source_type must be one of {VALID_SOURCE_TYPES}, got {source_type!r}"
        )
    # CLAMPED, not rejected — a limit above MAX_LIMIT is a client asking for
    # more than one page hands back, not a malformed request. See
    # core/pagination.py::clamp_limit. offset has no upper bound to clamp to,
    # only a lower one.
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where: list[str] = []
    params: list[Any] = []

    if status is not None:
        where.append("q.status = %s")
        params.append(status)
    if style is not None:
        where.append("q.style = %s")
        params.append(style)
    if source_type is not None:
        where.append("q.source_type = %s")
        params.append(source_type)
    if is_ai_generated is not None:
        where.append("q.is_ai_generated IS NOT DISTINCT FROM %s")
        params.append(is_ai_generated)
    if paper_id is not None:
        where.append("""
            q.question_id IN (
                SELECT pq.question_id
                FROM   paper_questions pq
                JOIN   paper_sections  psec ON psec.paper_section_id = pq.paper_section_id
                WHERE  psec.paper_id = %s
            )
        """)
        params.append(paper_id)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"""
        SELECT q.question_id, q.content, q.status, q.style, q.marks_max,
               q.source_type, q.source_id, q.is_ai_generated,
               q.question_group_id, q.parent_question_id, q.created_at
        FROM   questions q
        {where_sql}
        ORDER  BY q.created_at DESC, q.question_id DESC
        LIMIT  %s OFFSET %s
    """, (*params, limit, offset))

    return [
        {
            "question_id": r[0], "content": r[1], "status": r[2], "style": r[3],
            "marks_max": r[4], "source_type": r[5], "source_id": r[6],
            "is_ai_generated": r[7], "question_group_id": r[8],
            "parent_question_id": r[9], "created_at": r[10],
        }
        for r in cur.fetchall()
    ]


def count_questions(
    cur,
    *,
    status: str | None = None,
    style: str | None = None,
    source_type: str | None = None,
    is_ai_generated: bool | None = None,
    paper_id: str | None = None,
) -> int:
    """Total matching `list_questions`'s filters, ignoring limit/offset.

    Separate from list_questions (rather than a window function inside it) so
    a caller that only wants "how many drafts are waiting?" does not have to
    fetch a page of rows to find out.
    """
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    if style is not None and style not in VALID_STYLES:
        raise ValueError(f"style must be one of {VALID_STYLES}, got {style!r}")
    if source_type is not None and source_type not in VALID_SOURCE_TYPES:
        raise ValueError(
            f"source_type must be one of {VALID_SOURCE_TYPES}, got {source_type!r}"
        )

    where: list[str] = []
    params: list[Any] = []

    if status is not None:
        where.append("q.status = %s")
        params.append(status)
    if style is not None:
        where.append("q.style = %s")
        params.append(style)
    if source_type is not None:
        where.append("q.source_type = %s")
        params.append(source_type)
    if is_ai_generated is not None:
        where.append("q.is_ai_generated IS NOT DISTINCT FROM %s")
        params.append(is_ai_generated)
    if paper_id is not None:
        where.append("""
            q.question_id IN (
                SELECT pq.question_id
                FROM   paper_questions pq
                JOIN   paper_sections  psec ON psec.paper_section_id = pq.paper_section_id
                WHERE  psec.paper_id = %s
            )
        """)
        params.append(paper_id)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cur.execute(f"SELECT COUNT(*) FROM questions q {where_sql}", tuple(params))
    (total,) = cur.fetchone()
    return int(total)
