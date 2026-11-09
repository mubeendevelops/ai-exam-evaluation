"""Result sheet PDFs (P18): any version of a booklet's sheet. The evaluated booklets list went
with the Evaluated tab (P32, D215); the versions are in ``GET /booklets/{id}/review``."""

import re
from uuid import UUID

from fastapi import APIRouter, Response

from tarn_api.backends import unit_of_work
from tarn_api.schemas import (
    ErrorOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.personas import Capability
from tarn_core.ids import BookletId
from tarn_core.services.workflow.sheets import PDF_MEDIA_TYPE

router = APIRouter(
    prefix="/api/v1",
    tags=["evaluated booklets"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


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
        user = current_user(unit, who, Capability.EVALUATE)
        sheet, pdf = unit.evaluated.pdf(
            who.college_id, BookletId(booklet_id), version, reach=unit.reach(user)
        )
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
