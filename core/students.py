"""core/students.py — filtered reads over one college's students.

TENANCY: `students` is one of migration 003's seven RLS-protected
answer-schema tables. Same belt-and-braces argument as core/exams.py and
core/jobs.py: every function here takes `college_id` and filters on it
explicitly, in addition to whatever the RLS policy does.

FILTERING BY EXAM: there is no FK from `students` to `exams` — a student and
an exam are only ever connected through an `answers` row (one per question a
student answered on that exam). So `exam_id` filters via a JOIN onto
`answers`, and the query is DISTINCT: a student with five answered questions
on one exam must appear once in the page, not five times.

ORDERING: `enrolled_at DESC, student_id DESC` — `enrolled_at` is NOT NULL
(migration 001), so no NULLS LAST handling is needed here the way
core/exams.py needs it for `conducted_at`.
"""
from __future__ import annotations

from typing import Any

import core.pagination


def list_students(
    cur, *, college_id, exam_id=None, limit: int = 50, offset: int = 0,
) -> list[dict[str, Any]]:
    """A tenant-scoped, optionally exam-filtered page of students."""
    limit = core.pagination.clamp_limit(limit)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    # Params in QUERY-TEXT order: the JOIN's %s (if present) comes before the
    # WHERE's, which comes before LIMIT/OFFSET's.
    params: list[Any] = []
    if exam_id is not None:
        join = "JOIN answers a ON a.student_id = s.student_id AND a.exam_id = %s"
        params.append(str(exam_id))
        select = "SELECT DISTINCT s.student_id, s.name, s.roll_number, s.email, s.enrolled_at"
    else:
        join = ""
        select = "SELECT s.student_id, s.name, s.roll_number, s.email, s.enrolled_at"
    params.append(str(college_id))
    params.extend([limit, offset])

    cur.execute(
        f"""
        {select}
        FROM   students s
        {join}
        WHERE  s.college_id = %s
        ORDER  BY s.enrolled_at DESC, s.student_id DESC
        LIMIT  %s OFFSET %s
        """,
        tuple(params),
    )
    return [
        {
            "student_id": r[0], "name": r[1], "roll_number": r[2],
            "email": r[3], "enrolled_at": r[4],
        }
        for r in cur.fetchall()
    ]


def count_students(cur, *, college_id, exam_id=None) -> int:
    """Total matching `list_students`'s filters, ignoring limit/offset."""
    if exam_id is not None:
        cur.execute(
            """
            SELECT COUNT(DISTINCT s.student_id)
            FROM   students s
            JOIN   answers  a ON a.student_id = s.student_id AND a.exam_id = %s
            WHERE  s.college_id = %s
            """,
            (str(exam_id), str(college_id)),
        )
    else:
        cur.execute("SELECT COUNT(*) FROM students WHERE college_id = %s", (str(college_id),))
    (total,) = cur.fetchone()
    return int(total)
