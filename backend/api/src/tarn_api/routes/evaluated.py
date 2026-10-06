"""Evaluated booklets (P18): the college's approved booklets with their result sheet versions,
searchable by exam, student, USN and status, and the PDF of any version. Deleting a booklet from
the list is the ordinary ``DELETE /booklets/{id}`` (images, text, marks and every sheet go; a
content-free record stays)."""

import re
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from tarn_api.backends import unit_of_work
from tarn_api.routes.review import sheet_version_out
from tarn_api.schemas import (
    BookletStudentOut,
    ErrorOut,
    EvaluatedBookletOut,
    EvaluatedPageOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.tenancy import Role
from tarn_core.ids import BookletId
from tarn_core.services.workflow.sheets import PDF_MEDIA_TYPE

router = APIRouter(
    prefix="/api/v1",
    tags=["evaluated booklets"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


@router.get(
    "/evaluated-booklets",
    response_model=EvaluatedPageOut,
    summary="Approved booklets with their result sheet versions, newest first",
    description="`exam` matches the exam title or course code, `student` the name and `usn` the "
    "USN, each as a case-insensitive part of it. `status` is `approved`, "
    "`amendment_in_progress` (a result sheet stands while an answer is being amended) or "
    "`approved_amended`.",
)
def list_evaluated(
    who: PrincipalDep,
    backends: BackendsDep,
    exam: Annotated[str, Query(max_length=200)] = "",
    student: Annotated[str, Query(max_length=200)] = "",
    usn: Annotated[str, Query(max_length=40)] = "",
    booklet_status: Annotated[
        Literal["approved", "amendment_in_progress", "approved_amended"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EvaluatedPageOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        page = unit.evaluated.search(
            who.college_id,
            exam=exam,
            student=student,
            usn=usn,
            status=None if booklet_status is None else BookletStatus(booklet_status),
            limit=limit,
            offset=offset,
        )
    return EvaluatedPageOut(
        items=[
            EvaluatedBookletOut(
                id=e.booklet.id,
                status=e.booklet.status.value,
                student=BookletStudentOut(id=e.student.id, name=e.student.name, usn=e.student.usn),
                exam=e.exam,
                course_code=e.course_code,
                total=float(e.latest.total),
                max_marks=float(e.latest.max_marks),
                sheet_version=e.latest.version,
                evaluated_at=e.latest.issued_at,
                sheets=[sheet_version_out(e.booklet.id, s) for s in e.sheets],
            )
            for e in page.items
        ],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/booklets/{booklet_id}/result-sheets/{version}/pdf",
    responses={
        200: {"content": {PDF_MEDIA_TYPE: {}}},
        404: {"model": ErrorOut, "description": "No such booklet or version, or no PDF stored."},
    },
    summary="Download one version of a booklet's result sheet as a PDF",
)
def download_sheet(
    booklet_id: UUID, version: int, who: PrincipalDep, backends: BackendsDep
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        sheet, pdf = unit.evaluated.pdf(who.college_id, BookletId(booklet_id), version)
        student = unit.scope.students.get(
            who.college_id, unit.scope.booklets.get(who.college_id, sheet.booklet_id).student_id
        )
    name = f"result-sheet-{_UNSAFE.sub('', student.usn) or 'student'}-v{sheet.version}.pdf"
    return Response(
        content=pdf,
        media_type=PDF_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )
