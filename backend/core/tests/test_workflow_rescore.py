"""Re-score scope (D110, D111): a teacher's edit of OCR text, segments or a drawing re-scores
only the answers it touched; a changed key or rubric re-scores the unapproved answers that used
it, with a notice, and never an approved one."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.common import Box
from tarn_core.domain.content import RubricCriterion
from tarn_core.domain.diagram import DiagramGraph, NodeShape, StudentDiagram
from tarn_core.errors import InvariantError, StaleWriteError
from tarn_core.ids import AnswerId, RegionId, StudentDiagramId
from tarn_core.services.diagrams.editor import EditOp, GraphEdit
from tarn_core.services.diagrams.service import StaleGraphError
from tarn_core.services.scoring.booklet import AnswerApprovedError
from tarn_core.services.workflow import LockPolicy, clear_pending
from tarn_core.testing import InMemory
from tarn_core.testing.builders import CollegeFixture, add_college, ci_shaped_blueprint
from tarn_core.testing.workflow import GOOD, HALF, Workflow, scored_booklet, workflow

THREE = {"1": GOOD, "2": HALF, "3": GOOD}


def setup() -> tuple[InMemory, CollegeFixture, Booklet, ExamBlueprint, Workflow]:
    mem = InMemory()
    college = add_college(mem, "A")
    blueprint = ci_shaped_blueprint(mem, college)
    booklet = scored_booklet(mem, college, blueprint, THREE)
    wf = workflow(mem, policy=LockPolicy(timeout=timedelta(minutes=15)))
    wf.review.open(college.id, college.teacher.id, booklet.id)
    return mem, college, mem.booklets.get(college.id, booklet.id), blueprint, wf


def answers(mem: InMemory, booklet: Booklet) -> dict[str, AnswerId]:
    return {a.slot_label: a.id for a in mem.booklets.answers(booklet.college_id, booklet.id)}


def score_counts(mem: InMemory, booklet: Booklet) -> dict[str, int]:
    return {
        a.slot_label: len(mem.scores.scores(booklet.college_id, a.id))
        for a in mem.booklets.answers(booklet.college_id, booklet.id)
    }


def approve(
    mem: InMemory, college: CollegeFixture, booklet: Booklet, wf: Workflow, label: str
) -> None:
    a = mem.booklets.get_answer(college.id, answers(mem, booklet)[label])
    wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, a.id, expected_version=a.version
    )


def first_region(mem: InMemory, booklet: Booklet, label: str, k: int = 0) -> RegionId:
    answer = mem.booklets.get_answer(booklet.college_id, answers(mem, booklet)[label])
    segment = next(
        s
        for s in mem.booklets.segments(booklet.college_id, booklet.id)
        if s.id == answer.segment_ids[0]
    )
    return segment.region_ids[k]


# --- OCR text -------------------------------------------------------------------------------


def test_a_text_correction_rescores_only_its_answer() -> None:
    mem, college, booklet, _, wf = setup()
    region = first_region(mem, booklet, "2")
    result = wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        region,
        expected_version=booklet.version,
        text="alpha and beta.",
    )
    assert result.rescoring == (answers(mem, booklet)["2"],)
    assert result.booklet.version == booklet.version + 1
    assert score_counts(mem, booklet) == {"1": 1, "2": 2, "3": 1}
    two = mem.booklets.get_answer(college.id, answers(mem, booklet)["2"])
    assert not two.rescore_pending
    scores = mem.scores.scores(college.id, two.id)
    assert scores[-1].mark is not None and scores[0].mark is not None
    assert scores[-1].mark > scores[0].mark  # the new suggestion uses the corrected text
    edit = mem.booklets.region_edits(college.id, booklet.id)[0]
    assert (edit.before_text, edit.after_text) == ("alpha only.", "alpha and beta.")
    event = next(e for e in mem.audit.events if e.action is AuditAction.REGION_EDITED)
    assert "alpha" not in str(event.before) + str(event.after)  # no student text in audit
    # The same screen cannot write again.
    with pytest.raises(StaleWriteError):
        wf.text.correct(
            college.id,
            college.teacher.id,
            booklet.id,
            region,
            expected_version=booklet.version,
            struck_out=True,
        )


def test_striking_out_a_line_rescores_without_it() -> None:
    mem, college, booklet, _, wf = setup()
    region = first_region(mem, booklet, "1", 1)
    wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        region,
        expected_version=booklet.version,
        struck_out=True,
    )
    latest = mem.scores.scores(college.id, answers(mem, booklet)["1"])[-1]
    assert any("struck-out" in r for r in latest.reasons)


def test_text_of_an_approved_answer_is_refused() -> None:
    mem, college, booklet, _, wf = setup()
    approve(mem, college, booklet, wf, "1")
    with pytest.raises(AnswerApprovedError):
        wf.text.correct(
            college.id,
            college.teacher.id,
            booklet.id,
            first_region(mem, booklet, "1"),
            expected_version=booklet.version,
            text="changed",
        )


# --- segments -------------------------------------------------------------------------------


def test_a_boundary_move_rescores_the_two_answers_only() -> None:
    mem, college, booklet, _, wf = setup()
    ids = answers(mem, booklet)
    one = mem.booklets.get_answer(college.id, ids["1"])
    two = mem.booklets.get_answer(college.id, ids["2"])
    result = wf.segments.move_boundary(
        college.id,
        college.teacher.id,
        booklet.id,
        expected_version=booklet.version,
        upper=one.segment_ids[0],
        lower=two.segment_ids[0],
        region=first_region(mem, booklet, "1", 1),
    )
    assert set(result.rescoring) == {ids["1"], ids["2"]}
    assert score_counts(mem, booklet) == {"1": 2, "2": 2, "3": 1}
    assert result.booklet.version == booklet.version + 1
    with pytest.raises(StaleWriteError):
        wf.segments.reassign(
            college.id,
            college.teacher.id,
            booklet.id,
            expected_version=booklet.version,
            segment_id=one.segment_ids[0],
            label="4",
        )


def test_during_an_amendment_only_drafts_change() -> None:
    mem, college, booklet, _, wf = setup()
    for label in THREE:
        approve(mem, college, booklet, wf, label)
    approved, _ = wf.review.approve_booklet(
        college.id, college.teacher.id, booklet.id, expected_version=booklet.version
    )
    ids = answers(mem, booklet)
    one = mem.booklets.get_answer(college.id, ids["1"])
    reopened = wf.review.reopen(
        college.id, college.teacher.id, booklet.id, one.id, expected_version=one.version
    )
    two = mem.booklets.get_answer(college.id, ids["2"])
    with pytest.raises(InvariantError, match="answer 2 is approved"):
        wf.segments.move_boundary(
            college.id,
            college.teacher.id,
            booklet.id,
            expected_version=reopened.booklet.version,
            upper=one.segment_ids[0],
            lower=two.segment_ids[0],
            region=first_region(mem, booklet, "1", 1),
        )
    # Nor may an edit give rise to a new answer that is no draft.
    with pytest.raises(InvariantError, match="only its drafts"):
        wf.segments.reassign(
            college.id,
            college.teacher.id,
            booklet.id,
            expected_version=reopened.booklet.version,
            segment_id=one.segment_ids[0],
            label="4",
        )
    # The draft's own text can change.
    done = wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        first_region(mem, booklet, "1"),
        expected_version=reopened.booklet.version,
        text="alpha only.",
    )
    assert done.rescoring == (one.id,)
    assert approved.status is BookletStatus.APPROVED


# --- drawings -------------------------------------------------------------------------------


def test_a_graph_edit_rescores_its_answer_only() -> None:
    mem, college, booklet, _, wf = setup()
    ids = answers(mem, booklet)
    three = mem.booklets.get_answer(college.id, ids["3"])
    diagram = StudentDiagram(
        id=StudentDiagramId(mem.ids.new()),
        college_id=college.id,
        booklet_id=booklet.id,
        segment_id=three.segment_ids[0],
        box=Box(x0=10, y0=10, x1=400, y1=400),
        graph=DiagramGraph(nodes=()),
    )
    mem.booklets.save_diagram(college.id, diagram)
    add = GraphEdit(
        op=EditOp.ADD_NODE,
        shape=NodeShape.PROCESS,
        label="start",
        box=Box(x0=20, y0=20, x1=100, y1=60),
    )
    edited = wf.graphs.edit(
        college.id, college.teacher.id, booklet.id, diagram.id, expected_version=1, edits=[add]
    )
    assert edited.version == 2
    assert score_counts(mem, booklet) == {"1": 1, "2": 1, "3": 2}
    with pytest.raises(StaleWriteError):  # StaleGraphError is a stale write
        wf.graphs.edit(
            college.id, college.teacher.id, booklet.id, diagram.id, expected_version=1, edits=[add]
        )
    assert issubclass(StaleGraphError, StaleWriteError)


# --- changed content ------------------------------------------------------------------------


def test_a_changed_rubric_rescores_unapproved_answers_with_a_notice() -> None:
    mem, college, booklet, blueprint, wf = setup()
    approve(mem, college, booklet, wf, "1")
    wf.review.close(college.id, college.teacher.id, booklet.id)
    # The owning college edits the rubric of questions 1 and 2 (a new version of a criterion).
    for label in ("1", "2"):
        _, question_id, _ = blueprint.leaf(label)
        assert question_id is not None
        criterion = mem.content.for_question(RubricCriterion, question_id)[0]
        mem.content.save(
            replace(
                criterion,
                meta=replace(criterion.meta, version=2),
                label="Names the key items (reworded)",
            )
        )

    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    ids = answers(mem, booklet)
    assert opened.rescoring == (ids["2"],)  # 1 is approved, 3's content did not change
    assert opened.notices == ("re-scored because the content changed: rubric criterion v1 → v2",)
    assert score_counts(mem, booklet) == {"1": 1, "2": 2, "3": 1}
    latest = mem.scores.scores(college.id, ids["2"])[-1]
    assert any("rubric criterion v1 → v2" in r for r in latest.reasons)
    request = next(e for e in mem.audit.events if e.action is AuditAction.ANSWER_RESCORE_REQUESTED)
    assert isinstance(request.after, dict) and request.after["reason"] == "content_changed"
    # Up to date now: opening again re-scores nothing.
    assert wf.review.open(college.id, college.teacher.id, booklet.id).rescoring == ()
    # The approved answer still carries the old suggestion; its approval stands.
    approved = mem.booklets.get_answer(college.id, ids["1"])
    assert approved.status is AnswerStatus.APPROVED
    assert mem.scores.reviews(college.id, approved.id)[-1].teacher_mark == Decimal(2)


def test_a_given_up_rescore_frees_the_answer() -> None:
    mem, college, booklet, _, wf = setup()

    class Never:
        def rescore(self, college_id: object, actor_id: object, answer_ids: object) -> None:
            return None

    held = workflow(mem, rescorer=Never())
    two = answers(mem, booklet)["2"]
    held.rescore.request(college.id, college.teacher.id, [two])
    assert mem.booklets.get_answer(college.id, two).rescore_pending
    clear_pending(mem.booklets, college.id, [two])
    freed = mem.booklets.get_answer(college.id, two)
    assert not freed.rescore_pending
    wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, two, expected_version=freed.version
    )
