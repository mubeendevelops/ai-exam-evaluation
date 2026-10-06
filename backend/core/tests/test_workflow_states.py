"""The booklet and answer state machines (D106, D107): every allowed move, and every other
move refused."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tarn_core.domain.booklet import (
    ANSWER_TRANSITIONS,
    APPROVED_STATUSES,
    BOOKLET_TRANSITIONS,
    Answer,
    AnswerStatus,
    Booklet,
    BookletStatus,
)
from tarn_core.domain.common import ContentKind, ContentRef
from tarn_core.errors import IllegalTransitionError, InvariantError
from tarn_core.ids import AnswerId, BookletId, CollegeId, SegmentId, StudentId, UserId

S = BookletStatus


def booklet(status: BookletStatus) -> Booklet:
    return Booklet(
        id=BookletId(uuid4()),
        college_id=CollegeId(uuid4()),
        student_id=StudentId(uuid4()),
        blueprint=ContentRef(kind=ContentKind.BLUEPRINT, id=uuid4(), version=1),
        file_sha256="0" * 64,
        uploaded_by=UserId(uuid4()),
        uploaded_at=datetime(2026, 10, 6, tzinfo=UTC),
        status=status,
        failure_reason="unreadable_file" if status is S.FAILED else None,
        version=3,
    )


def answer(status: AnswerStatus) -> Answer:
    return Answer(
        id=AnswerId(uuid4()),
        college_id=CollegeId(uuid4()),
        booklet_id=BookletId(uuid4()),
        slot_label="1",
        segment_ids=(SegmentId(uuid4()),),
        status=status,
        version=2,
    )


def test_every_status_has_a_row_and_the_main_path_is_allowed() -> None:
    assert set(BOOKLET_TRANSITIONS) == set(BookletStatus)
    path = [
        S.UPLOADED,
        S.PROCESSING,
        S.NEEDS_RETAKE,
        S.PAGES_READY,
        S.READING,
        S.TEXT_READY,
        S.SEGMENTED,
        S.SCORED,
        S.IN_REVIEW,
        S.APPROVED,
        S.AMENDMENT_IN_PROGRESS,
        S.APPROVED_AMENDED,
        S.AMENDMENT_IN_PROGRESS,
        S.APPROVED_AMENDED,
    ]
    current = booklet(S.UPLOADED)
    for nxt in path[1:]:
        moved = current.moved_to(nxt)
        assert moved.status is nxt and moved.version == current.version + 1
        current = moved


def test_failed_is_final_and_only_machine_stages_fail() -> None:
    assert BOOKLET_TRANSITIONS[S.FAILED] == frozenset()
    failing = {s for s, nexts in BOOKLET_TRANSITIONS.items() if S.FAILED in nexts}
    assert failing == {
        S.UPLOADED,
        S.PROCESSING,
        S.PAGES_READY,
        S.READING,
        S.TEXT_READY,
        S.SEGMENTED,
    }
    failed = booklet(S.SEGMENTED).moved_to(S.FAILED, failure_reason="scoring_failed")
    assert failed.failure_reason == "scoring_failed"


def test_approved_statuses_keep_the_sheet_valid() -> None:
    assert {S.APPROVED, S.AMENDMENT_IN_PROGRESS, S.APPROVED_AMENDED} == APPROVED_STATUSES
    assert booklet(S.AMENDMENT_IN_PROGRESS).approved
    assert not booklet(S.IN_REVIEW).approved


@pytest.mark.parametrize(
    ("start", "end"),
    [(a, b) for a in BookletStatus for b in BookletStatus if b not in BOOKLET_TRANSITIONS[a]],
)
def test_illegal_booklet_moves_are_refused(start: BookletStatus, end: BookletStatus) -> None:
    with pytest.raises(IllegalTransitionError):
        booklet(start).moved_to(end, failure_reason="x" if end is S.FAILED else None)


def test_answer_moves() -> None:
    a = answer(AnswerStatus.SUGGESTED)
    skipped = a.moved_to(AnswerStatus.SKIPPED)
    approved = skipped.moved_to(AnswerStatus.APPROVED)
    reopened = approved.moved_to(AnswerStatus.SUGGESTED)
    assert [x.version for x in (a, skipped, approved, reopened)] == [2, 3, 4, 5]
    assert a.moved_to(AnswerStatus.APPROVED).status is AnswerStatus.APPROVED


@pytest.mark.parametrize(
    ("start", "end"),
    [(a, b) for a in AnswerStatus for b in AnswerStatus if b not in ANSWER_TRANSITIONS[a]],
)
def test_illegal_answer_moves_are_refused(start: AnswerStatus, end: AnswerStatus) -> None:
    with pytest.raises(IllegalTransitionError):
        answer(start).moved_to(end)


def test_an_approved_answer_is_never_waiting_for_a_rescore() -> None:
    with pytest.raises(InvariantError):
        Answer(
            id=AnswerId(uuid4()),
            college_id=CollegeId(uuid4()),
            booklet_id=BookletId(uuid4()),
            slot_label="1",
            segment_ids=(),
            status=AnswerStatus.APPROVED,
            rescore_pending=True,
        )
