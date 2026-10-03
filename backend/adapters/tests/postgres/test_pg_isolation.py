"""The P2 isolation scenarios (core/tests/test_isolation.py) against the real database:
services wired to PostgreSQL sessions bound to one college."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_adapters.postgres.testing import SHARED_HASH, Opener, World
from tarn_core.domain.booklet import AnswerStatus
from tarn_core.domain.review import Review
from tarn_core.errors import NotFoundError, TenantViolationError
from tarn_core.ids import BlueprintId, ReviewId
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration


def test_service_for_a_sees_only_a(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    blueprint_id = BlueprintId(world.blueprint.id)
    with session(a.id) as s:
        svc = make_services(s)
        assert [bk.id for bk in svc.booklets.list(a.id)] == [a.booklet.id]
        assert svc.booklets.get(a.id, a.booklet.id) == a.booklet
        with pytest.raises(NotFoundError):
            svc.booklets.get(a.id, b.booklet.id)
        # Asking for B by name through A's session: row-level security still says no.
        assert svc.booklets.list(b.id) == []
        with pytest.raises(NotFoundError):
            svc.booklets.get(b.id, b.booklet.id)

        # Same hash exists in B, but A's duplicate check sees only A's booklet.
        again = svc.booklets.register(
            a.id,
            a.college.teacher.id,
            student_id=a.college.students[2].id,
            blueprint_id=blueprint_id,
            file_sha256=SHARED_HASH,
        )
        assert again.duplicates == (a.booklet.id,)

        with pytest.raises(NotFoundError):  # B's student
            svc.booklets.register(
                a.id,
                a.college.teacher.id,
                student_id=b.college.students[0].id,
                blueprint_id=blueprint_id,
                file_sha256="d" * 64,
            )
        with pytest.raises(NotFoundError):  # B's teacher
            svc.booklets.register(
                a.id,
                b.college.teacher.id,
                student_id=a.college.students[0].id,
                blueprint_id=blueprint_id,
                file_sha256="d" * 64,
            )
        with pytest.raises(NotFoundError):
            svc.scoring.score_answer(a.id, a.college.teacher.id, b.answers[0].id, answer_text="x")
        with pytest.raises(NotFoundError):
            svc.totals.preview(a.id, b.booklet.id)
        with pytest.raises(NotFoundError):
            svc.booklets.delete(a.id, a.college.teacher.id, b.booklet.id)

        preview = svc.totals.preview(a.id, a.booklet.id)
        # Answer "1" was approved at 1.5 during seeding; "2" and "8" keep their AI marks.
        assert {x.slot_label for x in preview.result.slots if x.counted} == {"1", "2", "8"}
        assert preview.result.total == Decimal("8.5")

    # B is unchanged by everything A did.
    with session(b.id) as s:
        assert [bk.id for bk in s.booklets.list(b.id)] == [b.booklet.id]


def test_totals_prefer_the_teacher_mark_once_approved(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    with session(a.id) as s:
        answer = next(x for x in a.answers if x.slot_label == "8")
        ai = s.scores.scores(a.id, answer.id)[-1]
        s.scores.save_review(
            a.id,
            Review(
                id=ReviewId(s.ids.new()),
                college_id=a.id,
                answer_id=answer.id,
                answer_score_id=ai.id,
                ai_mark=ai.mark,
                teacher_mark=Decimal("3.5"),
                reviewer=a.college.teacher.id,
                reviewed_at=s.clock.now(),
            ),
        )
        s.booklets.save_answer(a.id, replace(answer, status=AnswerStatus.APPROVED))
        assert make_services(s).totals.preview(a.id, a.booklet.id).result.total == Decimal(7)
    with session(b.id) as s:
        assert make_services(s).totals.preview(b.id, b.booklet.id).result.total == Decimal("8.5")
        assert s.scores.reviews(b.id, answer.id) == []


def test_repositories_refuse_cross_college_writes(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    with session(a.id) as s:
        # Caught before SQL: the row's college differs from the call's.
        with pytest.raises(TenantViolationError):
            s.booklets.save(a.id, b.booklet)
        with pytest.raises(TenantViolationError):
            s.students.save(a.id, b.college.students[0])
        # Call and row agree on B, but the session is A's: row-level security refuses.
        with pytest.raises(TenantViolationError):
            s.booklets.save(b.id, replace(b.booklet, version=99))
        with pytest.raises(TenantViolationError):
            s.students.save(b.id, replace(b.college.students[0], name="Overwritten"))
        with pytest.raises(TenantViolationError):
            s.colleges.save(replace(b.college.college, name="Taken over"))
        # The session's transaction is still usable after the refusals.
        assert s.booklets.get(a.id, a.booklet.id) == a.booklet
    with session(b.id) as s:
        assert s.booklets.get(b.id, b.booklet.id) == b.booklet
        assert s.students.get(b.id, b.college.students[0].id) == b.college.students[0]
        assert s.colleges.get(b.id) == b.college.college


def test_a_session_without_college_reads_only_global_content(session: Opener, world: World) -> None:
    with session(None) as s:
        assert s.content.get(type(world.blueprint), world.blueprint.id) == world.blueprint
        with pytest.raises(NotFoundError):
            s.colleges.get(world.a.id)
        assert s.booklets.list(world.a.id) == []
