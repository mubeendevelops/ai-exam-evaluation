"""The teacher's review (P15, design.md "Workflow engine"): open and close a booklet (its lock),
the review view, per-answer decisions (approve, skip, reopen, withdraw a draft), booklet
approval with its result sheet versions, and OCR line corrections.

Every write needs the booklet's lock (423 otherwise) and carries the version it was based on
(409 when stale). Decisions return the whole review view, so the screen redraws from one
response. New suggestions after an edit or a changed key come from the worker; the answer
shows ``rescore_pending`` until then."""

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Response, status

from tarn_api.backends import Unit, unit_of_work
from tarn_api.routes.booklets import region_out
from tarn_api.schemas import (
    ApprovalOut,
    ApproveAnswerIn,
    CriterionResultOut,
    DraftOut,
    ErrorOut,
    ExpectedVersionIn,
    LockedOut,
    LockOut,
    RegionEditIn,
    RegionEditOut,
    ReopenIn,
    ResultSheetOut,
    ReviewAnswerOut,
    ReviewOut,
    SheetVersionOut,
    SlotResultOut,
    SuggestionOut,
    TotalsOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.review import BookletLock, ResultSheet
from tarn_core.domain.scoring import AnswerScore
from tarn_core.domain.tenancy import Role
from tarn_core.ids import AnswerId, BookletId, CollegeId, RegionId, UserId
from tarn_core.services.workflow import BookletReview

router = APIRouter(
    prefix="/api/v1",
    tags=["review"],
    responses={
        401: {"model": ErrorOut},
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
    },
)
WRITE_ERRORS: dict[int | str, dict[str, object]] = {
    409: {
        "model": ErrorOut,
        "description": "Stale version (reload), a move the workflow does not allow, a new "
        "suggestion still on its way, or an approved answer that must be reopened first.",
    },
    422: {"model": ErrorOut, "description": "A mark, tag or text the rules refuse."},
    423: {
        "model": LockedOut,
        "description": "Another teacher has the booklet open, or the caller has not opened it.",
    },
}


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _lock_out(unit: Unit, college_id: CollegeId, me: UserId, lock: BookletLock) -> LockOut:
    return LockOut(
        holder_id=lock.holder,
        holder_name=unit.scope.users.get(college_id, lock.holder).display_name,
        acquired_at=lock.acquired_at,
        expires_at=lock.expires_at,
        mine=lock.holder == me,
    )


def _suggestion(score: AnswerScore) -> SuggestionOut:
    return SuggestionOut(
        id=score.id,
        mark=_f(score.mark),
        mark_step=float(score.mark_step),
        flags=[f.value for f in score.flags],
        reasons=list(score.reasons),
        relevance=score.relevance,
        created_at=score.created_at,
        criteria=[
            CriterionResultOut(
                criterion_id=c.criterion.id,
                criterion_version=c.criterion.version,
                weight=float(c.weight),
                credit=float(c.credit),
                marks=float(c.marks),
                scorer=f"{c.scorer.name} {c.scorer.version}",
                flags=list(c.flags),
                similarity=c.similarity,
                reason=None if c.reason is None else c.reason.summary,
                matched=[] if c.reason is None else list(c.reason.matched),
                missing=[] if c.reason is None else list(c.reason.missing),
            )
            for c in score.criterion_scores
        ],
    )


def sheet_pdf_url(booklet_id: BookletId, sheet: ResultSheet) -> str | None:
    if sheet.pdf is None:
        return None
    return f"/api/v1/booklets/{booklet_id}/result-sheets/{sheet.version}/pdf"


def sheet_version_out(booklet_id: BookletId, sheet: ResultSheet) -> SheetVersionOut:
    return SheetVersionOut(
        version=sheet.version,
        total=float(sheet.total),
        max_marks=float(sheet.max_marks),
        issued_at=sheet.issued_at,
        note=sheet.note,
        pdf_url=sheet_pdf_url(booklet_id, sheet),
    )


def _sheet_out(booklet_id: BookletId, sheet: ResultSheet) -> ResultSheetOut:
    return ResultSheetOut(
        **sheet_version_out(booklet_id, sheet).model_dump(),
        id=sheet.id,
        issued_by=sheet.issued_by,
        lines=[
            SlotResultOut(
                section_label=ln.section_label,
                slot_label=ln.slot_label,
                mark=_f(ln.mark),
                counted=ln.counted,
                outcome=ln.reason,
            )
            for ln in sheet.lines
        ],
    )


def review_out(
    unit: Unit,
    college_id: CollegeId,
    me: UserId,
    view: BookletReview,
    *,
    rescoring: tuple[AnswerId, ...] = (),
    notices: tuple[str, ...] = (),
) -> ReviewOut:
    result = view.totals.result
    return ReviewOut(
        booklet_id=view.booklet.id,
        status=view.booklet.status.value,
        version=view.booklet.version,
        approved=view.booklet.approved,
        amendment_in_progress=view.booklet.status is BookletStatus.AMENDMENT_IN_PROGRESS,
        lock=None if view.lock is None else _lock_out(unit, college_id, me, view.lock),
        can_approve=view.can_approve,
        waiting=list(view.waiting),
        answers=[
            ReviewAnswerOut(
                id=a.answer.id,
                slot_label=a.answer.slot_label,
                status=a.answer.status.value,
                version=a.answer.version,
                rescore_pending=a.answer.rescore_pending,
                attempted=bool(a.answer.segment_ids),
                max_marks=float(a.max_marks),
                suggestion=None if a.score is None else _suggestion(a.score),
                approval=None
                if a.review is None
                else ApprovalOut(
                    id=a.review.id,
                    ai_mark=_f(a.review.ai_mark),
                    teacher_mark=float(a.review.teacher_mark),
                    overridden=a.review.overridden,
                    tags=list(a.review.tags),
                    remarks=a.review.remarks,
                    reviewer_id=a.review.reviewer,
                    reviewed_at=a.review.reviewed_at,
                ),
                draft=None
                if a.draft is None
                else DraftOut(
                    amendment_id=a.draft.id,
                    reason=a.draft.reason,
                    opened_by=a.draft.opened_by,
                    opened_at=a.draft.opened_at,
                ),
            )
            for a in view.answers
        ],
        totals=TotalsOut(
            total=float(result.total),
            max_marks=float(result.max_marks),
            slots=[
                SlotResultOut(
                    section_label=s.section_label,
                    slot_label=s.slot_label,
                    mark=_f(s.mark),
                    counted=s.counted,
                    outcome=s.outcome.value,
                )
                for s in result.slots
            ],
        ),
        sheets=[_sheet_out(view.booklet.id, s) for s in view.sheets],
        rescoring=list(rescoring),
        notices=list(notices),
    )


def _view(unit: Unit, who: PrincipalDep, booklet_id: BookletId) -> ReviewOut:
    view = unit.review(booklet_id).view(who.college_id, booklet_id)
    return review_out(unit, who.college_id, who.user_id, view)


# --- opening and reading -------------------------------------------------------------------


@router.post(
    "/booklets/{booklet_id}/lock",
    response_model=ReviewOut,
    responses={423: {"model": LockedOut}},
    summary="Open a booklet for review (take or refresh its lock)",
    description="The first open of a scored booklet starts its review (`in_review`). Call it "
    "again to keep the lock while reading (every write also refreshes it). Unapproved answers "
    "whose key, rubric, glossary or reference diagram changed since they were scored are sent "
    "for re-scoring (`rescoring`, `notices`).",
)
def open_booklet(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        review = unit.review(BookletId(booklet_id))
        opened = review.open(who.college_id, who.user_id, BookletId(booklet_id))
        view = review.view(who.college_id, BookletId(booklet_id))
        return review_out(
            unit,
            who.college_id,
            who.user_id,
            view,
            rescoring=opened.rescoring,
            notices=opened.notices,
        )


@router.delete(
    "/booklets/{booklet_id}/lock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Close a booklet (release the caller's lock)",
    description="Nothing happens when the caller holds no lock on it.",
)
def close_booklet(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.scope.booklets.get(who.college_id, BookletId(booklet_id))
        unit.review(BookletId(booklet_id)).close(who.college_id, who.user_id, BookletId(booklet_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/booklets/{booklet_id}/review",
    response_model=ReviewOut,
    summary="The review of a booklet: answers, suggestions, approvals, totals, sheets, lock",
)
def get_review(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        return _view(unit, who, BookletId(booklet_id))


@router.get(
    "/booklets/{booklet_id}/result-sheets",
    response_model=list[ResultSheetOut],
    summary="Every result sheet version of a booklet, oldest first",
)
def result_sheets(
    booklet_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> list[ResultSheetOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.scope.booklets.get(who.college_id, BookletId(booklet_id))
        sheets = unit.scope.sheets.versions(who.college_id, BookletId(booklet_id))
    return [_sheet_out(BookletId(booklet_id), s) for s in sheets]


# --- decisions ------------------------------------------------------------------------------


@router.post(
    "/booklets/{booklet_id}/answers/{answer_id}/approve",
    response_model=ReviewOut,
    responses=WRITE_ERRORS,
    summary="Approve an answer: accept the AI's mark or override it",
    description="Both marks are kept. While the booklet is amended, approving the last draft "
    "issues the next result sheet version.",
)
def approve_answer(
    booklet_id: UUID,
    answer_id: UUID,
    body: ApproveAnswerIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.review(BookletId(booklet_id)).approve_answer(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            AnswerId(answer_id),
            expected_version=body.expected_version,
            teacher_mark=None if body.teacher_mark is None else Decimal(str(body.teacher_mark)),
            tags=body.tags,
            remarks=body.remarks,
        )
        return _view(unit, who, BookletId(booklet_id))


@router.post(
    "/booklets/{booklet_id}/answers/{answer_id}/skip",
    response_model=ReviewOut,
    responses=WRITE_ERRORS,
    summary="Skip an answer for now (approve it later)",
)
def skip_answer(
    booklet_id: UUID,
    answer_id: UUID,
    body: ExpectedVersionIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.review(BookletId(booklet_id)).skip(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            AnswerId(answer_id),
            expected_version=body.expected_version,
        )
        return _view(unit, who, BookletId(booklet_id))


@router.post(
    "/booklets/{booklet_id}/answers/{answer_id}/reopen",
    response_model=ReviewOut,
    responses=WRITE_ERRORS,
    summary="Reopen an approved answer",
    description="Before the booklet is approved this takes the approval back. Afterwards it "
    "opens an amendment: the answer becomes a draft, the booklet stays approved with the "
    "`amendment_in_progress` badge, and the issued sheets stay valid. The reason is optional.",
)
def reopen_answer(
    booklet_id: UUID,
    answer_id: UUID,
    body: ReopenIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.review(BookletId(booklet_id)).reopen(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            AnswerId(answer_id),
            expected_version=body.expected_version,
            reason=body.reason,
        )
        return _view(unit, who, BookletId(booklet_id))


@router.post(
    "/booklets/{booklet_id}/answers/{answer_id}/withdraw",
    response_model=ReviewOut,
    responses=WRITE_ERRORS,
    summary="Withdraw an amendment draft: the earlier approval stands again",
)
def withdraw_draft(
    booklet_id: UUID,
    answer_id: UUID,
    body: ExpectedVersionIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.review(BookletId(booklet_id)).withdraw_draft(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            AnswerId(answer_id),
            expected_version=body.expected_version,
        )
        return _view(unit, who, BookletId(booklet_id))


@router.post(
    "/booklets/{booklet_id}/approve",
    response_model=ReviewOut,
    responses=WRITE_ERRORS,
    summary="Approve the booklet: result sheet v1",
    description="Every attempted answer must be approved first (`can_approve`).",
)
def approve_booklet(
    booklet_id: UUID, body: ExpectedVersionIn, who: PrincipalDep, backends: BackendsDep
) -> ReviewOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.review(BookletId(booklet_id)).approve_booklet(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            expected_version=body.expected_version,
        )
        return _view(unit, who, BookletId(booklet_id))


# --- OCR corrections ------------------------------------------------------------------------


@router.post(
    "/booklets/{booklet_id}/regions/{region_id}",
    response_model=RegionEditOut,
    responses=WRITE_ERRORS,
    summary="Correct an OCR line or mark it struck out",
    description="The answers holding the line are re-scored by the worker (`rescoring`); an "
    "approved answer must be reopened first. The text before and after is kept with the "
    "booklet (not in the audit log).",
)
def edit_region(
    booklet_id: UUID,
    region_id: UUID,
    body: RegionEditIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> RegionEditOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        done = unit.text_editor(BookletId(booklet_id)).correct(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            RegionId(region_id),
            expected_version=body.expected_version,
            text=body.text,
            struck_out=body.struck_out,
        )
    return RegionEditOut(
        booklet_version=done.booklet.version,
        region=region_out(done.region),
        rescoring=list(done.rescoring),
    )
