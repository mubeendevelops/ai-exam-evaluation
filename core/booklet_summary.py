"""core/booklet_summary.py — one row per (student, exam) "booklet", rolled up
from `answers`, for the GET /api/v1/results/booklets list endpoint.

WHY GROUPED BY (student_id, exam_id). There is no `booklets` table and no
`exams.paper_id` column (§7C's open schema gap — see
api/services/evaluation.py's module docstring for the full explanation this
repo already settled on). A booklet is only ever addressable as "the answers
one student wrote for one exam", so that pair is the only grain this can
group on. There is also no FK from `questions`/`exams` to a paper, so there is
no fixed "expected question count" to compare against — this reports how many
answers exist and how many are scored, not "N of M expected".

SCORE / MAX SCORE. `max_score` sums `questions.marks_max` only for the
answers that currently have a score (`evaluation_results.is_current`) — not
every answered question. Diluting the denominator with not-yet-scored
answers would make a partially-graded booklet's ratio meaningless; the
`answer_count` / `scored_count` pair already tells the caller how much of the
booklet is still pending.

STATUS ROLLUP — no existing precedent in this codebase, this module is where
the rule is decided:
    1. Any answer `flagged`   -> booklet is `flagged`. A flag is the most
       urgent signal a reviewer can get; it must not be hidden by other
       answers being further along.
    2. Otherwise, the booklet's status is whichever of
       [pending_evaluation, ai_scored, sme_reviewed, finalized] is the
       EARLIEST one present among its answers — a booklet is only as done as
       its least-progressed answer. A booklet with five `finalized` answers
       and one `pending_evaluation` answer is still `pending_evaluation`.
This is computed in Python (`_rollup_status`) over `ARRAY_AGG(DISTINCT
a.status)`, not in SQL, to keep the ordering rule readable and in one place.

`needs_review` reuses core/results.py's exact per-answer EXISTS
(`NEEDS_REVIEW_EXISTS`) rolled up with BOOL_OR — an answer "needs review"
here for the same reason it does there: at least one of its answer_blocks
(migration 013) is flagged.

TENANCY: same belt-and-braces `college_id` predicate as core/results.py,
core/jobs.py, core/exams.py, core/students.py.
"""
from __future__ import annotations

from typing import Any

import core.pagination
from core.results import NEEDS_REVIEW_EXISTS, answer_scope_filters

#: answer_status pipeline order, least to most progressed (migration 001
#: line 34) — used by _rollup_status to find a booklet's least-progressed
#: non-flagged answer.
_PIPELINE_ORDER = ("pending_evaluation", "ai_scored", "sme_reviewed", "finalized")

def _rollup_status(statuses: list[str]) -> str:
    """One status for a booklet from the distinct statuses of its answers.

    See this module's docstring for the rule and why it's ordered this way.
    """
    if "flagged" in statuses:
        return "flagged"
    for status in _PIPELINE_ORDER:
        if status in statuses:
            return status
    # Unreachable in practice: every answer_status value is covered above,
    # and an answer always has one. Falls back rather than raising so a
    # future enum value degrades gracefully instead of 500ing the list.
    return statuses[0]


def list_booklets(
    cur, *, college_id, exam_id=None, student_id=None,
    needs_review: bool | None = None, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """A tenant-scoped, filtered page of booklet (student+exam) summaries."""
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    where, having, params = _filters(college_id, exam_id, student_id, needs_review)

    cur.execute(
        f"""
        SELECT a.student_id, a.exam_id,
               COUNT(DISTINCT a.answer_id) AS answer_count,
               COUNT(DISTINCT a.answer_id)
                   FILTER (WHERE er.answer_id IS NOT NULL) AS scored_count,
               SUM(er.score) AS total_score,
               SUM(q.marks_max)
                   FILTER (WHERE er.answer_id IS NOT NULL) AS max_score,
               MAX(a.submitted_at) AS latest_submitted_at,
               ARRAY_AGG(DISTINCT a.status) AS statuses,
               BOOL_OR({NEEDS_REVIEW_EXISTS}) AS needs_review
        FROM   answers a
        JOIN   questions q ON q.question_id = a.question_id
        LEFT JOIN evaluation_results er
               ON er.answer_id = a.answer_id AND er.is_current
        WHERE  {' AND '.join(where)}
        GROUP  BY a.student_id, a.exam_id
        {f'HAVING {having}' if having else ''}
        ORDER  BY MAX(a.submitted_at) DESC, a.student_id, a.exam_id
        LIMIT  %s OFFSET %s
        """,
        (*params, limit, offset),
    )
    return [
        {
            "student_id": r[0], "exam_id": r[1], "answer_count": r[2],
            "scored_count": r[3],
            "total_score": r[4], "max_score": r[5],
            "latest_submitted_at": r[6],
            "status": _rollup_status(r[7]),
            "needs_review": r[8],
        }
        for r in cur.fetchall()
    ]


def count_booklets(
    cur, *, college_id, exam_id=None, student_id=None,
    needs_review: bool | None = None,
) -> int:
    """Total distinct booklets matching `list_booklets`'s filters."""
    where, having, params = _filters(college_id, exam_id, student_id, needs_review)

    cur.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT 1
            FROM   answers a
            JOIN   questions q ON q.question_id = a.question_id
            LEFT JOIN evaluation_results er
                   ON er.answer_id = a.answer_id AND er.is_current
            WHERE  {' AND '.join(where)}
            GROUP  BY a.student_id, a.exam_id
            {f'HAVING {having}' if having else ''}
        ) sub
        """,
        tuple(params),
    )
    (total,) = cur.fetchone()
    return int(total)


def _filters(college_id, exam_id, student_id, needs_review):
    where, params = answer_scope_filters(college_id, exam_id, student_id)

    # needs_review rolls up per BOOKLET here (any answer flagged), so it is a
    # HAVING over the group, not core/results.py's per-answer WHERE term.
    having = ""
    if needs_review is True:
        having = f"BOOL_OR({NEEDS_REVIEW_EXISTS})"
    elif needs_review is False:
        having = f"NOT BOOL_OR({NEEDS_REVIEW_EXISTS})"

    return where, having, params
