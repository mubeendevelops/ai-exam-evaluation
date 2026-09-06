"""api/routers/exams.py — GET /api/v1/exams: this college's exams.

Calls core/exams.py directly, the same pattern api/routers/jobs.py and
api/routers/upload.py already use for a plain tenant-scoped read with no
translation to do — a service-layer adapter earns its place when there is
real translation or error classification (questions, papers, evaluation);
this is neither.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

import core.exams
from api.deps.db import get_tenant_conn
from api.deps.identity import CurrentUser, require_college_user
from api.deps.pagination import Pagination, get_pagination
from api.schemas.exams import ExamStatus, ExamSummary, exam_to_summary
from api.schemas.pagination import Page

router = APIRouter(prefix="/api/v1/exams", tags=["exams"])


@router.get("", response_model=Page[ExamSummary], summary="List this college's exams")
def list_exams(
    status_filter: ExamStatus | None = Query(default=None, alias="status"),
    user: CurrentUser = Depends(require_college_user),
    conn=Depends(get_tenant_conn),
    pagination: Pagination = Depends(get_pagination),
) -> Page[ExamSummary]:
    with conn.cursor() as cur:
        exams = core.exams.list_exams(
            cur, college_id=user.college_id, status=status_filter,
            limit=pagination.limit, offset=pagination.offset,
        )
        total = core.exams.count_exams(cur, college_id=user.college_id, status=status_filter)

    return Page(
        items=[exam_to_summary(e) for e in exams],
        total=total, limit=pagination.limit, offset=pagination.offset,
    )
