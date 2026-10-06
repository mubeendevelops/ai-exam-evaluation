"""Diagram recognition stage, reference recognition, teacher edits and the diagram scorer (P14),
with a scripted recognizer (no model)."""

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import AnswerStatus, BookletStatus
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.content import DiagramComponent, Glossary, ReferenceDiagram
from tarn_core.domain.diagram import (
    DetectedArrow,
    DetectedShape,
    DiagramDetection,
    DiagramKind,
    DiagramText,
    NodeShape,
    Point,
    RecognitionState,
)
from tarn_core.domain.scoring import CHECK
from tarn_core.errors import InvariantError, NotOwnerError
from tarn_core.ids import AnswerId
from tarn_core.ports.jobs import JOB_DIAGRAMS_BOOKLET, JOB_RECOGNIZE_REFERENCE, JOB_SCORE_BOOKLET
from tarn_core.services.diagrams.build import GraphBuilder
from tarn_core.services.diagrams.editor import EditOp, GraphEdit, apply_edits
from tarn_core.services.diagrams.scorer import DiagramScorer
from tarn_core.services.diagrams.service import (
    BookletDiagrams,
    ReferenceDiagrams,
    StaleGraphError,
    StudentDiagrams,
    queue_diagrams,
    queue_reference_recognition,
)
from tarn_core.services.ocr.reader import PageText, ReadLine
from tarn_core.services.ocr.selector import Lexicon, LineChoice
from tarn_core.services.scoring import MANUAL, BookletScorer, ScoringService
from tarn_core.services.scoring.booklet import AnswerApprovedError
from tarn_core.testing import InMemory, ScriptedDiagramRecognizer
from tarn_core.testing.builders import add_college, make_services
from tarn_core.testing.diagrams import (
    SHAPES,
    DiagramWorld,
    diagram_world,
    flowchart_detection,
)

REC = EngineRef(name="scripted-diagram", version="1")

type World = DiagramWorld[InMemory]


class FakeLabels:
    """Reads the reference PNG: one line per shape label, inside its box."""

    def __init__(self, labels: tuple[str, ...] = ("start", "read n", "stop")) -> None:
        self._labels = labels
        self.lexicons: list[Lexicon] = []

    @property
    def engine_names(self) -> tuple[str, ...]:
        return ("scripted",)

    def read(self, image: bytes, lexicon: Lexicon, *, cuts: Sequence[Box] = ()) -> PageText:
        from tarn_core.domain.booklet import LineReading, RegionKind

        self.lexicons.append(lexicon)
        lines = []
        for (_, box, _), text in zip(SHAPES, self._labels, strict=False):
            inner = Box(x0=box.x0 + 20, y0=box.y0 + 10, x1=box.x1 - 20, y1=box.y1 - 10)
            reading = LineReading(engine=REC, text=text, box=inner, confidence=0.8)
            lines.append(
                ReadLine(
                    kind=RegionKind.TEXT_LINE,
                    box=inner,
                    readings=(reading,),
                    choice=LineChoice(
                        scores=(), chosen=0, line_score=0.8, flagged=False, calibrations=()
                    ),
                )
            )
        return PageText(regions=tuple(lines), engines=("scripted",), failures=())


@pytest.fixture
def world() -> World:
    return diagram_world(InMemory())


def references(world: World, **kw: object) -> ReferenceDiagrams:
    mem = world.mem
    return ReferenceDiagrams(
        content=mem.content,
        blobs=mem.blobs,
        runtime=mem.runtime,
        recognizers=kw.get(  # type: ignore[arg-type]
            "recognizers", {DiagramKind.FLOWCHART: ScriptedDiagramRecognizer(flowchart_detection())}
        ),
        labels=kw.get("labels", FakeLabels()),  # type: ignore[arg-type]
        glossary=make_services(mem).bank,
    )


def recognised(world: World) -> ReferenceDiagram:
    return references(world).recognize(
        world.college.id, world.college.teacher.id, world.reference.id
    )


def stage(world: World, detection: DiagramDetection | None = None, **kw: object) -> BookletDiagrams:
    mem = world.mem
    recognizer = ScriptedDiagramRecognizer(detection or flowchart_detection(), **kw)  # type: ignore[arg-type]
    return BookletDiagrams(
        booklets=mem.booklets,
        content=mem.content,
        blobs=mem.blobs,
        runtime=mem.runtime,
        jobs=mem.jobs,
        recognizers={DiagramKind.FLOWCHART: recognizer},
    )


def segmented(world: World) -> None:
    b = world.mem.booklets.get(world.booklet.college_id, world.booklet.id)
    world.mem.booklets.save(
        b.college_id, replace(b, status=BookletStatus.SEGMENTED, version=b.version + 1)
    )


def scoring(world: World) -> ScoringService:
    mem = world.mem
    return ScoringService.standard(
        booklets=mem.booklets,
        scores=mem.scores,
        content=mem.content,
        runtime=mem.runtime,
        embedder=None,
        extra=[DiagramScorer()],
    )


def jobs_of(world: World) -> list[str]:
    return [j.kind for j in world.mem.jobs.jobs if j.college_id == world.booklet.college_id]


# --- reference recognition -------------------------------------------------------------------


def test_upload_leaves_the_reference_pending_and_the_job_is_idempotent(world: World) -> None:
    assert world.reference.recognition is RecognitionState.PENDING
    assert world.reference.graph.empty
    for _ in range(2):
        queue_reference_recognition(
            world.mem.jobs, world.college.id, world.college.teacher.id, world.reference
        )
    assert [k for k in jobs_of(world) if k == JOB_RECOGNIZE_REFERENCE] == [JOB_RECOGNIZE_REFERENCE]


def test_recognising_the_reference_builds_its_graph_and_glossary(world: World) -> None:
    labels = FakeLabels()
    ref = references(world, labels=labels).recognize(
        world.college.id, world.college.teacher.id, world.reference.id
    )
    assert ref.meta.version == world.reference.meta.version + 1
    assert ref.recognition is RecognitionState.RECOGNISED
    assert [n.label for n in ref.graph.nodes] == ["start", "read n", "stop"]
    assert [(e.source, e.target) for e in ref.graph.edges] == [("n1", "n2"), ("n2", "n3")]
    assert ref.graph.label_engines == ("scripted",)
    glossary = world.mem.content.for_question(Glossary, world.question.id)[0]
    assert set(glossary.reference_labels) == {"start", "read n", "stop"}
    assert labels.lexicons, "labels were read through the OCR framework"


def test_a_failing_recognizer_marks_the_reference_failed(world: World) -> None:
    rec = {DiagramKind.FLOWCHART: ScriptedDiagramRecognizer(flowchart_detection(), fail=True)}
    ref = references(world, recognizers=rec).recognize(
        world.college.id, world.college.teacher.id, world.reference.id
    )
    assert ref.recognition is RecognitionState.FAILED and ref.graph.empty


def test_a_kind_without_recognizer_fails_cleanly() -> None:
    world = diagram_world(InMemory(), kind=DiagramKind.CIRCUIT)
    ref = references(world).recognize(
        world.college.id, world.college.teacher.id, world.reference.id
    )
    assert ref.recognition is RecognitionState.FAILED


def test_teacher_edits_the_reference_as_a_new_version(world: World) -> None:
    ref = recognised(world)
    edited = references(world).edit(
        world.college.id,
        world.college.teacher.id,
        ref.id,
        expected_version=ref.meta.version,
        edits=[
            GraphEdit(op=EditOp.RELABEL_NODE, id="n2", label="input n"),
            GraphEdit(op=EditOp.REVERSE_EDGE, id="e2"),
        ],
        kind=DiagramKind.BLOCK,
    )
    assert edited.meta.version == ref.meta.version + 1
    assert edited.recognition is RecognitionState.EDITED and edited.kind is DiagramKind.BLOCK
    assert edited.graph.edited_by_teacher
    assert [(e.source, e.target) for e in edited.graph.edges] == [("n1", "n2"), ("n3", "n2")]
    glossary = world.mem.content.for_question(Glossary, world.question.id)[0]
    assert "input n" in glossary.reference_labels
    with pytest.raises(StaleGraphError):
        references(world).edit(
            world.college.id,
            world.college.teacher.id,
            ref.id,
            expected_version=ref.meta.version,
            edits=[GraphEdit(op=EditOp.REMOVE_NODE, id="n1")],
        )
    event = [e for e in world.mem.audit.events if e.college_id == world.college.id][-2]
    assert "input n" not in str(event.after)


def test_only_the_owning_college_edits_the_reference(world: World) -> None:
    ref = recognised(world)
    other = add_college(world.mem, "OTHER")
    with pytest.raises(NotOwnerError):
        references(world).edit(
            other.id,
            other.teacher.id,
            ref.id,
            expected_version=ref.meta.version,
            edits=[GraphEdit(op=EditOp.REMOVE_NODE, id="n1")],
        )


# --- the booklet stage -----------------------------------------------------------------------


def test_stage_recognises_each_diagram_region_then_queues_scoring(world: World) -> None:
    recognised(world)
    answer = world.answer()
    segmented(world)
    s = stage(world)
    assert s.step(world.booklet.college_id, world.booklet.id) is False
    diagrams = world.mem.booklets.diagrams(world.booklet.college_id, world.booklet.id)
    assert len(diagrams) == 1
    d = diagrams[0]
    assert d.segment_id == world.mem.booklets.get_answer(d.college_id, answer.id).segment_ids[0]
    assert [n.label for n in d.graph.nodes] == ["start", "read n", "stop"]
    assert d.graph.label_engines == ("scripted",)
    assert JOB_SCORE_BOOKLET not in jobs_of(world)
    assert s.step(world.booklet.college_id, world.booklet.id) is True
    assert JOB_SCORE_BOOKLET in jobs_of(world)
    event = [e for e in world.mem.audit.events if e.college_id == world.college.id]
    recognised_events = [e for e in event if e.action is AuditAction.DIAGRAM_RECOGNISED]
    assert recognised_events and "start" not in str(recognised_events[0].after)


def test_stage_skips_questions_without_diagram_criteria() -> None:
    world = diagram_world(InMemory(), components=())
    world.answer()
    segmented(world)
    assert stage(world).step(world.booklet.college_id, world.booklet.id) is True
    assert world.mem.booklets.diagrams(world.booklet.college_id, world.booklet.id) == []


def test_stage_stores_an_empty_graph_when_recognition_fails(world: World) -> None:
    world.answer()
    segmented(world)
    s = stage(world, fail=True)
    s.step(world.booklet.college_id, world.booklet.id)
    (d,) = world.mem.booklets.diagrams(world.booklet.college_id, world.booklet.id)
    assert d.graph.empty and d.graph.recognizer is None


def test_stage_abandon_fails_the_booklet(world: World) -> None:
    segmented(world)
    stage(world).abandon(world.booklet.college_id, world.booklet.id)
    b = world.mem.booklets.get(world.booklet.college_id, world.booklet.id)
    assert b.status is BookletStatus.FAILED and b.failure_reason == "diagrams_failed"


def test_queue_diagrams_is_idempotent(world: World) -> None:
    for _ in range(2):
        queue_diagrams(world.mem.jobs, world.booklet.college_id, world.booklet.id)
    assert jobs_of(world).count(JOB_DIAGRAMS_BOOKLET) == 1


# --- scoring ---------------------------------------------------------------------------------


def run(world: World, detection: DiagramDetection | None = None) -> AnswerId:
    answer = world.answer()
    segmented(world)
    s = stage(world, detection)
    while not s.step(world.booklet.college_id, world.booklet.id):
        pass
    return answer.id


def test_a_correct_drawing_gets_full_diagram_credit(world: World) -> None:
    ref = recognised(world)
    references(world).edit(
        world.college.id,
        world.college.teacher.id,
        ref.id,
        expected_version=ref.meta.version,
        edits=[GraphEdit(op=EditOp.RELABEL_NODE, id="n1", label="start")],
    )  # checked by the teacher: no "check" flag for an unchecked reference
    answer_id = run(world)
    score = scoring(world).score_answer(world.booklet.college_id, None, answer_id)
    diagram = next(c for c in score.criterion_scores if c.scorer == DiagramScorer.ref)
    assert diagram.credit == Decimal(1) and diagram.flags == ()
    assert diagram.similarity == 1.0
    assert isinstance(diagram.detail, dict)
    assert diagram.detail["similarity"] == 1.0
    assert diagram.detail["question_id"] == world.question.code
    used = {(r.kind.value, r.id) for r in score.content_versions}
    assert ("reference_diagram", world.reference.id) in used


def test_a_reversed_arrow_and_an_unchecked_reference(world: World) -> None:
    recognised(world)
    answer_id = run(world, flowchart_detection(reverse_second=True))
    score = scoring(world).score_answer(world.booklet.college_id, None, answer_id)
    diagram = next(c for c in score.criterion_scores if c.scorer == DiagramScorer.ref)
    assert Decimal(0) < diagram.credit < Decimal(1)
    assert CHECK in diagram.flags
    assert diagram.reason is not None and "reversed" in diagram.reason.summary
    assert "not checked by a teacher" in diagram.reason.summary
    assert isinstance(diagram.detail, dict)
    edges = diagram.detail["edges"]
    assert isinstance(edges, list)
    assert [e["status"] for e in edges if isinstance(e, dict)] == ["present", "reversed"]


def test_split_criteria_take_their_sub_scores() -> None:
    world = diagram_world(
        InMemory(),
        components=(DiagramComponent.NODES, DiagramComponent.EDGES, DiagramComponent.LABELS),
    )
    recognised(world)
    answer_id = run(world, flowchart_detection(drop_last=True))
    score = scoring(world).score_answer(world.booklet.college_id, None, answer_id)
    credits = [c.credit for c in score.criterion_scores if c.scorer == DiagramScorer.ref]
    # nodes: 2 of 3 found; edges: 1 present, the second now dangles; labels: 2 of 3
    assert credits == [Decimal("0.8"), Decimal("0.5"), Decimal("0.6667")]


def test_no_reference_graph_or_no_drawing(world: World) -> None:
    answer_id = run(world)
    score = scoring(world).score_answer(world.booklet.college_id, None, answer_id)
    diagram = next(c for c in score.criterion_scores if c.scorer == DiagramScorer.ref)
    assert diagram.flags == (MANUAL,) and diagram.credit == 0
    recognised(world)
    plain = world.answer()
    score = scoring(world).score_answer(world.booklet.college_id, None, plain.id, diagrams=())
    diagram = next(c for c in score.criterion_scores if c.scorer == DiagramScorer.ref)
    assert diagram.flags == (CHECK,) and diagram.credit == 0
    assert diagram.reason is not None and "no diagram" in diagram.reason.summary


def test_an_unrecognised_drawing_goes_to_the_teacher(world: World) -> None:
    recognised(world)
    answer = world.answer()
    segmented(world)
    s = stage(world, fail=True)
    while not s.step(world.booklet.college_id, world.booklet.id):
        pass
    score = scoring(world).score_answer(world.booklet.college_id, None, answer.id)
    diagram = next(c for c in score.criterion_scores if c.scorer == DiagramScorer.ref)
    assert diagram.flags == (MANUAL,)


# --- student edits ---------------------------------------------------------------------------


def students(world: World) -> StudentDiagrams:
    mem = world.mem
    scorer = BookletScorer(booklets=mem.booklets, scoring=scoring(world), runtime=mem.runtime)
    return StudentDiagrams(booklets=mem.booklets, runtime=mem.runtime, rescore=scorer)


def test_teacher_corrects_a_student_drawing_and_the_answer_is_rescored(world: World) -> None:
    recognised(world)
    answer_id = run(world, flowchart_detection(reverse_second=True))
    cid = world.booklet.college_id
    (d,) = world.mem.booklets.diagrams(cid, world.booklet.id)
    new = students(world).edit(
        cid,
        world.college.teacher.id,
        world.booklet.id,
        d.id,
        expected_version=1,
        edits=[GraphEdit(op=EditOp.REVERSE_EDGE, id="e2")],
    )
    assert new.version == 2 and new.graph.edited_by_teacher
    latest = world.mem.scores.scores(cid, answer_id)[-1]
    diagram = next(c for c in latest.criterion_scores if c.scorer == DiagramScorer.ref)
    assert diagram.similarity == 1.0
    assert isinstance(diagram.detail, dict) and diagram.detail["edited_by_teacher"] is True
    assert diagram.detail["student_graph_version"] == 2
    with pytest.raises(StaleGraphError):
        students(world).edit(
            cid, world.college.teacher.id, world.booklet.id, d.id, expected_version=1,
            edits=[GraphEdit(op=EditOp.REMOVE_EDGE, id="e1")],
        )  # fmt: skip


def test_an_approved_answer_is_not_edited(world: World) -> None:
    answer_id = run(world)
    cid = world.booklet.college_id
    answer = world.mem.booklets.get_answer(cid, answer_id)
    world.mem.booklets.save_answer(cid, replace(answer, status=AnswerStatus.APPROVED))
    (d,) = world.mem.booklets.diagrams(cid, world.booklet.id)
    with pytest.raises(AnswerApprovedError):
        students(world).edit(
            cid, world.college.teacher.id, world.booklet.id, d.id, expected_version=1,
            edits=[GraphEdit(op=EditOp.REMOVE_EDGE, id="e1")],
        )  # fmt: skip
    # refused before anything is stored (the API re-scores later, in the worker)
    assert world.mem.booklets.diagrams(cid, world.booklet.id) == [d]


def test_another_college_cannot_see_the_drawing(world: World) -> None:
    run(world)
    other = add_college(world.mem, "OTHER")
    (d,) = world.mem.booklets.diagrams(world.booklet.college_id, world.booklet.id)
    with pytest.raises(Exception):  # noqa: B017  (tenant violation or not found)
        students(world).edit(
            other.id, other.teacher.id, world.booklet.id, d.id, expected_version=1,
            edits=[GraphEdit(op=EditOp.REMOVE_EDGE, id="e1")],
        )  # fmt: skip


# --- builder and editor ----------------------------------------------------------------------


def test_builder_attaches_ends_labels_and_leaves_dangling_arrows() -> None:
    shapes = (
        DetectedShape(
            shape=NodeShape.DECISION, box=Box(x0=100, y0=100, x1=300, y1=200), confidence=0.9
        ),
        DetectedShape(
            shape=NodeShape.PROCESS, box=Box(x0=500, y0=100, x1=700, y1=200), confidence=0.9
        ),
        DetectedShape(
            shape=NodeShape.PROCESS, box=Box(x0=100, y0=400, x1=300, y1=500), confidence=0.1
        ),
    )
    arrows = (
        DetectedArrow(
            box=Box(x0=300, y0=140, x1=500, y1=160),
            tail=Point(x=302, y=150), head=Point(x=498, y=150), has_head=True, confidence=0.8,
        ),
        DetectedArrow(
            box=Box(x0=200, y0=200, x1=220, y1=380),
            tail=Point(x=210, y=203), head=Point(x=210, y=380), has_head=True, confidence=0.8,
        ),
    )  # fmt: skip
    texts = [
        DiagramText(text="x > 0?", box=Box(x0=150, y0=130, x1=250, y1=170), confidence=0.7),
        DiagramText(text="yes", box=Box(x0=380, y0=110, x1=420, y1=135), confidence=0.9),
        DiagramText(text="caption", box=Box(x0=800, y0=900, x1=900, y1=930), confidence=0.9),
        DiagramText(text="  ", box=Box(x0=0, y0=0, x1=5, y1=5)),
    ]
    graph = GraphBuilder().build(
        DiagramDetection(width=1000, height=1000, shapes=shapes, arrows=arrows),
        texts,
        recognizer=REC,
    )
    assert [(n.id, n.shape, n.label) for n in graph.nodes] == [
        ("n1", NodeShape.DECISION, "x > 0?"),
        ("n2", NodeShape.PROCESS, ""),
    ]  # the low-confidence shape is left out
    e1, e2 = graph.edges
    assert (e1.source, e1.target, e1.label) == ("n1", "n2", "yes")
    assert (e2.source, e2.target) == ("n1", None)
    assert [f.text for f in graph.free_labels] == ["caption"]
    assert graph.nodes[0].label_confidence == 0.7


def test_both_ends_on_one_shape_move_the_farther_end() -> None:
    shapes = (
        DetectedShape(shape=NodeShape.PROCESS, box=Box(x0=0, y0=0, x1=100, y1=100), confidence=0.9),
        DetectedShape(
            shape=NodeShape.PROCESS, box=Box(x0=0, y0=130, x1=100, y1=230), confidence=0.9
        ),
    )
    arrow = DetectedArrow(
        box=Box(x0=40, y0=95, x1=60, y1=125), tail=Point(x=50, y=98), head=Point(x=50, y=122),
        has_head=True, confidence=0.9,
    )  # fmt: skip
    graph = GraphBuilder().build(
        DiagramDetection(width=300, height=300, shapes=shapes, arrows=(arrow,)), [], recognizer=REC
    )
    assert (graph.edges[0].source, graph.edges[0].target) == ("n1", "n2")


def test_editor_operations() -> None:
    graph = GraphBuilder().build(flowchart_detection(), [], recognizer=REC)
    edited = apply_edits(
        graph,
        [
            GraphEdit(op=EditOp.ADD_NODE, shape=NodeShape.DECISION, label="n > 0?"),
            GraphEdit(op=EditOp.ADD_EDGE, source="n2", target="t1", label="go"),
            GraphEdit(op=EditOp.RELABEL_EDGE, id="u1", label="next"),
            GraphEdit(op=EditOp.RESHAPE_NODE, id="n2", shape=NodeShape.PROCESS),
            GraphEdit(op=EditOp.SET_EDGE_ENDS, id="e1", source="n1", target="t1"),
            GraphEdit(op=EditOp.REMOVE_NODE, id="n3"),
        ],
    )
    assert [n.id for n in edited.nodes] == ["n1", "n2", "t1"]
    assert [(e.id, e.source, e.target, e.label) for e in edited.edges] == [
        ("e1", "n1", "t1", ""),
        ("u1", "n2", "t1", "next"),
    ]
    assert edited.edited_by_teacher
    for bad in (
        [GraphEdit(op=EditOp.REMOVE_NODE, id="zz")],
        [GraphEdit(op=EditOp.ADD_EDGE, source="n1", target="zz")],
        [GraphEdit(op=EditOp.ADD_NODE, id="n1")],
        [GraphEdit(op=EditOp.RESHAPE_NODE, id="n1")],
        [GraphEdit(op=EditOp.RELABEL_NODE, id="n1", label="x" * 201)],
        [],
    ):
        with pytest.raises(InvariantError):
            apply_edits(graph, bad)


def test_a_line_joined_across_two_shapes_is_split_between_them() -> None:
    left = DetectedShape(
        shape=NodeShape.TERMINAL, box=Box(x0=0, y0=100, x1=400, y1=160), confidence=0.9
    )
    right = DetectedShape(
        shape=NodeShape.PROCESS, box=Box(x0=600, y0=90, x1=1000, y1=170), confidence=0.9
    )
    arrow = DetectedArrow(
        box=Box(x0=400, y0=120, x1=600, y1=140), tail=Point(x=402, y=130),
        head=Point(x=598, y=130), has_head=True, confidence=0.9,
    )  # fmt: skip
    # one OCR line from x 20 to 980: "How it evolves" in the left box, "What world is now" right
    line = DiagramText(
        text="How it evolves What world is now", box=Box(x0=20, y0=110, x1=980, y1=150)
    )
    graph = GraphBuilder().build(
        DiagramDetection(width=1000, height=300, shapes=(left, right), arrows=(arrow,)),
        [line],
        recognizer=REC,
    )
    by_shape = {n.shape: n.label for n in graph.nodes}
    assert by_shape[NodeShape.TERMINAL] == "How it evolves"
    assert by_shape[NodeShape.PROCESS] == "What world is now"
    assert graph.edges[0].label == ""


def test_a_line_inside_one_shape_stays_whole() -> None:
    from tarn_core.services.diagrams.build import split_across_shapes

    text = DiagramText(text="read n and m", box=Box(x0=10, y0=10, x1=200, y1=40))
    assert split_across_shapes(text, [Box(x0=0, y0=0, x1=300, y1=60)]) == [text]
    assert split_across_shapes(text, []) == [text]


def test_names_at_open_arrow_ends_are_free_labels_and_head_marks_are_dropped() -> None:
    box = DetectedShape(
        shape=NodeShape.PROCESS, box=Box(x0=400, y0=300, x1=700, y1=400), confidence=0.9
    )
    arrow = DetectedArrow(
        box=Box(x0=540, y0=120, x1=560, y1=298), tail=Point(x=550, y=125),
        head=Point(x=550, y=298), has_head=True, confidence=0.9,
    )  # fmt: skip
    texts = [
        DiagramText(text="Sensors", box=Box(x0=500, y0=80, x1=620, y1=115)),
        DiagramText(text="V", box=Box(x0=540, y0=280, x1=560, y1=298)),
    ]
    graph = GraphBuilder().build(
        DiagramDetection(width=1000, height=500, shapes=(box,), arrows=(arrow,)),
        texts,
        recognizer=REC,
    )
    (edge,) = graph.edges
    assert (edge.source, edge.target, edge.label) == (None, "n1", "")
    assert [f.text for f in graph.free_labels] == ["Sensors"]


def test_computer_drawn_labels_go_to_their_arrows_and_shapes() -> None:
    """The layout of a typical block diagram: three boxes in a row, a label stacked over
    several lines above each arrow, one under the second arrow, a watermark below a box and
    a speck read as text."""
    shapes = tuple(
        DetectedShape(
            shape=NodeShape.PROCESS, box=Box(x0=x, y0=80, x1=x + 170, y1=185), confidence=0.9
        )
        for x in (40, 330, 650)
    )
    arrows = (
        DetectedArrow(
            box=Box(x0=210, y0=128, x1=332, y1=142), tail=Point(x=212, y=135),
            head=Point(x=330, y=135), has_head=True, confidence=0.9,
        ),
        DetectedArrow(
            box=Box(x0=500, y0=128, x1=652, y1=142), tail=Point(x=502, y=135),
            head=Point(x=650, y=135), has_head=True, confidence=0.9,
        ),
    )  # fmt: skip

    def t(text: str, x0: int, y0: int, x1: int, y1: int) -> DiagramText:
        return DiagramText(text=text, box=Box(x0=x0, y0=y0, x1=x1, y1=y1))

    texts = [
        t("( Sensors", 50, 110, 200, 135),
        t("Continuously", 223, 33, 313, 59),
        t("measures", 230, 59, 307, 78),
        t("the", 253, 79, 284, 101),
        t("Controller .", 345, 110, 490, 135),
        t("If temperature gets", 503, 32, 642, 57),
        t("above certain", 521, 52, 624, 74),
        t("temperature then", 510, 74, 637, 93),
        t("turn on the fan", 515, 93, 630, 112),
        t("Else no", 533, 143, 593, 166),
        t("action", 537, 165, 588, 188),
        t("Actuator", 680, 110, 790, 135),
        t("GeeksforGeeks", 714, 211, 852, 236),
        t("0.", 100, 10, 110, 19),
    ]
    graph = GraphBuilder().build(
        DiagramDetection(width=870, height=270, shapes=shapes, arrows=arrows),
        texts,
        recognizer=REC,
    )
    assert [n.label for n in graph.nodes] == ["Sensors", "Controller", "Actuator"]
    e1, e2 = graph.edges
    assert e1.label == "Continuously measures the"
    assert e2.label == (
        "If temperature gets above certain temperature then turn on the fan Else no action"
    )
    assert [f.text for f in graph.free_labels] == ["GeeksforGeeks"]


def test_lines_are_cut_at_shape_edges_before_reading() -> None:
    from tarn_core.services.ocr.reader import cut_line

    line = Box(x0=50, y0=92, x1=777, y1=140)
    shapes = [Box(x0=43, y0=82, x1=215, y1=185), Box(x0=332, y0=82, x1=505, y1=184)]
    assert cut_line(line, shapes) == [
        Box(x0=50, y0=92, x1=215, y1=140),
        Box(x0=215, y0=92, x1=332, y1=140),
        Box(x0=332, y0=92, x1=505, y1=140),
        Box(x0=505, y0=92, x1=777, y1=140),
    ]
    assert cut_line(line, [Box(x0=0, y0=300, x1=900, y1=400)]) == [line]  # not beside it
