"""api/routers/students.py — GET /api/v1/students: this college's students,
optionally filtered to one exam.

Calls core/students.py directly — see api/routers/exams.py's header for why
this doesn't get its own service-layer adapter.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

import core.students
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, require_college_user
from api.deps.pagination import Pagination, get_pagination
from api.schemas.pagination import Page
from api.schemas.students import StudentSummary, student_to_summary

router = APIRouter(prefix="/api/v1/students", tags=["students"])


@router.get("", response_model=Page[StudentSummary], summary="List this college's students")
def list_students(
    exam_id: uuid.UUID | None = Query(
        default=None,
        description="Only students with at least one answer on this exam. "
                    "There is no direct student->exam link in this schema "
                    "(§7C) — see core/students.py.",
    ),
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[StudentSummary]:
    with conn.cursor() as cur:
        students = core.students.list_students(
            cur, college_id=user.college_id, exam_id=exam_id,
            limit=pagination.limit, offset=pagination.offset,
        )
        total = core.students.count_students(cur, college_id=user.college_id, exam_id=exam_id)

    return Page(
        items=[student_to_summary(s) for s in students],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )
