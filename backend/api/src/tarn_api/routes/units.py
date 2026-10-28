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
    FreezeIn,
    FrozenAnswerOut,
    FrozenBookletOut,
    FrozenPaperOut,
    FrozenRecordOut,
    FrozenSheetOut,
    ReturnIn,
    SubmitIn,
    TransferIn,
    TransferOut,
    UnitChoicePaperOut,
    UnitCountsOut,
    UnitDetailOut,
    UnitFrozenOut,
    UnitIn,
    UnitOut,
    UnitOwnerOut,
    UnitPaperOut,
    UnitRequestRefOut,
    UnitSmeOut,
    UnitStateName,
    UnitSubjectOut,
    UnitSubmissionOut,
    UnitUploadOut,
    UnmarkedOut,
    UploadFileOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import APPROVED_STATUSES, Booklet, BookletStatus
from tarn_core.domain.content import Subject
from tarn_core.domain.personas import Capability
from tarn_core.domain.unit import DelegationRequest, UnitState, UnmarkedBooklet, UnmarkedReason
from tarn_core.domain.unit import Unit as WorkUnit
from tarn_core.ids import BlueprintId, BookletId, CollegeId, SubjectId, UnitId, UserId
from tarn_core.services.unit_flow import PaperSummary, paper_summary, summary_of
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


def _paper_out(paper: ExamBlueprint | PaperSummary) -> UnitPaperOut:
    if isinstance(paper, ExamBlueprint):
        paper = summary_of(paper)
    return UnitPaperOut(
        id=paper.id,
        version=paper.version,
        title=paper.title,
        exam_code=paper.exam_code,
        course_code=paper.course_code,
        total_marks=float(paper.total_marks),
    )


def _subject_out(subject: Subject) -> UnitSubjectOut:
    return UnitSubjectOut(id=subject.id, code=subject.code, name=subject.name)


def _paper(unit: Unit, college_id: CollegeId, work: WorkUnit) -> PaperSummary:
    """The unit's paper, or its frozen copy once the paper is deleted (D166)."""
    return paper_summary(unit.scope.content, unit.scope.units, college_id, work.id, work.paper_id)


def _submission_out(unit: Unit, college_id: CollegeId, work: WorkUnit) -> UnitSubmissionOut | None:
    found = unit.scope.units.submissions(college_id, work.id)
    if not found:
        return None
    last = found[-1]
    return UnitSubmissionOut(
        id=last.id,
        submitted_by=owner_out(unit, college_id, last.submitted_by),
        submitted_at=last.submitted_at,
        unmarked=[
            UnmarkedOut(booklet_id=u.booklet_id, reason=u.reason.value, note=u.note)
            for u in last.unmarked
        ],
        returned_at=last.returned_at,
        returned_by=None
        if last.returned_by is None
        else owner_out(unit, college_id, last.returned_by),
        return_note=last.return_note,
    )


def _frozen_out(unit: Unit, college_id: CollegeId, work: WorkUnit) -> UnitFrozenOut | None:
    if work.state is not UnitState.FROZEN:
        return None
    record = unit.scope.units.frozen(college_id, work.id)
    if record is None:
        return None
    return UnitFrozenOut(
        frozen_by=owner_out(unit, college_id, record.frozen_by), frozen_at=record.frozen_at
    )


def unit_out(unit: Unit, college_id: CollegeId, work: WorkUnit) -> UnitOut:
    paper = _paper(unit, college_id, work)
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
        submission=_submission_out(unit, college_id, work),
        frozen=_frozen_out(unit, college_id, work),
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
    paper = _paper(unit, college_id, work)
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
    summary="Units of work, newest first: every unit for the admin, your own for an SME, the "
    "submitted and frozen ones for the Governor",
)
def list_units(
    who: PrincipalDep,
    backends: BackendsDep,
    subject_id: Annotated[UUID | None, Query()] = None,
    owner_id: Annotated[UUID | None, Query(description="The admin's filter by SME")] = None,
    state: Annotated[UnitStateName | None, Query()] = None,
) -> list[UnitOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_PROGRESS, Capability.FREEZE_UNITS)
        found = unit.units.list(
            who.college_id,
            who.user_id,
            subject_id=None if subject_id is None else SubjectId(subject_id),
            owner_id=None if owner_id is None else UserId(owner_id),
            state=None if state is None else UnitState(state),
        )
        return [unit_out(unit, who.college_id, u) for u in found]


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
        return unit_out(unit, who.college_id, made)


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
            **unit_out(unit, who.college_id, work).model_dump(),
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
        return unit_out(unit, who.college_id, moved)


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


# --- submit, return, freeze (P28, D164) --------------------------------------------------------

_CLOSED = {"model": ErrorOut, "description": "Not in a state that allows this, or stale"}


@router.post(
    "/units/{unit_id}/submit",
    response_model=UnitOut,
    responses={404: {"model": ErrorOut}, 409: _CLOSED, 422: {"model": ErrorOut}},
    summary="Submit your unit to the admin (SME): final until the admin returns it",
    description="Every booklet must be approved, or listed in `unmarked` with a reason (absent, "
    "unreadable, or other with a note). Refused while a booklet is still being processed or "
    "has an amendment in progress. Afterwards nothing in the unit can be changed. Audited "
    "(reason codes and counts only).",
)
def submit_unit(unit_id: UUID, body: SubmitIn, who: PrincipalDep, backends: BackendsDep) -> UnitOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.SUBMIT_UNITS)
        done = unit.unit_flow.submit(
            who.college_id,
            who.user_id,
            UnitId(unit_id),
            unmarked=[
                UnmarkedBooklet(
                    booklet_id=BookletId(u.booklet_id),
                    reason=UnmarkedReason(u.reason),
                    note=u.note.strip(),
                )
                for u in body.unmarked
            ],
            expected_version=body.expected_version,
        )
        return unit_out(unit, who.college_id, done)


@router.post(
    "/units/{unit_id}/return",
    response_model=UnitOut,
    responses={404: {"model": ErrorOut}, 409: _CLOSED, 422: {"model": ErrorOut}},
    summary="Return a submitted unit to its SME with a note (admin), until it is frozen",
    description="The unit is open again; the SME is emailed the note. Audited (without the note).",
)
def return_unit(unit_id: UUID, body: ReturnIn, who: PrincipalDep, backends: BackendsDep) -> UnitOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.RETURN_UNITS)
        done = unit.unit_flow.return_(
            who.college_id,
            who.user_id,
            UnitId(unit_id),
            note=body.note,
            expected_version=body.expected_version,
        )
        return unit_out(unit, who.college_id, done)


@router.post(
    "/units/{unit_id}/freeze",
    response_model=UnitOut,
    responses={404: {"model": ErrorOut}, 409: _CLOSED},
    summary="Freeze and sign off a submitted unit (Governor): nothing in it changes again",
    description="Writes the frozen record (marks per booklet and answer, paper versions and "
    "slots, result sheet versions, who froze it and when). Audited.",
)
def freeze_unit(unit_id: UUID, body: FreezeIn, who: PrincipalDep, backends: BackendsDep) -> UnitOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.FREEZE_UNITS)
        done = unit.unit_flow.freeze(
            who.college_id, who.user_id, UnitId(unit_id), expected_version=body.expected_version
        )
        return unit_out(unit, who.college_id, done)


@router.get(
    "/units/{unit_id}/frozen-record",
    response_model=FrozenRecordOut,
    responses={404: {"model": ErrorOut}},
    summary="The frozen record of a frozen unit (Governor); kept when content is deleted",
)
def frozen_record(unit_id: UUID, who: PrincipalDep, backends: BackendsDep) -> FrozenRecordOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.FREEZE_UNITS)
        record = unit.unit_flow.frozen_record(who.college_id, who.user_id, UnitId(unit_id))
        return FrozenRecordOut(
            unit_id=record.unit_id,
            unit_code=record.unit_code,
            year=record.year,
            subject_code=record.subject_code,
            subject_name=record.subject_name,
            frozen_by=owner_out(unit, who.college_id, record.frozen_by),
            frozen_at=record.frozen_at,
            papers=[
                FrozenPaperOut(
                    paper_id=UUID(str(p["paper_id"])),
                    version=int(str(p["version"])),
                    title=str(p["title"]),
                    exam_code=str(p["exam_code"]),
                    course_code=str(p["course_code"]),
                    total_marks=float(str(p["total_marks"])),
                )
                for p in record.papers
            ],
            booklets=[
                FrozenBookletOut(
                    booklet_id=b.booklet_id,
                    student_id=b.student_id,
                    paper_id=b.paper_id,
                    paper_version=b.paper_version,
                    outcome=b.outcome.value,
                    reason=None if b.reason is None else b.reason.value,
                    total=None if b.total is None else float(b.total),
                    max_marks=float(b.max_marks),
                    sheets=[
                        FrozenSheetOut(
                            version=x.version,
                            total=float(x.total),
                            max_marks=float(x.max_marks),
                            issued_at=x.issued_at,
                            note=x.note,
                        )
                        for x in b.sheets
                    ],
                    answers=[
                        FrozenAnswerOut(
                            slot_label=a.slot_label,
                            question_id=a.question_id,
                            question_version=a.question_version,
                            question_code=a.question_code,
                            max_marks=float(a.max_marks),
                            ai_mark=None if a.ai_mark is None else float(a.ai_mark),
                            sme_mark=None if a.sme_mark is None else float(a.sme_mark),
                            counted=a.counted,
                        )
                        for a in b.answers
                    ],
                )
                for b in record.booklets
            ],
        )
