"""Units of work (P26, D162): the admin creates a unit (a paper, a student year, an entitled
SME, a due date; ``assign_units``) and uploads its booklets, one PDF per student named by the
roll number (``upload_booklets``). The admin sees every unit with each booklet's status and no
marks (D167); an SME lists and opens the units they own (``view_progress``)."""

from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, UploadFile, status

from tarn_api.backends import Unit, unit_of_work
from tarn_api.routes.booklets import booklet_out, is_evaluator, read_capped
from tarn_api.schemas import (
    DelegationRequestIn,
    DelegationRequestOut,
    DelegationUnitOut,
    ErrorOut,
    TransferIn,
    TransferOut,
    UnitChoicePaperOut,
    UnitCountsOut,
    UnitDetailOut,
    UnitIn,
    UnitOut,
    UnitOwnerOut,
    UnitPaperOut,
    UnitRequestRefOut,
    UnitSmeOut,
    UnitStateName,
    UnitSubjectOut,
    UnitUploadOut,
    UploadFileOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import APPROVED_STATUSES, Booklet, BookletStatus
from tarn_core.domain.content import Subject
from tarn_core.domain.personas import Capability
from tarn_core.domain.unit import DelegationRequest, UnitState
from tarn_core.domain.unit import Unit as WorkUnit
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UnitId, UserId
from tarn_core.services.unit_upload import PROBLEM_TEXT, FileOutcome, NamedFile

router = APIRouter(
    prefix="/api/v1",
    tags=["units"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

_S = BookletStatus
_PROCESSING = frozenset(
    {_S.UPLOADED, _S.PROCESSING, _S.PAGES_READY, _S.READING, _S.TEXT_READY, _S.SEGMENTED}
)


def _counts(booklets: Sequence[Booklet]) -> UnitCountsOut:
    statuses = [b.status for b in booklets]
    return UnitCountsOut(
        total=len(statuses),
        processing=sum(1 for s in statuses if s in _PROCESSING),
        needs_retake=statuses.count(_S.NEEDS_RETAKE),
        failed=statuses.count(_S.FAILED),
        ready=statuses.count(_S.SCORED),
        in_review=statuses.count(_S.IN_REVIEW),
        completed=sum(1 for s in statuses if s in APPROVED_STATUSES),
    )


def _paper_out(paper: ExamBlueprint) -> UnitPaperOut:
    return UnitPaperOut(
        id=paper.id,
        version=paper.meta.version,
        title=paper.title,
        exam_code=paper.exam_code,
        course_code=paper.course_code,
        total_marks=float(paper.total_marks),
    )


def _subject_out(subject: Subject) -> UnitSubjectOut:
    return UnitSubjectOut(id=subject.id, code=subject.code, name=subject.name)


def _unit_out(unit: Unit, college_id: CollegeId, work: WorkUnit) -> UnitOut:
    paper = unit.scope.content.get(college_id, ExamBlueprint, work.paper_id)
    subject = unit.scope.content.get(college_id, Subject, work.subject_id)
    owner = unit.scope.users.get(college_id, work.owner_id)
    return UnitOut(
        id=work.id,
        code=work.code,
        paper=_paper_out(paper),
        subject=_subject_out(subject),
        year=work.year,
        owner=UnitOwnerOut(id=owner.id, name=owner.display_name),
        due_date=work.due_date,
        state=work.state.value,
        created_at=work.created_at,
        version=work.version,
        counts=_counts(unit.scope.booklets.in_unit(college_id, work.id)),
        pending_request=_pending(unit, college_id, work),
    )


def _pending(unit: Unit, college_id: CollegeId, work: WorkUnit) -> UnitRequestRefOut | None:
    found = unit.scope.units.pending_request(college_id, work.id)
    return (
        None if found is None else UnitRequestRefOut(id=found.id, requested_at=found.requested_at)
    )


def owner_out(unit: Unit, college_id: CollegeId, user_id: UserId) -> UnitOwnerOut:
    return UnitOwnerOut(id=user_id, name=unit.scope.users.get(college_id, user_id).display_name)


def delegation_out(
    unit: Unit, college_id: CollegeId, request: DelegationRequest
) -> DelegationRequestOut:
    """The request with its unit as it is now (code, paper, owner, progress: no marks)."""
    work = unit.scope.units.get(college_id, request.unit_id)
    paper = unit.scope.content.get(college_id, ExamBlueprint, work.paper_id)
    subject = unit.scope.content.get(college_id, Subject, work.subject_id)
    return DelegationRequestOut(
        id=request.id,
        unit=DelegationUnitOut(
            id=work.id,
            code=work.code,
            paper_title=paper.title,
            subject=_subject_out(subject),
            year=work.year,
            due_date=work.due_date,
            owner=owner_out(unit, college_id, work.owner_id),
            counts=_counts(unit.scope.booklets.in_unit(college_id, work.id)),
        ),
        requested_by=owner_out(unit, college_id, request.requested_by),
        reason=request.reason,
        requested_at=request.requested_at,
        state=request.state.value,
        closed_at=request.closed_at,
        note=request.note,
    )


@router.get(
    "/units",
    response_model=list[UnitOut],
    summary="Units of work, newest first: every unit for the admin, your own for an SME",
)
def list_units(
    who: PrincipalDep,
    backends: BackendsDep,
    subject_id: Annotated[UUID | None, Query()] = None,
    owner_id: Annotated[UUID | None, Query(description="The admin's filter by SME")] = None,
    state: Annotated[UnitStateName | None, Query()] = None,
) -> list[UnitOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_PROGRESS)
        found = unit.units.list(
            who.college_id,
            who.user_id,
            subject_id=None if subject_id is None else SubjectId(subject_id),
            owner_id=None if owner_id is None else UserId(owner_id),
            state=None if state is None else UnitState(state),
        )
        return [_unit_out(unit, who.college_id, u) for u in found]


@router.get(
    "/units/papers",
    response_model=list[UnitChoicePaperOut],
    summary="Papers a unit can be made from: every question linked, an exam code, a live subject",
)
def unit_papers(
    who: PrincipalDep,
    backends: BackendsDep,
    subject_id: Annotated[UUID | None, Query()] = None,
) -> list[UnitChoicePaperOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.ASSIGN_UNITS)
        choices = unit.units.papers(
            who.college_id, who.user_id, None if subject_id is None else SubjectId(subject_id)
        )
        return [
            UnitChoicePaperOut(**_paper_out(c.paper).model_dump(), subject=_subject_out(c.subject))
            for c in choices
        ]


@router.get(
    "/units/smes",
    response_model=list[UnitSmeOut],
    summary="The SMEs a unit of this subject may be assigned to: active and entitled (D160)",
)
def unit_smes(
    who: PrincipalDep,
    backends: BackendsDep,
    subject_id: Annotated[UUID, Query()],
) -> list[UnitSmeOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.ASSIGN_UNITS)
        smes = unit.units.entitled_smes(who.college_id, who.user_id, SubjectId(subject_id))
        return [UnitSmeOut(id=u.id, name=u.display_name, email=u.email) for u in smes]


@router.post(
    "/units",
    response_model=UnitOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut, "description": "A unit of this paper and year exists"},
        422: {"model": ErrorOut},
    },
    summary="Create a unit of work and assign it to an SME (emailed)",
    description="The paper must have every question linked and an exam code; the SME must be "
    "active and entitled to the paper's subject; the due date is today or later. The SME is "
    "emailed the unit code, paper, subject and due date (no student data). Audited.",
)
def create_unit(body: UnitIn, who: PrincipalDep, backends: BackendsDep) -> UnitOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.ASSIGN_UNITS)
        made = unit.units.create(
            who.college_id,
            who.user_id,
            paper_id=BlueprintId(body.paper_id),
            year=body.year,
            owner_id=UserId(body.owner_id),
            due_date=body.due_date,
        )
        return _unit_out(unit, who.college_id, made)


@router.get(
    "/units/{unit_id}",
    response_model=UnitDetailOut,
    responses={404: {"model": ErrorOut}},
    summary="One unit with each booklet's processing status (no marks for the admin, D167)",
)
def get_unit(unit_id: UUID, who: PrincipalDep, backends: BackendsDep) -> UnitDetailOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.VIEW_PROGRESS)
        work = unit.units.get(who.college_id, who.user_id, UnitId(unit_id))
        evaluator = is_evaluator(user)
        booklets = [
            booklet_out(unit, who.college_id, b, evaluator=evaluator)[0]
            for b in unit.scope.booklets.in_unit(who.college_id, work.id)
        ]
        limits = backends.upload_limits
        return UnitDetailOut(
            **_unit_out(unit, who.college_id, work).model_dump(),
            booklets=booklets,
            waiting=unit.scope.booklets.count_waiting(who.college_id, who.user_id),
            max_waiting=limits.max_waiting,
            max_files=limits.max_batch_files,
        )


@router.post(
    "/units/{unit_id}/booklets",
    response_model=UnitUploadOut,
    responses={404: {"model": ErrorOut}, 413: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Upload booklets into a unit: one PDF per student, named by the roll number",
    description="Each file is named by the student's roll number (USN), for example "
    "`1XX21CS001.pdf`, matched without regard to case. Every file is checked first; a file "
    "that fails is listed with its reason and nothing is stored for it. Valid files are queued "
    "while you have free queue slots; the rest are listed as not queued (upload them again "
    "later). Only the last part of a file name counts.",
)
async def upload_to_unit(
    unit_id: UUID,
    request: Request,
    who: PrincipalDep,
    backends: BackendsDep,
    files: Annotated[list[UploadFile], File(description="PDFs, one per student")],
) -> UnitUploadOut:
    data = await read_capped(request, files, backends.upload_limits.max_total_bytes)
    named = [NamedFile(name=f.filename or "", data=d) for f, d in zip(files, data, strict=True)]
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.UPLOAD_BOOKLETS)
        report = unit.unit_uploads.upload(who.college_id, who.user_id, UnitId(unit_id), named)
    return UnitUploadOut(
        files=[
            UploadFileOut(
                name=r.name,
                outcome=r.outcome.value,
                problem=None if r.problem is None else r.problem.value,
                message=None if r.problem is None else PROBLEM_TEXT[r.problem],
                usn=r.usn,
                student_id=r.student_id,
                booklet_id=r.booklet_id,
            )
            for r in report.results
        ],
        queued=report.count(FileOutcome.QUEUED),
        refused=report.count(FileOutcome.REFUSED),
        not_queued=report.count(FileOutcome.NOT_QUEUED),
    )


# --- transfers and delegation requests (P27, D163) ---------------------------------------------


@router.post(
    "/units/{unit_id}/transfer",
    response_model=UnitOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Move an open unit to another SME entitled to its subject (admin)",
    description="The whole unit moves with every booklet's state; the old SME loses access at "
    "once and their locks on its booklets are released. A pending delegation request for the "
    "unit closes as done. Both SMEs are emailed (no reason, no student data). Audited with the "
    "reason.",
)
def transfer_unit(
    unit_id: UUID, body: TransferIn, who: PrincipalDep, backends: BackendsDep
) -> UnitOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.REDISTRIBUTE_UNITS)
        moved = unit.transfers.transfer(
            who.college_id,
            who.user_id,
            UnitId(unit_id),
            owner_id=UserId(body.owner_id),
            reason=body.reason,
            expected_version=body.expected_version,
        )
        return _unit_out(unit, who.college_id, moved)


@router.get(
    "/units/{unit_id}/transfers",
    response_model=list[TransferOut],
    responses={404: {"model": ErrorOut}},
    summary="The unit's transfers, oldest first (admin)",
)
def unit_transfers(unit_id: UUID, who: PrincipalDep, backends: BackendsDep) -> list[TransferOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.REDISTRIBUTE_UNITS)
        found = unit.transfers.history(who.college_id, who.user_id, UnitId(unit_id))
        return [
            TransferOut(
                id=t.id,
                from_owner=owner_out(unit, who.college_id, t.from_owner),
                to_owner=owner_out(unit, who.college_id, t.to_owner),
                reason=t.reason,
                at=t.at,
                request_id=t.request_id,
            )
            for t in found
        ]


@router.post(
    "/units/{unit_id}/delegation-requests",
    response_model=DelegationRequestOut,
    status_code=status.HTTP_201_CREATED,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Ask your admin to move one of your units to another SME (SME)",
    description="One pending request per unit (409 while one waits). The admin picks the new "
    "SME. Audited.",
)
def request_reassignment(
    unit_id: UUID, body: DelegationRequestIn, who: PrincipalDep, backends: BackendsDep
) -> DelegationRequestOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.REQUEST_REASSIGNMENT)
        made = unit.delegation.request(
            who.college_id, who.user_id, UnitId(unit_id), reason=body.reason
        )
        return delegation_out(unit, who.college_id, made)
