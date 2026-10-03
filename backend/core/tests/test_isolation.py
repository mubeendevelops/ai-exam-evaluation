"""A service called for college A never sees college B's data (R7, design "Isolation in the core").

Both colleges are seeded identically (same paper, same file hashes, answers scored), so any
leak would show up as an extra row, a false duplicate warning or a foreign audit event."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.booklet import AnswerStatus, Booklet
from tarn_core.domain.review import Review
from tarn_core.errors import NotFoundError, TenantViolationError
from tarn_core.ids import BlueprintId, ReviewId
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    Services,
    add_answer,
    add_college,
    ci_shaped_blueprint,
    make_services,
)

SHARED_HASH = "f" * 64


def seed(
    mem: InMemory, svc: Services, college: CollegeFixture, blueprint_id: BlueprintId
) -> Booklet:
    booklet = svc.booklets.register(
        college.id,
        college.teacher.id,
        student_id=college.students[0].id,
        blueprint_id=blueprint_id,
        file_sha256=SHARED_HASH,
    ).booklet
    for label in ("1", "2", "8"):
        answer = add_answer(mem, booklet, label)
        svc.scoring.score_answer(college.id, college.teacher.id, answer.id, answer_text="x")
    return booklet


@pytest.fixture
def two_colleges() -> tuple[InMemory, Services, CollegeFixture, CollegeFixture, Booklet, Booklet]:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    blueprint = ci_shaped_blueprint(mem, a)  # global: B uses A's paper too
    svc = make_services(mem)
    booklet_b = seed(mem, svc, b, blueprint.id)
    booklet_a = seed(mem, svc, a, blueprint.id)
    return mem, svc, a, b, booklet_a, booklet_b


def test_service_for_a_sees_only_a(
    two_colleges: tuple[InMemory, Services, CollegeFixture, CollegeFixture, Booklet, Booklet],
) -> None:
    mem, svc, a, b, booklet_a, booklet_b = two_colleges
    mem.log.calls.clear()
    events_before = len(mem.audit.events)

    assert [bk.id for bk in svc.booklets.list(a.id)] == [booklet_a.id]
    assert svc.booklets.get(a.id, booklet_a.id) == booklet_a
    with pytest.raises(NotFoundError):
        svc.booklets.get(a.id, booklet_b.id)

    # Same hash exists in B, but A's duplicate check sees only A's booklet.
    again = svc.booklets.register(
        a.id,
        a.teacher.id,
        student_id=a.students[1].id,
        blueprint_id=BlueprintId(booklet_a.blueprint.id),
        file_sha256=SHARED_HASH,
    )
    assert again.duplicates == (booklet_a.id,)

    # A cannot pick B's student, act as B's teacher, or score B's answers.
    with pytest.raises(NotFoundError):
        svc.booklets.register(
            a.id,
            a.teacher.id,
            student_id=b.students[0].id,
            blueprint_id=BlueprintId(booklet_a.blueprint.id),
            file_sha256="e" * 64,
        )
    with pytest.raises(NotFoundError):
        svc.booklets.register(
            a.id,
            b.teacher.id,
            student_id=a.students[0].id,
            blueprint_id=BlueprintId(booklet_a.blueprint.id),
            file_sha256="e" * 64,
        )
    answer_b = mem.booklets.answers(b.id, booklet_b.id)[0]
    mem.log.calls.clear()  # the line above was the test peeking at B
    with pytest.raises(NotFoundError):
        svc.scoring.score_answer(a.id, a.teacher.id, answer_b.id, answer_text="x")
    with pytest.raises(NotFoundError):
        svc.totals.preview(a.id, booklet_b.id)

    preview = svc.totals.preview(a.id, booklet_a.id)
    assert {s.slot_label for s in preview.result.slots if s.counted} == {"1", "2", "8"}
    assert preview.result.total == Decimal(9)

    with pytest.raises(NotFoundError):
        svc.booklets.delete(a.id, a.teacher.id, booklet_b.id)

    # Every repository call made for A's work was scoped to A, and so was every audit event.
    assert mem.log.colleges == {a.id}
    assert {e.college_id for e in mem.audit.events[events_before:]} == {a.id}


def test_totals_prefer_the_teacher_mark_once_approved(
    two_colleges: tuple[InMemory, Services, CollegeFixture, CollegeFixture, Booklet, Booklet],
) -> None:
    mem, svc, a, b, booklet_a, booklet_b = two_colleges
    answer = next(x for x in mem.booklets.answers(a.id, booklet_a.id) if x.slot_label == "8")
    ai = mem.scores.scores(a.id, answer.id)[-1]
    mem.scores.save_review(
        a.id,
        Review(
            id=ReviewId(mem.ids.new()),
            college_id=a.id,
            answer_id=answer.id,
            answer_score_id=ai.id,
            ai_mark=ai.mark,
            teacher_mark=Decimal("3.5"),
            reviewer=a.teacher.id,
            reviewed_at=mem.clock.now(),
        ),
    )
    mem.booklets.save_answer(a.id, replace(answer, status=AnswerStatus.APPROVED))
    assert svc.totals.preview(a.id, booklet_a.id).result.total == Decimal("7.5")
    assert svc.totals.preview(b.id, booklet_b.id).result.total == Decimal(9)
    assert mem.scores.reviews(b.id, answer.id) == []


def test_delete_in_a_leaves_b_untouched(
    two_colleges: tuple[InMemory, Services, CollegeFixture, CollegeFixture, Booklet, Booklet],
) -> None:
    mem, svc, a, b, booklet_a, booklet_b = two_colleges
    pages_b = mem.booklets.pages(b.id, booklet_b.id)
    answers_a = mem.booklets.answers(a.id, booklet_a.id)

    svc.booklets.delete(a.id, a.teacher.id, booklet_a.id)

    assert svc.booklets.list(a.id) == []
    assert mem.booklets.pages(a.id, booklet_a.id) == []
    assert all(mem.scores.scores(a.id, x.id) == [] for x in answers_a)
    assert all(k.college_id == b.id for k in mem.blobs.keys)
    assert [p.id for p in mem.booklets.pages(b.id, booklet_b.id)] == [p.id for p in pages_b]
    assert len(mem.booklets.answers(b.id, booklet_b.id)) == 3
    record = mem.audit.events[-1]
    assert (record.college_id, record.booklet_id, record.before, record.after) == (
        a.id,
        booklet_a.id,
        None,
        None,
    )


def test_repositories_refuse_cross_college_writes(
    two_colleges: tuple[InMemory, Services, CollegeFixture, CollegeFixture, Booklet, Booklet],
) -> None:
    mem, _, a, b, _, booklet_b = two_colleges
    with pytest.raises(TenantViolationError):
        mem.booklets.save(a.id, booklet_b)
    with pytest.raises(TenantViolationError):
        mem.students.save(a.id, b.students[0])
