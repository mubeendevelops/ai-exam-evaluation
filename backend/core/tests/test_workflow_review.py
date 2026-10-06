"""The teacher's review (P15): opening and the lock, per-answer decisions, booklet approval,
amendments with result sheet versions, stale writes, and deletion under a lock."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tarn_core.domain.booklet import AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.review import AmendmentOutcome
from tarn_core.domain.scoring import AnswerFlag
from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import (
    BookletLockedError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    RescorePendingError,
    StaleWriteError,
)
from tarn_core.ids import AnswerId, AnswerScoreId, UserId
from tarn_core.services.workflow import LockPolicy
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    ci_shaped_blueprint,
    make_services,
)
from tarn_core.testing.workflow import GOOD, HALF, Workflow, scored_booklet, workflow


def setup(
    answers: dict[str, list[str]] | None = None,
) -> tuple[InMemory, CollegeFixture, Booklet, Workflow]:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = scored_booklet(
        mem, college, ci_shaped_blueprint(mem, college), answers or {"1": GOOD, "2": HALF}
    )
    return mem, college, booklet, workflow(mem, policy=LockPolicy(timeout=timedelta(minutes=15)))


def second_teacher(mem: InMemory, college: CollegeFixture) -> User:
    other = User(
        id=UserId(mem.ids.new()),
        college_id=college.id,
        display_name="Teacher A2",
        email="teacher.a2@example.test",
        role=Role.TEACHER,
    )
    mem.users.save(college.id, other)
    return other


def answer_of(mem: InMemory, booklet: Booklet, label: str) -> tuple[AnswerId, int]:
    a = next(
        a for a in mem.booklets.answers(booklet.college_id, booklet.id) if a.slot_label == label
    )
    return a.id, a.version


def approve_all(mem: InMemory, college: CollegeFixture, booklet: Booklet, wf: Workflow) -> None:
    for a in mem.booklets.answers(college.id, booklet.id):
        if a.status is not AnswerStatus.APPROVED:
            wf.review.approve_answer(
                college.id, college.teacher.id, booklet.id, a.id, expected_version=a.version
            )


# --- opening and the lock -----------------------------------------------------------------


def test_first_open_starts_the_review_and_takes_the_lock() -> None:
    mem, college, booklet, wf = setup()
    assert booklet.status is BookletStatus.SCORED
    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    assert opened.booklet.status is BookletStatus.IN_REVIEW
    assert opened.booklet.version == booklet.version + 1
    assert opened.lock.holder == college.teacher.id
    assert opened.lock.expires_at == mem.clock.now() + timedelta(minutes=15)
    assert opened.rescoring == ()
    # Opening again refreshes; the status stays.
    mem.clock.advance(minutes=5)
    again = wf.review.open(college.id, college.teacher.id, booklet.id)
    assert again.booklet.version == opened.booklet.version
    assert again.lock.expires_at == mem.clock.now() + timedelta(minutes=15)
    assert again.lock.acquired_at == opened.lock.acquired_at


def test_a_second_teacher_is_refused_until_the_lock_lapses_or_is_released() -> None:
    mem, college, booklet, wf = setup()
    other = second_teacher(mem, college)
    wf.review.open(college.id, college.teacher.id, booklet.id)
    with pytest.raises(BookletLockedError) as refused:
        wf.review.open(college.id, other.id, booklet.id)
    assert refused.value.holder == college.teacher.id
    a, v = answer_of(mem, booklet, "1")
    with pytest.raises(BookletLockedError):
        wf.review.approve_answer(college.id, other.id, booklet.id, a, expected_version=v)

    # Released on close.
    assert wf.review.close(college.id, college.teacher.id, booklet.id)
    assert not wf.review.close(college.id, college.teacher.id, booklet.id)  # nothing held
    assert wf.review.open(college.id, other.id, booklet.id).lock.holder == other.id


def test_lock_expiry_after_inactivity() -> None:
    mem, college, booklet, wf = setup()
    other = second_teacher(mem, college)
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    # A write moves the expiry on.
    mem.clock.advance(minutes=10)
    wf.review.skip(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    mem.clock.advance(minutes=10)
    with pytest.raises(BookletLockedError):
        wf.review.open(college.id, other.id, booklet.id)
    # Fifteen quiet minutes later anyone may take it over; the takeover is recorded.
    mem.clock.advance(minutes=6)
    taken = wf.review.open(college.id, other.id, booklet.id)
    assert taken.lock.holder == other.id
    event = mem.audit.events[-1]
    assert event.before == {"status": "in_review", "lapsed_lock_of": str(college.teacher.id)}
    # The first teacher's next write is refused.
    a, v = answer_of(mem, booklet, "1")
    with pytest.raises(BookletLockedError):
        wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)


def test_an_own_lapsed_lock_is_renewed_by_the_next_write() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    mem.clock.advance(hours=2)
    a, v = answer_of(mem, booklet, "1")
    wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    lock = wf.guard.current(college.id, booklet.id)
    assert lock is not None and lock.expires_at == mem.clock.now() + timedelta(minutes=15)


def test_writes_need_the_lock() -> None:
    mem, college, booklet, wf = setup()
    a, v = answer_of(mem, booklet, "1")
    with pytest.raises(BookletLockedError, match="open the booklet"):
        wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)


# --- answer decisions -----------------------------------------------------------------------


def test_accepting_the_ai_mark_is_an_approval_with_both_marks() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    decision = wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, a, expected_version=v, tags=[" neat ", "neat"]
    )
    assert decision.answer.status is AnswerStatus.APPROVED and decision.answer.version == v + 1
    review = decision.review
    assert review is not None
    assert review.ai_mark == review.teacher_mark == Decimal(2)
    assert not review.overridden and review.tags == ("neat",)
    assert review.answer_score_id == mem.scores.scores(college.id, a)[-1].id


def test_an_override_stores_both_marks() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "2")
    ai = mem.scores.scores(college.id, a)[-1].mark
    decision = wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        a,
        expected_version=v,
        teacher_mark=Decimal("1.5"),
        remarks="Second item missing.",
    )
    assert decision.review is not None
    assert decision.review.ai_mark == ai and decision.review.teacher_mark == Decimal("1.5")
    assert decision.review.overridden and decision.review.remarks == "Second item missing."
    totals = make_services(mem).totals.preview(college.id, booklet.id)
    assert {s.slot_label: s.mark for s in totals.result.slots}["2"] == Decimal("1.5")


@pytest.mark.parametrize("mark", ["-0.5", "2.5", "0.3"])
def test_the_teacher_mark_stays_in_range_and_on_the_step(mark: str) -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    with pytest.raises(InvariantError):
        wf.review.approve_answer(
            college.id,
            college.teacher.id,
            booklet.id,
            a,
            expected_version=v,
            teacher_mark=Decimal(mark),
        )


def test_too_many_or_too_long_tags_are_refused() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    for tags in (["x" * 41], [f"t{n}" for n in range(11)], [""]):
        with pytest.raises(InvariantError):
            wf.review.approve_answer(
                college.id, college.teacher.id, booklet.id, a, expected_version=v, tags=tags
            )


def test_an_answer_without_an_ai_mark_needs_the_teachers_mark() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    score = mem.scores.scores(college.id, a)[-1]
    manual = replace(
        score,
        id=AnswerScoreId(mem.ids.new()),
        criterion_scores=(),
        mark=None,
        flags=(AnswerFlag.MARK_MANUALLY,),
    )
    mem.scores.save_score(college.id, manual)
    with pytest.raises(InvariantError, match="teacher's mark"):
        wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    decision = wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, a, expected_version=v, teacher_mark=Decimal(1)
    )
    assert decision.review is not None
    assert decision.review.ai_mark is None and decision.review.answer_score_id is None


def test_skip_and_return() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    skipped = wf.review.skip(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    assert skipped.answer.status is AnswerStatus.SKIPPED
    with pytest.raises(IllegalTransitionError):
        wf.review.skip(college.id, college.teacher.id, booklet.id, a, expected_version=v + 1)
    approved = wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, a, expected_version=v + 1
    )
    assert approved.answer.status is AnswerStatus.APPROVED


def test_reopening_before_booklet_approval_takes_the_approval_back() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    reopened = wf.review.reopen(
        college.id, college.teacher.id, booklet.id, a, expected_version=v + 1
    )
    assert reopened.answer.status is AnswerStatus.SUGGESTED and reopened.amendment is None
    assert mem.booklets.amendments(college.id, booklet.id) == []


def test_answers_are_decided_only_in_review() -> None:
    mem, college, booklet, wf = setup()
    # Holding the lock without the review having started is impossible: open starts it. A
    # segmented booklet (not scored yet) can be opened, but not decided.
    mem.booklets.save(college.id, replace(booklet, status=BookletStatus.SEGMENTED))
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    with pytest.raises(IllegalTransitionError):
        wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)


def test_another_booklets_answer_is_not_found() -> None:
    mem, college, booklet, wf = setup()
    other = scored_booklet(mem, college, ci_shaped_blueprint(mem, college), {"1": GOOD})
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, other, "1")
    with pytest.raises(NotFoundError):
        wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)


# --- stale writes ---------------------------------------------------------------------------


def test_stale_answer_and_booklet_versions_are_rejected() -> None:
    mem, college, booklet, wf = setup()
    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    wf.review.skip(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    # The other tab still shows version v.
    with pytest.raises(StaleWriteError):
        wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    approve_all(mem, college, booklet, wf)
    with pytest.raises(StaleWriteError):
        wf.review.approve_booklet(
            college.id, college.teacher.id, booklet.id, expected_version=opened.booklet.version - 1
        )


def test_a_new_suggestion_makes_the_old_screen_stale_and_pending_blocks_approval() -> None:
    mem, college, booklet, wf = setup()
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "2")
    # Queue the re-score without running it: the answer waits for its new suggestion.
    held = workflow(mem, rescorer=_Nothing())
    held.rescore.request(college.id, college.teacher.id, [a])
    pending = mem.booklets.get_answer(college.id, a)
    assert pending.rescore_pending and pending.version == v + 1
    with pytest.raises(RescorePendingError):
        wf.review.approve_answer(
            college.id, college.teacher.id, booklet.id, a, expected_version=v + 1
        )
    view = wf.review.view(college.id, booklet.id)
    assert "2" in view.waiting


class _Nothing:
    def rescore(self, college_id: object, actor_id: object, answer_ids: object) -> None:
        return None


# --- booklet approval and amendments -------------------------------------------------------


def test_booklet_approval_needs_every_answer_and_issues_sheet_v1() -> None:
    mem, college, booklet, wf = setup()
    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    wf.review.approve_answer(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    view = wf.review.view(college.id, booklet.id)
    assert view.waiting == ("2",) and not view.can_approve
    with pytest.raises(InvariantError, match="waiting: 2"):
        wf.review.approve_booklet(
            college.id, college.teacher.id, booklet.id, expected_version=opened.booklet.version
        )
    approve_all(mem, college, booklet, wf)
    assert wf.review.view(college.id, booklet.id).can_approve
    approved, sheet = wf.review.approve_booklet(
        college.id, college.teacher.id, booklet.id, expected_version=opened.booklet.version
    )
    assert approved.status is BookletStatus.APPROVED
    assert sheet.version == 1 and sheet.note == ""
    lines = {ln.slot_label: ln for ln in sheet.lines}
    assert lines["1"].mark == Decimal(2) and lines["1"].counted
    assert lines["7"].mark is None and lines["7"].reason == "not attempted"
    # Approving twice is an illegal move.
    with pytest.raises(IllegalTransitionError):
        wf.review.approve_booklet(
            college.id, college.teacher.id, booklet.id, expected_version=approved.version
        )


def approved_booklet() -> tuple[InMemory, CollegeFixture, Booklet, Workflow]:
    mem, college, booklet, wf = setup({"1": GOOD, "2": HALF, "3": GOOD})
    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    approve_all(mem, college, booklet, wf)
    approved, _ = wf.review.approve_booklet(
        college.id, college.teacher.id, booklet.id, expected_version=opened.booklet.version
    )
    return mem, college, approved, wf


def test_amendment_issues_v2_and_keeps_v1() -> None:
    mem, college, booklet, wf = approved_booklet()
    a, v = answer_of(mem, booklet, "2")
    first_review = mem.scores.reviews(college.id, a)[-1]
    reopened = wf.review.reopen(
        college.id, college.teacher.id, booklet.id, a, expected_version=v, reason="Recounted"
    )
    assert reopened.booklet.status is BookletStatus.AMENDMENT_IN_PROGRESS
    assert reopened.booklet.approved  # the badge: v1 still stands
    assert reopened.answer.status is AnswerStatus.SUGGESTED
    assert reopened.amendment is not None and reopened.amendment.base_review == first_review.id
    view = wf.review.view(college.id, booklet.id)
    assert [s.version for s in view.sheets] == [1]
    assert next(x for x in view.answers if x.answer.id == a).draft is not None

    # Other answers are not decided during an amendment unless reopened.
    b, bv = answer_of(mem, booklet, "1")
    with pytest.raises(IllegalTransitionError):
        wf.review.skip(college.id, college.teacher.id, booklet.id, b, expected_version=bv)

    decision = wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        a,
        expected_version=v + 1,
        teacher_mark=Decimal(2),
    )
    assert decision.sheet is not None and decision.sheet.version == 2
    assert decision.sheet.note == "2: Recounted"
    assert decision.booklet.status is BookletStatus.APPROVED_AMENDED
    sheets = mem.sheets.versions(college.id, booklet.id)
    assert [s.version for s in sheets] == [1, 2]
    assert {ln.slot_label: ln.mark for ln in sheets[0].lines}["2"] != Decimal(2)
    assert {ln.slot_label: ln.mark for ln in sheets[1].lines}["2"] == Decimal(2)
    amendment = mem.booklets.amendments(college.id, booklet.id)[0]
    assert amendment.outcome is AmendmentOutcome.APPROVED and amendment.sheet_version == 2
    # Both approvals are kept.
    assert len(mem.scores.reviews(college.id, a)) == 2


def test_two_drafts_issue_one_sheet_when_the_last_is_approved() -> None:
    mem, college, booklet, wf = approved_booklet()
    a, av = answer_of(mem, booklet, "1")
    b, bv = answer_of(mem, booklet, "2")
    wf.review.reopen(college.id, college.teacher.id, booklet.id, a, expected_version=av)
    wf.review.reopen(college.id, college.teacher.id, booklet.id, b, expected_version=bv)
    first = wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        a,
        expected_version=av + 1,
        teacher_mark=Decimal(1),
    )
    assert first.sheet is None and first.booklet.status is BookletStatus.AMENDMENT_IN_PROGRESS
    last = wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, b, expected_version=bv + 1
    )
    assert last.sheet is not None and last.sheet.version == 2
    assert last.sheet.note == "1 amended; 2 amended"
    # A second amendment round gives v3.
    c, cv = answer_of(mem, booklet, "3")
    wf.review.reopen(college.id, college.teacher.id, booklet.id, c, expected_version=cv)
    third = wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, c, expected_version=cv + 1
    )
    assert third.sheet is not None and third.sheet.version == 3
    assert [s.version for s in mem.sheets.versions(college.id, booklet.id)] == [1, 2, 3]


def test_withdrawing_the_only_draft_restores_the_approval_without_a_sheet() -> None:
    mem, college, booklet, wf = approved_booklet()
    a, v = answer_of(mem, booklet, "1")
    wf.review.reopen(college.id, college.teacher.id, booklet.id, a, expected_version=v)
    withdrawn = wf.review.withdraw_draft(
        college.id, college.teacher.id, booklet.id, a, expected_version=v + 1
    )
    assert withdrawn.answer.status is AnswerStatus.APPROVED
    assert withdrawn.booklet.status is BookletStatus.APPROVED and withdrawn.sheet is None
    assert withdrawn.amendment is not None
    assert withdrawn.amendment.outcome is AmendmentOutcome.WITHDRAWN
    assert len(mem.sheets.versions(college.id, booklet.id)) == 1
    with pytest.raises(IllegalTransitionError):
        wf.review.withdraw_draft(
            college.id, college.teacher.id, booklet.id, a, expected_version=v + 2
        )


def test_withdrawing_after_another_draft_was_approved_issues_the_sheet() -> None:
    mem, college, booklet, wf = approved_booklet()
    a, av = answer_of(mem, booklet, "1")
    b, bv = answer_of(mem, booklet, "2")
    wf.review.reopen(college.id, college.teacher.id, booklet.id, a, expected_version=av)
    wf.review.reopen(college.id, college.teacher.id, booklet.id, b, expected_version=bv)
    wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        a,
        expected_version=av + 1,
        teacher_mark=Decimal(1),
    )
    done = wf.review.withdraw_draft(
        college.id, college.teacher.id, booklet.id, b, expected_version=bv + 1
    )
    assert done.sheet is not None and done.sheet.version == 2 and done.sheet.note == "1 amended"
    assert done.booklet.status is BookletStatus.APPROVED_AMENDED


def test_reopen_is_refused_before_scoring_is_done() -> None:
    mem, college, booklet, wf = setup()
    mem.booklets.save(college.id, replace(booklet, status=BookletStatus.SEGMENTED))
    wf.review.open(college.id, college.teacher.id, booklet.id)
    a, v = answer_of(mem, booklet, "1")
    with pytest.raises(IllegalTransitionError):
        wf.review.reopen(college.id, college.teacher.id, booklet.id, a, expected_version=v)


# --- deletion -------------------------------------------------------------------------------


def test_deletion_is_refused_while_another_teacher_has_the_booklet_open() -> None:
    mem, college, booklet, _ = approved_booklet()
    other = second_teacher(mem, college)
    services = make_services(mem)
    with pytest.raises(BookletLockedError):
        services.booklets.delete(college.id, other.id, booklet.id)
    services.booklets.delete(college.id, college.teacher.id, booklet.id)  # the holder may
    with pytest.raises(NotFoundError):
        mem.booklets.get(college.id, booklet.id)
    assert mem.sheets.versions(college.id, booklet.id) == []
    assert mem.booklets.amendments(college.id, booklet.id) == []
    event = mem.audit.events[-1]
    assert event.action.value == "booklet.deleted" and event.before is None and event.after is None


def test_deletion_after_the_lock_lapsed() -> None:
    mem, college, booklet, wf = setup()
    other = second_teacher(mem, college)
    wf.review.open(college.id, college.teacher.id, booklet.id)
    mem.clock.advance(minutes=16)
    make_services(mem).booklets.delete(college.id, other.id, booklet.id)
    assert all(b.id != booklet.id for b in mem.booklets.list(college.id))
