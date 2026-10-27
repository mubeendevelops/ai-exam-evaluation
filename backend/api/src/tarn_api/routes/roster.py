"""The roster: CSV import and the student picker's search. The admin's (D162, D177)."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status

from tarn_api.backends import unit_of_work
from tarn_api.schemas import ErrorOut, RosterReportOut, RowErrorOut, StudentIn, StudentOut
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.personas import Capability
from tarn_core.domain.tenancy import Student
from tarn_core.ids import StudentId
from tarn_core.services.roster import MAX_CHARS

router = APIRouter(
    prefix="/api/v1",
    tags=["roster"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)
MAX_BYTES = 4 * MAX_CHARS  # UTF-8; the character limit is checked after decoding


@router.post(
    "/roster/import",
    response_model=RosterReportOut,
    responses={413: {"model": ErrorOut}, 415: {"model": ErrorOut}},
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": "name,usn,class/section\nAsha Rao,1AB21CS001,CSE-A\n",
                }
            },
        }
    },
    summary="Import the roster CSV (name, USN, class/section); upsert by USN",
    description="If any row has an error nothing is written and every error is reported "
    "with its line number.",
)
async def import_roster(
    request: Request, who: PrincipalDep, backends: BackendsDep
) -> RosterReportOut:
    media = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media not in {"text/csv", "text/plain", "application/csv"}:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Send the file as text/csv."
        )
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, detail="The file is larger than 1 MB."
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Save the CSV as UTF-8."
        ) from None
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_STUDENTS)
        report = unit.roster.import_csv(who.college_id, who.user_id, text)
    return RosterReportOut(
        imported=report.imported,
        rows=report.rows,
        created=report.created,
        updated=report.updated,
        unchanged=report.unchanged,
        errors=[RowErrorOut(line=e.line, field=e.field, message=e.message) for e in report.errors],
    )


@router.get(
    "/students",
    response_model=list[StudentOut],
    summary="Search the roster by USN prefix or part of the name (student picker)",
)
def search_students(
    who: PrincipalDep,
    backends: BackendsDep,
    q: str = Query("", max_length=100),
    limit: int = Query(20, ge=1, le=50),
) -> list[StudentOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_STUDENTS)
        students = unit.roster.search(who.college_id, q, limit)
    return [_student_out(s) for s in students]


def _student_out(student: Student) -> StudentOut:
    return StudentOut(
        id=student.id, name=student.name, usn=student.usn, class_section=student.class_section
    )


@router.post(
    "/students",
    status_code=status.HTTP_201_CREATED,
    response_model=StudentOut,
    responses={409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Add one student to the roster (admin)",
)
def add_student(body: StudentIn, who: PrincipalDep, backends: BackendsDep) -> StudentOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_STUDENTS)
        student = unit.roster.add(
            who.college_id,
            who.user_id,
            name=body.name,
            usn=body.usn,
            class_section=body.class_section,
        )
    return _student_out(student)


@router.put(
    "/students/{student_id}",
    response_model=StudentOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Correct a student's name, USN or class/section (admin)",
)
def edit_student(
    student_id: UUID, body: StudentIn, who: PrincipalDep, backends: BackendsDep
) -> StudentOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_STUDENTS)
        student = unit.roster.update(
            who.college_id,
            who.user_id,
            StudentId(student_id),
            name=body.name,
            usn=body.usn,
            class_section=body.class_section,
        )
    return _student_out(student)
