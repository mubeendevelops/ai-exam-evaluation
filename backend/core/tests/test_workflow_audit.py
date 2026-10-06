"""Audit completeness (rule 6, D113): a whole review, from opening to an amended sheet, leaves
one event per transition and edit, each naming the teacher, the booklet and (for answer
events) the answer, with before and after values; no student text or remarks reach the log."""

from datetime import timedelta
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import Booklet
from tarn_core.ids import AnswerId, RegionId
from tarn_core.services.workflow import LockPolicy
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, ci_shaped_blueprint
from tarn_core.testing.workflow import GOOD, HALF, scored_booklet, workflow

A = AuditAction


def _answer(mem: InMemory, booklet: Booklet, label: str) -> tuple[AnswerId, int]:
    a = next(
        a for a in mem.booklets.answers(booklet.college_id, booklet.id) if a.slot_label == label
    )
    return a.id, a.version


def test_a_full_review_is_audited_step_by_step() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = scored_booklet(
        mem, college, ci_shaped_blueprint(mem, college), {"1": GOOD, "2": HALF}
    )
    wf = workflow(mem, policy=LockPolicy(timeout=timedelta(minutes=15)))
    t = college.teacher.id
    start = len(mem.audit.events)

    opened = wf.review.open(college.id, t, booklet.id)
    one, v1 = _answer(mem, booklet, "1")
    two, _ = _answer(mem, booklet, "2")
    segment = mem.booklets.get_answer(college.id, two).segment_ids[0]
    region: RegionId = next(
        s for s in mem.booklets.segments(college.id, booklet.id) if s.id == segment
    ).region_ids[0]
    edited = wf.text.correct(
        college.id, t, booklet.id, region, expected_version=opened.booklet.version, text="beta b."
    )
    wf.review.skip(college.id, t, booklet.id, one, expected_version=v1)
    wf.review.approve_answer(
        college.id, t, booklet.id, one, expected_version=v1 + 1, tags=["neat"], remarks="Clear."
    )
    two_now = mem.booklets.get_answer(college.id, two)
    wf.review.approve_answer(
        college.id,
        t,
        booklet.id,
        two,
        expected_version=two_now.version,
        teacher_mark=Decimal("0.5"),
        remarks="Student wrote beta.",
    )
    wf.review.approve_booklet(college.id, t, booklet.id, expected_version=edited.booklet.version)
    two_now = mem.booklets.get_answer(college.id, two)
    wf.review.reopen(
        college.id, t, booklet.id, two, expected_version=two_now.version, reason="Recount"
    )
    wf.review.approve_answer(
        college.id,
        t,
        booklet.id,
        two,
        expected_version=two_now.version + 1,
        teacher_mark=Decimal(2),
    )
    wf.review.close(college.id, t, booklet.id)

    events = mem.audit.events[start:]
    assert [e.action for e in events] == [
        A.BOOKLET_OPENED,
        A.REGION_EDITED,
        A.ANSWER_RESCORE_REQUESTED,
        A.ANSWER_SCORED,
        A.ANSWER_SKIPPED,
        A.ANSWER_APPROVED,
        A.ANSWER_APPROVED,
        A.RESULT_SHEET_ISSUED,
        A.BOOKLET_APPROVED,
        A.AMENDMENT_OPENED,
        A.ANSWER_APPROVED,
        A.RESULT_SHEET_ISSUED,
        A.BOOKLET_APPROVED,
        A.BOOKLET_CLOSED,
    ]
    answer_events = {
        A.ANSWER_RESCORE_REQUESTED,
        A.ANSWER_SCORED,
        A.ANSWER_SKIPPED,
        A.ANSWER_APPROVED,
        A.AMENDMENT_OPENED,
    }
    for event in events:
        assert event.actor_id == t
        assert event.booklet_id == booklet.id and event.college_id == college.id
        if event.action in answer_events:
            assert event.answer_id in (one, two)
        if event.action is not A.ANSWER_SCORED:  # a new suggestion has no "before"
            assert event.before is not None, event.action
        assert event.after is not None, event.action
        logged = f"{event.before} {event.after}"
        for secret in ("beta", "alpha", "Clear.", "Recount", "Student wrote"):
            assert secret not in logged, (event.action, secret)

    approvals = [e for e in events if e.action is A.ANSWER_APPROVED]
    assert approvals[1].after["ai_mark"] != "0.5"  # type: ignore[index, call-overload]
    assert approvals[1].after["teacher_mark"] == "0.5"  # type: ignore[index, call-overload]
    assert approvals[1].after["overridden"] is True  # type: ignore[index, call-overload]
    assert approvals[0].after["tags"] == ["neat"]  # type: ignore[index, call-overload]
    assert approvals[2].after["amendment_id"]  # type: ignore[index, call-overload]
    booklet_moves = [
        (e.before["status"], e.after["status"])  # type: ignore[index, call-overload]
        for e in events
        if e.action in (A.BOOKLET_OPENED, A.BOOKLET_APPROVED)
    ]
    assert booklet_moves == [
        ("scored", "in_review"),
        ("in_review", "approved"),
        ("amendment_in_progress", "approved_amended"),
    ]
    amendment = next(e for e in events if e.action is A.AMENDMENT_OPENED)
    assert amendment.before["booklet_status"] == "approved"  # type: ignore[index, call-overload]
    assert amendment.after["booklet_status"] == "amendment_in_progress"  # type: ignore[index, call-overload]
    assert amendment.after["reason_given"] is True  # type: ignore[index, call-overload]
    sheets = [e.after["version"] for e in events if e.action is A.RESULT_SHEET_ISSUED]  # type: ignore[index, call-overload]
    assert sheets == [1, 2]
