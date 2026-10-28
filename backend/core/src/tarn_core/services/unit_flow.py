"""Submitting, returning and freezing units (P28, D164).

- **Submit** (the owning SME): the whole unit goes to the admin. Every booklet is approved, or
  carries a reason it was not marked (absent, unreadable, or other with a note). Booklets still
  in the machine stages and open amendment drafts are refused: they are still changing. The
  locks on the unit's booklets are released. From then on nothing in the unit changes
  (``services.unit_guard``).
- **Return** (the admin, with a note): the unit is open again for its SME, who is emailed.
- **Freeze** (the Governor): the unit is signed off for good. The frozen record (marks per
  booklet and answer, the paper versions with their slots, the result sheet versions) is
  written in the same transaction; reports read it (D168), and it stays when the paper and its
  questions are deleted (D166).

The audit log holds ids, counts and reason codes only: notes may name students and stay in
the submission rows."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import APPROVED_STATUSES, AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.common import JsonValue
from tarn_core.domain.content import Question, Subject
from tarn_core.domain.personas import Capability
from tarn_core.domain.unit import (
    FrozenAnswer,
    FrozenBooklet,
    FrozenOutcome,
    FrozenSheet,
    FrozenUnit,
    RequestState,
    Unit,
    UnitState,
    UnitSubmission,
    UnmarkedBooklet,
    UnmarkedReason,
)
from tarn_core.errors import (
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    StaleWriteError,
    UnitClosedError,
)
from tarn_core.ids import (
    BlueprintId,
    BookletId,
    CollegeId,
    QuestionId,
    UnitId,
    UnitSubmissionId,
    UserId,
)
from tarn_core.ports.identity import EmailMessage, Mailer
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    ResultSheetRepository,
    ScoreRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.access import require
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.services.transfers import check_reason
from tarn_core.services.unit_guard import closed_message

MARKED = frozenset({BookletStatus.APPROVED, BookletStatus.APPROVED_AMENDED})
"""Booklets that need no reason: a result sheet stands and no amendment draft is open."""

MACHINE = frozenset(
    {
        BookletStatus.UPLOADED,
        BookletStatus.PROCESSING,
        BookletStatus.PAGES_READY,
        BookletStatus.READING,
        BookletStatus.TEXT_READY,
        BookletStatus.SEGMENTED,
    }
)

NO_BOOKLETS = "The unit has no booklets yet: nothing to submit."
STILL_PROCESSING = (
    "{n} booklet(s) are still being processed: wait until they are ready, then submit."
)
OPEN_DRAFTS = "{n} booklet(s) have an amendment in progress: approve or withdraw the draft first."
NEEDS_REASON = "Give a reason for each booklet that is not marked ({n} without one)."
NO_REASON_NEEDED = "A marked booklet needs no reason: remove it from the list."
NOT_IN_UNIT = "A booklet in the list is not in this unit."
NOT_SUBMITTED = "Only a submitted unit can be returned or frozen."


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperSummary:
    """What lists show of a paper; read from the frozen record once the paper is deleted."""

    id: BlueprintId
    version: int
    title: str
    exam_code: str
    course_code: str
    total_marks: Decimal


def summary_of(paper: ExamBlueprint) -> PaperSummary:
    return PaperSummary(
        id=paper.id,
        version=paper.meta.version,
        title=paper.title,
        exam_code=paper.exam_code,
        course_code=paper.course_code,
        total_marks=paper.total_marks,
    )


def paper_summary(
    content: ContentRepository,
    units: UnitRepository,
    college_id: CollegeId,
    unit_id: UnitId,
    paper_id: BlueprintId,
    version: int | None = None,
) -> PaperSummary:
    """The paper as stored, or as the unit's frozen record keeps it after deletion (D166)."""
    try:
        return summary_of(content.get(college_id, ExamBlueprint, paper_id, version))
    except NotFoundError:
        record = units.frozen(college_id, unit_id)
        if record is None:
            raise
    try:
        snap = record.paper(paper_id, version)
    except InvariantError:
        raise NotFoundError(f"blueprint {paper_id}") from None
    return PaperSummary(
        id=paper_id,
        version=int(str(snap["version"])),
        title=str(snap["title"]),
        exam_code=str(snap["exam_code"]),
        course_code=str(snap["course_code"]),
        total_marks=Decimal(str(snap["total_marks"])),
    )


class UnitFlowService:
    def __init__(
        self,
        *,
        units: UnitRepository,
        users: UserRepository,
        booklets: BookletRepository,
        scores: ScoreRepository,
        sheets: ResultSheetRepository,
        content: ContentRepository,
        mailer: Mailer,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._users = users
        self._booklets = booklets
        self._scores = scores
        self._sheets = sheets
        self._content = content
        self._mailer = mailer
        self._rt = runtime

    # --- reading -----------------------------------------------------------------------------

    def latest_submission(self, college_id: CollegeId, unit_id: UnitId) -> UnitSubmission | None:
        found = self._units.submissions(college_id, unit_id)
        return found[-1] if found else None

    def frozen_record(self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId) -> FrozenUnit:
        require(self._users.get(college_id, actor_id), Capability.FREEZE_UNITS)
        record = self._units.frozen(college_id, unit_id)
        if record is None:
            raise NotFoundError(f"frozen record of unit {unit_id}")
        return record

    # --- submit ------------------------------------------------------------------------------

    def submit(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        unit_id: UnitId,
        *,
        unmarked: Sequence[UnmarkedBooklet],
        expected_version: int | None = None,
    ) -> Unit:
        """Raises ``NotFoundError`` (not the caller's unit), ``StaleWriteError``,
        ``IllegalTransitionError`` (not open, booklets still processing or with open drafts,
        no booklets, unmarked booklets without a reason) and ``InvariantError`` (a reason for
        a marked booklet or another unit's booklet)."""
        require(self._users.get(college_id, actor_id), Capability.SUBMIT_UNITS)
        unit = self._units.get(college_id, unit_id)
        if unit.owner_id != actor_id:
            raise NotFoundError(f"unit {unit_id}")
        self._check_version(unit, expected_version)
        if not unit.open:
            raise UnitClosedError(closed_message(unit))
        booklets = list(self._booklets.in_unit(college_id, unit.id))
        if not booklets:
            raise IllegalTransitionError(NO_BOOKLETS)
        processing = [b for b in booklets if b.status in MACHINE]
        if processing:
            raise IllegalTransitionError(STILL_PROCESSING.format(n=len(processing)))
        drafts = [b for b in booklets if b.status is BookletStatus.AMENDMENT_IN_PROGRESS]
        if drafts:
            raise IllegalTransitionError(OPEN_DRAFTS.format(n=len(drafts)))
        by_id = {b.id: b for b in booklets}
        reasons = {u.booklet_id: u for u in unmarked}
        if len(reasons) != len(unmarked):
            raise InvariantError("A booklet has one reason.")
        for booklet_id in reasons:
            booklet = by_id.get(booklet_id)
            if booklet is None:
                raise InvariantError(NOT_IN_UNIT)
            if booklet.status in MARKED:
                raise InvariantError(NO_REASON_NEEDED)
        missing = [b for b in booklets if b.status not in MARKED and b.id not in reasons]
        if missing:
            raise IllegalTransitionError(NEEDS_REASON.format(n=len(missing)))
        # Booklet rows first: once the unit is submitted the database refuses them (0016).
        released = 0
        for booklet in booklets:
            if self._booklets.lock(college_id, booklet.id) is not None:
                self._booklets.delete_lock(college_id, booklet.id)
                released += 1
            if booklet.id in reasons:
                # A re-score still on its way stands down (the worker skips closed units).
                for answer in self._booklets.answers(college_id, booklet.id):
                    if answer.rescore_pending:
                        self._booklets.save_answer(
                            college_id,
                            replace(answer, rescore_pending=False, version=answer.version + 1),
                        )
        # A request to move the unit is moot once it is submitted: it is withdrawn.
        request = self._units.pending_request(college_id, unit.id)
        if request is not None:
            self._units.save_request(
                college_id,
                replace(
                    request,
                    state=RequestState.WITHDRAWN,
                    closed_by=actor_id,
                    closed_at=self._rt.clock.now(),
                ),
            )
        submitted = unit.moved_to(UnitState.SUBMITTED)
        self._units.save(college_id, submitted)
        submission = UnitSubmission(
            id=self._rt.new_id(UnitSubmissionId),
            college_id=college_id,
            unit_id=unit.id,
            submitted_by=actor_id,
            submitted_at=self._rt.clock.now(),
            unmarked=tuple(reasons[b.id] for b in booklets if b.id in reasons),
        )
        self._units.save_submission(college_id, submission)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.UNIT_SUBMITTED,
            before={"unit_id": str(unit.id), "state": unit.state.value, "version": unit.version},
            after={
                "unit_id": str(unit.id),
                "state": submitted.state.value,
                "version": submitted.version,
                "submission_id": str(submission.id),
                "booklets": len(booklets),
                "marked": len(booklets) - len(reasons),
                "unmarked": {
                    r.value: sum(1 for u in reasons.values() if u.reason is r)
                    for r in UnmarkedReason
                },
                "locks_released": released,
                "request_withdrawn": None if request is None else str(request.id),
            },
        )
        return submitted

    # --- return ------------------------------------------------------------------------------

    def return_(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        unit_id: UnitId,
        *,
        note: str,
        expected_version: int | None = None,
    ) -> Unit:
        require(self._users.get(college_id, actor_id), Capability.RETURN_UNITS)
        note = check_reason(note, "note")
        unit = self._units.get(college_id, unit_id)
        self._check_version(unit, expected_version)
        if unit.state is not UnitState.SUBMITTED:
            raise IllegalTransitionError(NOT_SUBMITTED)
        submission = self.latest_submission(college_id, unit.id)
        if submission is None or submission.returned:
            raise IllegalTransitionError(NOT_SUBMITTED)
        reopened = unit.moved_to(UnitState.OPEN)
        self._units.save(college_id, reopened)
        now = self._rt.clock.now()
        self._units.save_submission(
            college_id, replace(submission, returned_by=actor_id, returned_at=now, return_note=note)
        )
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.UNIT_RETURNED,
            before={"unit_id": str(unit.id), "state": unit.state.value, "version": unit.version},
            after={
                "unit_id": str(unit.id),
                "state": reopened.state.value,
                "version": reopened.version,
                "submission_id": str(submission.id),
                "note_given": True,
            },
        )
        owner = self._users.get(college_id, unit.owner_id)
        self._mailer.send(
            self._returned_email(college_id, reopened, owner.email, owner.display_name, note)
        )
        return reopened

    def _returned_email(
        self, college_id: CollegeId, unit: Unit, to: str, name: str, note: str
    ) -> EmailMessage:
        paper = paper_summary(self._content, self._units, college_id, unit.id, unit.paper_id)
        return EmailMessage(
            to=to,
            subject=f"Tarn: unit {unit.code} returned to you",
            text=(
                f"Hello {name},\n\n"
                f"Your admin has returned the unit {unit.code} to you; it is open again.\n\n"
                f"Paper: {paper.title} ({paper.exam_code})\n"
                f"Student year: {unit.year}\n\n"
                f"The admin's note:\n{note}\n\n"
                "Sign in to Tarn AI Evaluation, make the changes and submit the unit again."
            ),
        )

    # --- freeze ------------------------------------------------------------------------------

    def freeze(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        unit_id: UnitId,
        *,
        expected_version: int | None = None,
    ) -> Unit:
        require(self._users.get(college_id, actor_id), Capability.FREEZE_UNITS)
        unit = self._units.get(college_id, unit_id)
        self._check_version(unit, expected_version)
        if unit.state is not UnitState.SUBMITTED:
            raise IllegalTransitionError(NOT_SUBMITTED)
        submission = self.latest_submission(college_id, unit.id)
        if submission is None or submission.returned:
            raise IllegalTransitionError(NOT_SUBMITTED)
        record = self._record(college_id, actor_id, unit, submission)
        frozen = unit.moved_to(UnitState.FROZEN)
        self._units.save(college_id, frozen)
        self._units.save_frozen(college_id, record)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.UNIT_FROZEN,
            before={"unit_id": str(unit.id), "state": unit.state.value, "version": unit.version},
            after={
                "unit_id": str(unit.id),
                "state": frozen.state.value,
                "version": frozen.version,
                "submission_id": str(submission.id),
                "booklets": len(record.booklets),
                "approved": sum(1 for b in record.booklets if b.outcome is FrozenOutcome.APPROVED),
                "answers": sum(len(b.answers) for b in record.booklets),
                "papers": len(record.papers),
            },
        )
        return frozen

    def _record(
        self, college_id: CollegeId, actor_id: UserId, unit: Unit, submission: UnitSubmission
    ) -> FrozenUnit:
        subject = self._content.get(college_id, Subject, unit.subject_id)
        reasons = {u.booklet_id: u for u in submission.unmarked}
        papers: dict[tuple[BlueprintId, int], ExamBlueprint] = {}
        codes: dict[QuestionId, str] = {}
        booklets = []
        for booklet in self._booklets.in_unit(college_id, unit.id):
            key = (BlueprintId(booklet.blueprint.id), booklet.blueprint.version)
            if key not in papers:
                papers[key] = self._content.get(college_id, ExamBlueprint, *key)
            booklets.append(self._frozen_booklet(college_id, booklet, papers[key], reasons, codes))
        return FrozenUnit(
            college_id=college_id,
            unit_id=unit.id,
            unit_code=unit.code,
            year=unit.year,
            subject_id=subject.id,
            subject_code=subject.code,
            subject_name=subject.name,
            submission_id=submission.id,
            frozen_by=actor_id,
            frozen_at=self._rt.clock.now(),
            papers=tuple(self._paper_json(college_id, p, codes) for p in papers.values()),
            booklets=tuple(booklets),
        )

    def _code(
        self, college_id: CollegeId, question_id: QuestionId, codes: dict[QuestionId, str]
    ) -> str:
        if question_id not in codes:
            try:
                codes[question_id] = self._content.get(college_id, Question, question_id).code
            except NotFoundError:
                codes[question_id] = ""
        return codes[question_id]

    def _paper_json(
        self, college_id: CollegeId, paper: ExamBlueprint, codes: dict[QuestionId, str]
    ) -> Mapping[str, JsonValue]:
        questions: list[JsonValue] = []
        for slot in paper.slots():
            for label, question_id, marks in leaves(slot):
                if question_id is None:
                    continue
                version = len(self._content.versions(college_id, Question, question_id))
                questions.append(
                    {
                        "slot_label": label,
                        "question_id": str(question_id),
                        "version": version,
                        "code": self._code(college_id, question_id, codes),
                        "max_marks": str(marks),
                    }
                )
        return {
            "paper_id": str(paper.id),
            "version": paper.meta.version,
            "title": paper.title,
            "exam_code": paper.exam_code,
            "course_code": paper.course_code,
            "total_marks": str(paper.total_marks),
            "mark_step": str(paper.mark_step),
            "document": blueprint_to_document(paper),
            "questions": questions,
        }

    def _frozen_booklet(
        self,
        college_id: CollegeId,
        booklet: Booklet,
        paper: ExamBlueprint,
        reasons: Mapping[BookletId, UnmarkedBooklet],
        codes: dict[QuestionId, str],
    ) -> FrozenBooklet:
        sheets = tuple(self._sheets.versions(college_id, booklet.id))
        approved = booklet.status in APPROVED_STATUSES and bool(sheets)
        counted = (
            {ln.slot_label: ln.counted for ln in sheets[-1].lines} if sheets and approved else {}
        )
        answers = []
        for answer in self._booklets.answers(college_id, booklet.id):
            if not answer.segment_ids:
                continue
            _, linked, max_marks = paper.leaf(answer.slot_label)
            scores = self._scores.scores(college_id, answer.id)
            reviews = self._scores.reviews(college_id, answer.id)
            score = scores[-1] if scores else None
            review = reviews[-1] if reviews and answer.status is AnswerStatus.APPROVED else None
            question_id = QuestionId(score.question.id) if score is not None else linked
            answers.append(
                FrozenAnswer(
                    booklet_id=booklet.id,
                    answer_id=answer.id,
                    slot_label=answer.slot_label,
                    question_id=question_id,
                    question_version=None if score is None else score.question.version,
                    question_code=self._code(college_id, question_id, codes),
                    max_marks=max_marks,
                    ai_mark=(
                        review.ai_mark if review is not None else score.mark if score else None
                    ),
                    sme_mark=None if review is None else review.teacher_mark,
                    reviewer=None if review is None else review.reviewer,
                    reviewed_at=None if review is None else review.reviewed_at,
                    counted=counted.get(answer.slot_label, False),
                )
            )
        reason = reasons.get(booklet.id)
        latest = sheets[-1] if sheets and approved else None
        return FrozenBooklet(
            booklet_id=booklet.id,
            student_id=booklet.student_id,
            paper_id=paper.id,
            paper_version=paper.meta.version,
            outcome=FrozenOutcome.APPROVED if reason is None else FrozenOutcome.UNMARKED,
            reason=None if reason is None else reason.reason,
            note="" if reason is None else reason.note,
            total=None if latest is None else latest.total,
            max_marks=paper.total_marks if latest is None else latest.max_marks,
            sheets=tuple(
                FrozenSheet(
                    version=s.version,
                    total=s.total,
                    max_marks=s.max_marks,
                    issued_by=s.issued_by,
                    issued_at=s.issued_at,
                    note=s.note,
                    pdf_key=None if s.pdf is None else s.pdf.value,
                )
                for s in sheets
            )
            if reason is None
            else (),
            answers=tuple(answers),
        )

    @staticmethod
    def _check_version(unit: Unit, expected: int | None) -> None:
        if expected is not None and unit.version != expected:
            raise StaleWriteError("The unit changed since it was loaded: reload it.")


__all__ = [
    "MACHINE",
    "MARKED",
    "PaperSummary",
    "UnitFlowService",
    "paper_summary",
    "summary_of",
]
