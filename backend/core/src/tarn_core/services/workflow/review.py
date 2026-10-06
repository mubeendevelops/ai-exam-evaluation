"""The teacher's review of a scored booklet (design.md "Workflow engine", baseline v1.3).

- Opening a booklet takes its lock; the first open moves it SCORED → IN_REVIEW. Answers whose
  key, rubric, glossary or reference diagram changed since they were scored, and that are not
  approved, are re-scored (with a notice on the new suggestion).
- Each answer is suggested, skipped or approved. Approving without a mark accepts the AI's
  mark; an override stores both marks. Tags and remarks go with the approval.
- The booklet is approved once every attempted answer is: result sheet v1 is issued.
- Reopening an answer of an approved booklet opens an amendment: a draft, with an optional
  reason. The booklet stays approved (AMENDMENT_IN_PROGRESS is its badge). When the last open
  draft is closed with at least one changed answer approved, the next sheet version is issued
  and the booklet becomes APPROVED_AMENDED; earlier sheets stay valid. Withdrawing a draft
  restores the earlier approval.

Every write holds the booklet lock and carries a version (``BookletGuard``); every transition
writes an audit event with before and after values. Remarks and reasons are kept with the
review and the amendment, not in the audit log (D113)."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import (
    APPROVED_STATUSES,
    REVIEW_STATUSES,
    Answer,
    AnswerStatus,
    Booklet,
    BookletStatus,
)
from tarn_core.domain.common import JsonValue
from tarn_core.domain.review import (
    MAX_TAG_CHARS,
    Amendment,
    AmendmentOutcome,
    BookletLock,
    ResultLine,
    ResultSheet,
    Review,
)
from tarn_core.domain.scoring import AnswerScore
from tarn_core.errors import (
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    RescorePendingError,
    StaleWriteError,
)
from tarn_core.ids import (
    AmendmentId,
    AnswerId,
    BookletId,
    CollegeId,
    ResultSheetId,
    ReviewId,
    UserId,
)
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    ResultSheetRepository,
    ScoreRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.scoring.changes import change_notice
from tarn_core.services.totals import TotalsPreview, TotalsService
from tarn_core.services.workflow.guard import BookletGuard
from tarn_core.services.workflow.rescore import CONTENT_CHANGED, RescoreRequests, stale_answers


@dataclass(frozen=True, slots=True, kw_only=True)
class Opened:
    booklet: Booklet
    lock: BookletLock
    rescoring: tuple[AnswerId, ...]
    """Answers sent for re-scoring because their content changed."""
    notices: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerReview:
    answer: Answer
    max_marks: Decimal
    score: AnswerScore | None
    """The latest AI suggestion."""
    review: Review | None
    """The latest approval (the one that stands while the answer is approved; the amended
    one while it is a draft)."""
    draft: Amendment | None
    """The open amendment, when the answer is a draft of an approved booklet."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BookletReview:
    booklet: Booklet
    lock: BookletLock | None
    answers: tuple[AnswerReview, ...]
    totals: TotalsPreview
    sheets: tuple[ResultSheet, ...]
    amendments: tuple[Amendment, ...]
    waiting: tuple[str, ...]
    """Questions whose answers still need a decision before the booklet can be approved."""

    @property
    def can_approve(self) -> bool:
        return self.booklet.status is BookletStatus.IN_REVIEW and not self.waiting


@dataclass(frozen=True, slots=True, kw_only=True)
class Decision:
    """What one answer decision did."""

    booklet: Booklet
    answer: Answer
    review: Review | None = None
    amendment: Amendment | None = None
    sheet: ResultSheet | None = None
    """Issued when this decision closed the last open draft of an amended booklet."""


class ReviewService:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        sheets: ResultSheetRepository,
        users: UserRepository,
        runtime: Runtime,
        guard: BookletGuard,
        rescore: RescoreRequests,
    ) -> None:
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._sheets = sheets
        self._users = users
        self._rt = runtime
        self._guard = guard
        self._rescore = rescore
        self._totals = TotalsService(booklets=booklets, scores=scores, content=content)

    # --- opening and closing -------------------------------------------------------------

    def open(self, college_id: CollegeId, actor: UserId, booklet_id: BookletId) -> Opened:
        """Take (or refresh) the lock. The first open of a scored booklet starts its review.
        Changed content re-scores the unapproved answers it affects."""
        acquired = self._guard.acquire(college_id, actor, booklet_id)
        booklet = self._booklets.get(college_id, booklet_id)
        before_status = booklet.status
        if booklet.status is BookletStatus.SCORED:
            booklet = booklet.moved_to(BookletStatus.IN_REVIEW)
            self._booklets.save(college_id, booklet)
        if acquired.fresh or before_status is not booklet.status:
            before: dict[str, JsonValue] = {"status": before_status.value}
            if acquired.taken_over is not None:
                before["lapsed_lock_of"] = str(acquired.taken_over.holder)
            self._rt.record(
                college_id,
                actor,
                AuditAction.BOOKLET_OPENED,
                booklet_id=booklet_id,
                before=before,
                after={
                    "status": booklet.status.value,
                    "version": booklet.version,
                    "lock_expires_at": acquired.lock.expires_at.isoformat(),
                },
            )
        rescoring: tuple[AnswerId, ...] = ()
        notices: list[str] = []
        if booklet.status in REVIEW_STATUSES:
            stale = stale_answers(self._booklets, self._scores, self._content, booklet)
            if stale:
                self._rescore.request(
                    college_id, actor, [s.answer_id for s in stale], reason=CONTENT_CHANGED
                )
                rescoring = tuple(s.answer_id for s in stale)
                notices = sorted({change_notice(s.changes) for s in stale})
        return Opened(
            booklet=self._booklets.get(college_id, booklet_id),
            lock=acquired.lock,
            rescoring=rescoring,
            notices=tuple(notices),
        )

    def close(self, college_id: CollegeId, actor: UserId, booklet_id: BookletId) -> bool:
        """Release the caller's lock; False (and nothing recorded) when they held none."""
        if not self._guard.release(college_id, actor, booklet_id):
            return False
        self._rt.record(
            college_id,
            actor,
            AuditAction.BOOKLET_CLOSED,
            booklet_id=booklet_id,
            before={"holder": str(actor)},
            after={"holder": None},
        )
        return True

    # --- reading -------------------------------------------------------------------------

    def view(self, college_id: CollegeId, booklet_id: BookletId) -> BookletReview:
        booklet = self._booklets.get(college_id, booklet_id)
        blueprint = self._blueprint(booklet)
        amendments = tuple(self._booklets.amendments(college_id, booklet_id))
        drafts = {a.answer_id: a for a in amendments if a.open}
        answers: list[AnswerReview] = []
        waiting: list[str] = []
        for answer in sorted(
            self._booklets.answers(college_id, booklet_id), key=lambda a: _order(a.slot_label)
        ):
            scores = self._scores.scores(college_id, answer.id)
            reviews = self._scores.reviews(college_id, answer.id)
            _, _, max_marks = blueprint.leaf(answer.slot_label)
            answers.append(
                AnswerReview(
                    answer=answer,
                    max_marks=max_marks,
                    score=scores[-1] if scores else None,
                    review=reviews[-1] if reviews else None,
                    draft=drafts.get(answer.id),
                )
            )
            if answer.segment_ids and (
                answer.status is not AnswerStatus.APPROVED or answer.rescore_pending
            ):
                waiting.append(answer.slot_label)
        return BookletReview(
            booklet=booklet,
            lock=self._guard.current(college_id, booklet_id),
            answers=tuple(answers),
            totals=self._totals.preview(college_id, booklet_id),
            sheets=tuple(self._sheets.versions(college_id, booklet_id)),
            amendments=amendments,
            waiting=tuple(waiting),
        )

    # --- answer decisions ----------------------------------------------------------------

    def approve_answer(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        answer_id: AnswerId,
        *,
        expected_version: int,
        teacher_mark: Decimal | None = None,
        tags: Sequence[str] = (),
        remarks: str = "",
    ) -> Decision:
        """Approve the AI's mark (``teacher_mark`` None) or override it. An answer with no AI
        mark ("mark manually") needs the teacher's mark."""
        booklet, answer = self._decidable(
            college_id, actor, booklet_id, answer_id, expected_version
        )
        if answer.rescore_pending:
            raise RescorePendingError("a new suggestion for this answer is on its way")
        approved = answer.moved_to(AnswerStatus.APPROVED)
        scores = self._scores.scores(college_id, answer_id)
        score = scores[-1] if scores else None
        ai_mark = None if score is None else score.mark
        mark = teacher_mark if teacher_mark is not None else ai_mark
        if mark is None:
            raise InvariantError("this answer has no AI mark: enter the teacher's mark")
        blueprint = self._blueprint(booklet)
        _, _, max_marks = blueprint.leaf(answer.slot_label)
        _check_mark(mark, max_marks, blueprint.mark_step)
        review = Review(
            id=self._rt.new_id(ReviewId),
            college_id=college_id,
            answer_id=answer_id,
            answer_score_id=None if score is None or ai_mark is None else score.id,
            ai_mark=ai_mark,
            teacher_mark=mark,
            reviewer=actor,
            reviewed_at=self._rt.clock.now(),
            tags=_tags(tags),
            remarks=remarks.strip(),
        )
        self._scores.save_review(college_id, review)
        self._booklets.save_answer(college_id, approved)
        draft = self._open_draft(college_id, booklet_id, answer_id)
        closed = None
        if draft is not None:
            closed = replace(
                draft,
                closed_by=actor,
                closed_at=self._rt.clock.now(),
                outcome=AmendmentOutcome.APPROVED,
            )
            self._booklets.save_amendment(college_id, closed)
        after: dict[str, JsonValue] = {
            "status": approved.status.value,
            "version": approved.version,
            "review_id": str(review.id),
            "answer_score_id": None
            if review.answer_score_id is None
            else str(review.answer_score_id),
            "ai_mark": _num(ai_mark),
            "teacher_mark": _num(mark),
            "overridden": review.overridden or ai_mark is None,
            "tags": list(review.tags),
            "remarks_given": bool(review.remarks),
        }
        if closed is not None:
            after["amendment_id"] = str(closed.id)
        self._rt.record(
            college_id,
            actor,
            AuditAction.ANSWER_APPROVED,
            booklet_id=booklet_id,
            answer_id=answer_id,
            before={"status": answer.status.value, "version": answer.version},
            after=after,
        )
        booklet, sheet = self._after_draft_closed(college_id, actor, booklet)
        return Decision(
            booklet=booklet, answer=approved, review=review, amendment=closed, sheet=sheet
        )

    def skip(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        answer_id: AnswerId,
        *,
        expected_version: int,
    ) -> Decision:
        """Leave the answer for later (it can be approved straight from skipped)."""
        booklet, answer = self._decidable(
            college_id, actor, booklet_id, answer_id, expected_version
        )
        skipped = answer.moved_to(AnswerStatus.SKIPPED)
        self._booklets.save_answer(college_id, skipped)
        self._rt.record(
            college_id,
            actor,
            AuditAction.ANSWER_SKIPPED,
            booklet_id=booklet_id,
            answer_id=answer_id,
            before={"status": answer.status.value, "version": answer.version},
            after={"status": skipped.status.value, "version": skipped.version},
        )
        return Decision(booklet=booklet, answer=skipped)

    def reopen(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        answer_id: AnswerId,
        *,
        expected_version: int,
        reason: str = "",
    ) -> Decision:
        """Reopen an approved answer. Before the booklet is approved this simply takes the
        approval back; afterwards it opens an amendment (``reason`` optional)."""
        booklet = self._guard.hold(college_id, actor, booklet_id)
        answer = self._answer(college_id, booklet_id, answer_id, expected_version)
        draft = answer.moved_to(AnswerStatus.SUGGESTED)
        if booklet.status is BookletStatus.IN_REVIEW:
            self._booklets.save_answer(college_id, draft)
            self._rt.record(
                college_id,
                actor,
                AuditAction.ANSWER_REOPENED,
                booklet_id=booklet_id,
                answer_id=answer_id,
                before={"status": answer.status.value, "version": answer.version},
                after={"status": draft.status.value, "version": draft.version},
            )
            return Decision(booklet=booklet, answer=draft)
        if booklet.status not in APPROVED_STATUSES:
            raise IllegalTransitionError(
                f"answers cannot be reopened while the booklet is {booklet.status}"
            )
        reviews = self._scores.reviews(college_id, answer_id)
        if not reviews:
            raise InvariantError("an approved answer has its approval")
        amendment = Amendment(
            id=self._rt.new_id(AmendmentId),
            college_id=college_id,
            booklet_id=booklet_id,
            answer_id=answer_id,
            base_review=reviews[-1].id,
            opened_by=actor,
            opened_at=self._rt.clock.now(),
            reason=reason.strip(),
        )
        before_status = booklet.status
        if booklet.status is not BookletStatus.AMENDMENT_IN_PROGRESS:
            booklet = booklet.moved_to(BookletStatus.AMENDMENT_IN_PROGRESS)
            self._booklets.save(college_id, booklet)
        self._booklets.save_answer(college_id, draft)
        self._booklets.save_amendment(college_id, amendment)
        self._rt.record(
            college_id,
            actor,
            AuditAction.AMENDMENT_OPENED,
            booklet_id=booklet_id,
            answer_id=answer_id,
            before={
                "status": answer.status.value,
                "version": answer.version,
                "booklet_status": before_status.value,
                "review_id": str(reviews[-1].id),
            },
            after={
                "status": draft.status.value,
                "version": draft.version,
                "booklet_status": booklet.status.value,
                "booklet_version": booklet.version,
                "amendment_id": str(amendment.id),
                "reason_given": bool(amendment.reason),
            },
        )
        return Decision(booklet=booklet, answer=draft, amendment=amendment)

    def withdraw_draft(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        answer_id: AnswerId,
        *,
        expected_version: int,
    ) -> Decision:
        """Give up an amendment: the earlier approval stands again."""
        booklet = self._guard.hold(college_id, actor, booklet_id)
        answer = self._answer(college_id, booklet_id, answer_id, expected_version)
        draft = self._open_draft(college_id, booklet_id, answer_id)
        if booklet.status is not BookletStatus.AMENDMENT_IN_PROGRESS or draft is None:
            raise IllegalTransitionError("this answer is not an amendment draft")
        if answer.rescore_pending:
            raise RescorePendingError("a new suggestion for this answer is on its way")
        restored = answer.moved_to(AnswerStatus.APPROVED)
        closed = replace(
            draft,
            closed_by=actor,
            closed_at=self._rt.clock.now(),
            outcome=AmendmentOutcome.WITHDRAWN,
        )
        self._booklets.save_answer(college_id, restored)
        self._booklets.save_amendment(college_id, closed)
        self._rt.record(
            college_id,
            actor,
            AuditAction.AMENDMENT_WITHDRAWN,
            booklet_id=booklet_id,
            answer_id=answer_id,
            before={"status": answer.status.value, "version": answer.version},
            after={
                "status": restored.status.value,
                "version": restored.version,
                "amendment_id": str(closed.id),
                "review_id": str(closed.base_review),
            },
        )
        booklet, sheet = self._after_draft_closed(college_id, actor, booklet)
        return Decision(booklet=booklet, answer=restored, amendment=closed, sheet=sheet)

    # --- the booklet ---------------------------------------------------------------------

    def approve_booklet(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        *,
        expected_version: int,
    ) -> tuple[Booklet, ResultSheet]:
        """Every attempted answer approved → APPROVED, result sheet v1."""
        booklet = self._guard.hold(college_id, actor, booklet_id, expected_version=expected_version)
        approved = booklet.moved_to(BookletStatus.APPROVED)
        self._check_all_approved(college_id, booklet_id)
        sheet = self._issue(college_id, actor, booklet, note="")
        self._booklets.save(college_id, approved)
        self._record_approval(college_id, actor, booklet, approved, sheet)
        return approved, sheet

    # --- internals -----------------------------------------------------------------------

    def _decidable(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        answer_id: AnswerId,
        expected_version: int,
    ) -> tuple[Booklet, Answer]:
        booklet = self._guard.hold(college_id, actor, booklet_id)
        if booklet.status not in REVIEW_STATUSES:
            raise IllegalTransitionError(
                f"answers are decided while the booklet is in review, not {booklet.status}"
            )
        answer = self._answer(college_id, booklet_id, answer_id, expected_version)
        if not answer.segment_ids:
            raise InvariantError("this answer has no text left: it counts as not attempted")
        if booklet.status is BookletStatus.AMENDMENT_IN_PROGRESS and (
            self._open_draft(college_id, booklet_id, answer_id) is None
        ):
            raise IllegalTransitionError("reopen the answer to amend it")
        return booklet, answer

    def _answer(
        self, college_id: CollegeId, booklet_id: BookletId, answer_id: AnswerId, expected: int
    ) -> Answer:
        answer = self._booklets.get_answer(college_id, answer_id)
        if answer.booklet_id != booklet_id:
            raise NotFoundError(f"answer {answer_id}")
        if answer.version != expected:
            raise StaleWriteError("the answer changed since it was loaded: reload it")
        return answer

    def _open_draft(
        self, college_id: CollegeId, booklet_id: BookletId, answer_id: AnswerId
    ) -> Amendment | None:
        return next(
            (
                a
                for a in self._booklets.amendments(college_id, booklet_id)
                if a.answer_id == answer_id and a.open
            ),
            None,
        )

    def _after_draft_closed(
        self, college_id: CollegeId, actor: UserId, booklet: Booklet
    ) -> tuple[Booklet, ResultSheet | None]:
        """After an amendment closed: with no draft left, issue the next sheet if a changed
        answer was approved, else go back to the booklet's earlier approved state."""
        if booklet.status is not BookletStatus.AMENDMENT_IN_PROGRESS:
            return booklet, None
        amendments = self._booklets.amendments(college_id, booklet.id)
        if any(a.open for a in amendments):
            return booklet, None
        unsheeted = [
            a
            for a in amendments
            if a.outcome is AmendmentOutcome.APPROVED and a.sheet_version is None
        ]
        if not unsheeted:
            earlier = len(self._sheets.versions(college_id, booklet.id))
            back = booklet.moved_to(
                BookletStatus.APPROVED if earlier <= 1 else BookletStatus.APPROVED_AMENDED
            )
            self._booklets.save(college_id, back)
            self._rt.record(
                college_id,
                actor,
                AuditAction.BOOKLET_APPROVED,
                booklet_id=booklet.id,
                before={"status": booklet.status.value, "version": booklet.version},
                after={
                    "status": back.status.value,
                    "version": back.version,
                    "sheet_version": earlier,
                },
            )
            return back, None
        self._check_all_approved(college_id, booklet.id)
        labels = {a.id: a.slot_label for a in self._booklets.answers(college_id, booklet.id)}
        parts = []
        for a in sorted(unsheeted, key=lambda a: _order(labels.get(a.answer_id, ""))):
            label = labels.get(a.answer_id, "?")
            parts.append(f"{label}: {a.reason}" if a.reason else f"{label} amended")
        sheet = self._issue(college_id, actor, booklet, note="; ".join(parts))
        for a in unsheeted:
            self._booklets.save_amendment(college_id, replace(a, sheet_version=sheet.version))
        amended = booklet.moved_to(BookletStatus.APPROVED_AMENDED)
        self._booklets.save(college_id, amended)
        self._record_approval(college_id, actor, booklet, amended, sheet)
        return amended, sheet

    def _check_all_approved(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        waiting = sorted(
            (
                a.slot_label
                for a in self._booklets.answers(college_id, booklet_id)
                if a.segment_ids and (a.status is not AnswerStatus.APPROVED or a.rescore_pending)
            ),
            key=_order,
        )
        if waiting:
            raise InvariantError(
                "every answer must be approved first; waiting: " + ", ".join(waiting)
            )

    def _issue(
        self, college_id: CollegeId, actor: UserId, booklet: Booklet, *, note: str
    ) -> ResultSheet:
        preview = self._totals.preview(college_id, booklet.id)
        if preview.unmarked:
            raise InvariantError("an answer has no mark")
        result = preview.result
        sheet = ResultSheet(
            id=self._rt.new_id(ResultSheetId),
            college_id=college_id,
            booklet_id=booklet.id,
            version=len(self._sheets.versions(college_id, booklet.id)) + 1,
            lines=tuple(
                ResultLine(
                    section_label=s.section_label,
                    slot_label=s.slot_label,
                    mark=s.mark,
                    counted=s.counted,
                    reason=s.outcome.value,
                )
                for s in result.slots
            ),
            total=result.total,
            max_marks=result.max_marks,
            issued_by=actor,
            issued_at=self._rt.clock.now(),
            note=note,
        )
        self._sheets.save(college_id, sheet)
        self._rt.record(
            college_id,
            actor,
            AuditAction.RESULT_SHEET_ISSUED,
            booklet_id=booklet.id,
            before={"version": sheet.version - 1 if sheet.version > 1 else None},
            after={
                "result_sheet_id": str(sheet.id),
                "version": sheet.version,
                "total": _num(sheet.total),
                "max_marks": _num(sheet.max_marks),
                "not_counted": [
                    ln.slot_label for ln in sheet.lines if ln.mark is not None and not ln.counted
                ],
            },
        )
        return sheet

    def _record_approval(
        self,
        college_id: CollegeId,
        actor: UserId,
        before: Booklet,
        after: Booklet,
        sheet: ResultSheet,
    ) -> None:
        self._rt.record(
            college_id,
            actor,
            AuditAction.BOOKLET_APPROVED,
            booklet_id=before.id,
            before={"status": before.status.value, "version": before.version},
            after={
                "status": after.status.value,
                "version": after.version,
                "sheet_version": sheet.version,
            },
        )

    def _blueprint(self, booklet: Booklet) -> ExamBlueprint:
        return self._content.get(ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version)


def _check_mark(mark: Decimal, max_marks: Decimal, step: Decimal) -> None:
    if not mark.is_finite() or not 0 <= mark <= max_marks:
        raise InvariantError(f"the mark must be between 0 and {max_marks}")
    if (mark / step) % 1 != 0:
        raise InvariantError(f"the mark must be a multiple of {step}")


def _tags(tags: Sequence[str]) -> tuple[str, ...]:
    cleaned = [t.strip() for t in tags]
    if any(not t or len(t) > MAX_TAG_CHARS for t in cleaned):
        raise InvariantError(f"a tag has 1 to {MAX_TAG_CHARS} characters")
    return tuple(dict.fromkeys(cleaned))


def _num(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _order(label: str) -> tuple[tuple[int, int | str], ...]:
    """Paper order of leaf labels: "2" < "10" < "10.a"."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in label.split("."))
