"""core/results.py — filtered reads over one college's answers, summarized
for the GET /api/v1/results list endpoint.

WHY THIS IS NOT api/services/evaluation.py::get_answer_report, SUMMARIZED.
That function returns one answer's FULL report — every ledger row, every
review, every region component — which is exactly what a list of dozens or
hundreds of answers must not carry per row. This module answers a different
question: "which answers match these filters, and what is their CURRENT
score and review state at a glance" — one row per answer, cheap to page
through. A client that needs the full report for one row still calls
GET /results/{answer_id}.

TENANCY: `answers` is one of migration 003's seven RLS-protected
answer-schema tables. Same belt-and-braces argument as core/jobs.py,
core/exams.py, core/students.py: `college_id` is filtered explicitly here too.

`needs_review` IS NOT A COLUMN ON `answers`. It lives on `answer_blocks`
(migration 013), per REGION — a layout classification confidence flag, not an
answer-level judgement. An answer "needs review" for this filter's purposes
when AT LEAST ONE of its blocks does; that is computed with an EXISTS
subquery rather than joined and grouped, so an answer with several flagged
blocks still appears exactly once.

ORDERING: `submitted_at DESC, answer_id DESC` — `submitted_at` is NOT NULL
(migration 001's DEFAULT now()), so ties only ever need the id tiebreak, the
same shape core/question_bank.py uses.
"""
from __future__ import annotations

from typing import Any

import core.pagination

#: answer_status enum (migration 001 line 34).
VALID_STATUSES = ("pending_evaluation", "ai_scored", "sme_reviewed", "finalized", "flagged")

_NEEDS_REVIEW_EXISTS = """
    EXISTS (
        SELECT 1 FROM answer_blocks ab
        WHERE ab.answer_id = a.answer_id AND ab.needs_review
    )
"""


def list_results(
    cur, *, college_id, exam_id=None, student_id=None, status: str | None = None,
    needs_review: bool | None = None, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """A tenant-scoped, filtered page of answers with their current score."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where, params = _filters(college_id, exam_id, student_id, status, needs_review)

    cur.execute(
        f"""
        SELECT a.answer_id, a.question_id, a.student_id, a.exam_id, a.status,
               a.submitted_at, er.score, er.evaluated_at,
               {_NEEDS_REVIEW_EXISTS} AS needs_review
        FROM   answers a
        LEFT JOIN evaluation_results er
               ON er.answer_id = a.answer_id AND er.is_current
        WHERE  {' AND '.join(where)}
        ORDER  BY a.submitted_at DESC, a.answer_id DESC
        LIMIT  %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return [
        {
            "answer_id": r[0], "question_id": r[1], "student_id": r[2],
            "exam_id": r[3], "status": r[4], "submitted_at": r[5],
            "score": r[6], "evaluated_at": r[7], "needs_review": r[8],
        }
        for r in cur.fetchall()
    ]


def count_results(
    cur, *, college_id, exam_id=None, student_id=None, status: str | None = None,
    needs_review: bool | None = None,
) -> int:
    """Total matching `list_results`'s filters, ignoring limit/offset."""
    if status is not None and status not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")

    where, params = _filters(college_id, exam_id, student_id, status, needs_review)

    # needs_review is evaluated per-answer (not per the join), so COUNT(*)
    # over `answers a` alone is correct here — no evaluation_results join is
    # needed just to count.
    cur.execute(f"SELECT COUNT(*) FROM answers a WHERE {' AND '.join(where)}", tuple(params))
    (total,) = cur.fetchone()
    return int(total)


def _filters(college_id, exam_id, student_id, status, needs_review):
    where: list[str] = ["a.college_id = %s"]
    params: list[Any] = [str(college_id)]

    if exam_id is not None:
        where.append("a.exam_id = %s")
        params.append(str(exam_id))
    if student_id is not None:
        where.append("a.student_id = %s")
        params.append(str(student_id))
    if status is not None:
        where.append("a.status = %s")
        params.append(status)
    if needs_review is True:
        where.append(_NEEDS_REVIEW_EXISTS)
    elif needs_review is False:
        where.append(f"NOT {_NEEDS_REVIEW_EXISTS}")

    return where, params
