"""The review (P15) on PostgreSQL: a full review with an amendment, the lock serialising
writes to one booklet, and the new tables' privileges (region edits are insert-only)."""

import threading
import time
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors

from tarn_adapters.postgres.testing import Opener, TestDatabase, World
from tarn_core.domain.booklet import AnswerStatus, BookletStatus
from tarn_core.domain.review import AmendmentOutcome
from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import BookletLockedError, StaleWriteError
from tarn_core.ids import CollegeId, UserId
from tarn_core.services.evaluated import EvaluatedBooklets
from tarn_core.services.workflow import LockPolicy, sheet_key
from tarn_core.testing.builders import add_college, ci_shaped_blueprint
from tarn_core.testing.workflow import GOOD, HALF, scored_booklet, workflow

pytestmark = pytest.mark.integration


def test_review_with_amendment_round_trips(session: Opener) -> None:
    cid = CollegeId(uuid4())
    with session(cid) as s:
        college = add_college(s, "W", cid)
        booklet = scored_booklet(
            s, college, ci_shaped_blueprint(s, college), {"1": GOOD, "2": HALF}
        )
    teacher = college.teacher.id
    policy = LockPolicy(timeout=timedelta(minutes=15))

    with session(cid) as s:
        opened = workflow(s, policy=policy).review.open(cid, teacher, booklet.id)
        assert opened.booklet.status is BookletStatus.IN_REVIEW
    with session(cid) as s:
        wf = workflow(s, policy=policy)
        for a in s.booklets.answers(cid, booklet.id):
            wf.review.approve_answer(cid, teacher, booklet.id, a.id, expected_version=a.version)
        wf.review.approve_booklet(cid, teacher, booklet.id, expected_version=opened.booklet.version)
    with session(cid) as s:
        wf = workflow(s, policy=policy)
        two = next(a for a in s.booklets.answers(cid, booklet.id) if a.slot_label == "2")
        wf.review.reopen(
            cid, teacher, booklet.id, two.id, expected_version=two.version, reason="Recount"
        )
    with session(cid) as s:
        view = workflow(s, policy=policy).review.view(cid, booklet.id)
        assert view.booklet.status is BookletStatus.AMENDMENT_IN_PROGRESS
        assert view.lock is not None and view.lock.holder == teacher
        draft = next(x for x in view.answers if x.answer.id == two.id)
        assert draft.draft is not None and draft.draft.reason == "Recount"
        assert draft.answer.status is AnswerStatus.SUGGESTED
    with session(cid) as s:
        decision = workflow(s, policy=policy).review.approve_answer(
            cid,
            teacher,
            booklet.id,
            two.id,
            expected_version=two.version + 1,
            teacher_mark=Decimal(2),
        )
        assert decision.sheet is not None
    with session(cid) as s:
        sheets = s.sheets.versions(cid, booklet.id)
        assert [x.version for x in sheets] == [1, 2]
        assert sheets[1].note == "2: Recount" and sheets[0].note == ""
        # Each version keeps its own PDF key (the CHECK holds it under the college's prefix).
        assert [x.pdf for x in sheets] == [sheet_key(cid, booklet.id, v) for v in (1, 2)]
        found = EvaluatedBooklets(
            booklets=s.booklets,
            students=s.students,
            content=s.content,
            sheets=s.sheets,
            blobs=s.blobs,
        ).search(cid, usn=college.students[0].usn, status=BookletStatus.APPROVED_AMENDED)
        assert [e.booklet.id for e in found.items] == [booklet.id]
        assert [x.version for x in found.items[0].sheets] == [1, 2]
        amendment = s.booklets.amendments(cid, booklet.id)[0]
        assert amendment.outcome is AmendmentOutcome.APPROVED and amendment.sheet_version == 2
        assert s.booklets.get(cid, booklet.id).status is BookletStatus.APPROVED_AMENDED
        # A stale write is refused through the database too.
        with pytest.raises(StaleWriteError):
            workflow(s, policy=policy).review.reopen(
                cid, teacher, booklet.id, two.id, expected_version=two.version
            )


def test_the_lock_serialises_writes_to_one_booklet(session: Opener, world: World) -> None:
    """While one transaction holds the booklet's row (``lock()``), another one's ``lock()``
    waits; it then sees the first one's lock and is refused."""
    a = world.a
    other = User(
        id=UserId(uuid4()),
        college_id=a.id,
        display_name="Teacher A2",
        email="teacher.a2@example.test",
        role=Role.TEACHER,
    )
    with session(a.id) as s:
        s.booklets.delete_lock(a.id, a.booklet.id)
        s.users.save(a.id, other)
    order: list[str] = []
    started = threading.Event()

    def second() -> None:
        started.wait()
        with session(a.id) as s2:
            try:
                workflow(s2).guard.acquire(a.id, other.id, a.booklet.id)
                order.append("second acquired")
            except BookletLockedError:
                order.append("second refused")

    worker = threading.Thread(target=second)
    worker.start()
    with session(a.id) as s1:
        workflow(s1).guard.acquire(a.id, a.college.teacher.id, a.booklet.id)
        started.set()
        time.sleep(0.5)  # the second transaction is blocked on the row meanwhile
        order.append("first commits")
    worker.join(timeout=10)
    assert order == ["first commits", "second refused"]


def test_region_edits_are_insert_only(test_database: TestDatabase, world: World) -> None:
    with test_database.app(world.a.id) as conn:
        for stmt in ("UPDATE region_edits SET after_text = 'x'",):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                conn.execute(stmt)
        # Locks and amendments are updated by the review.
        conn.execute("UPDATE booklet_locks SET expires_at = expires_at")
        conn.execute("UPDATE amendments SET reason = reason")


def test_an_approved_answer_cannot_wait_for_a_rescore(
    test_database: TestDatabase, world: World
) -> None:
    with test_database.app(world.a.id) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE answers SET rescore_pending = true WHERE id = %s", (world.a.answers[0].id,)
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE booklets SET status = 'reviewed' WHERE id = %s", (world.a.booklet.id,)
            )
