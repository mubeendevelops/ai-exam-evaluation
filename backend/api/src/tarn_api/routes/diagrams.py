"""Diagram graphs (R6, U6 Q20): the reference diagram's graph (global content, edited by the
owning college as a new version) and the drawings recognised in a booklet (college data,
edited by the teacher, after which the answer is re-scored by the worker), and the R6
comparison documents of an answer's latest score. Recognition itself runs in the worker."""

from uuid import UUID

from fastapi import APIRouter, Response, status

from tarn_api.backends import unit_of_work
from tarn_api.schemas import (
    DiagramComparisonOut,
    DiagramGraphOut,
    ErrorOut,
    GraphEditIn,
    ReferenceGraphEditIn,
    ReferenceGraphOut,
    StudentDiagramOut,
    StudentGraphEditIn,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.common import Box
from tarn_core.domain.content import ReferenceDiagram
from tarn_core.domain.diagram import DiagramGraph, DiagramKind, NodeShape, StudentDiagram
from tarn_core.domain.tenancy import Role
from tarn_core.errors import NotFoundError
from tarn_core.ids import AnswerId, BookletId, QuestionId, ReferenceDiagramId, StudentDiagramId
from tarn_core.services.content import ensure_can_edit
from tarn_core.services.diagrams.editor import EditOp, GraphEdit
from tarn_core.services.diagrams.graph_json import graph_to_json
from tarn_core.services.diagrams.jobs import queue_reference_recognition

router = APIRouter(
    prefix="/api/v1",
    tags=["diagrams"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)
_EDIT_ERRORS: dict[int | str, dict[str, object]] = {
    422: {
        "model": ErrorOut,
        "description": "An edit names an unknown node or edge, or a label is too long.",
    },
    409: {
        "model": ErrorOut,
        "description": "The graph changed since it was loaded (reload and edit again).",
    },
}


def _graph(graph: DiagramGraph) -> DiagramGraphOut:
    return DiagramGraphOut.model_validate(graph_to_json(graph))


def _edits(edits: list[GraphEditIn]) -> list[GraphEdit]:
    return [
        GraphEdit(
            op=EditOp(e.op),
            id=e.id,
            shape=None if e.shape is None else NodeShape(e.shape),
            label=e.label,
            source=e.source,
            target=e.target,
            directed=e.directed,
            box=None if e.box is None else Box(x0=e.box[0], y0=e.box[1], x1=e.box[2], y1=e.box[3]),
        )
        for e in edits
    ]


def _reference_out(question_id: UUID, d: ReferenceDiagram) -> ReferenceGraphOut:
    return ReferenceGraphOut(
        diagram_id=d.id,
        version=d.meta.version,
        kind=d.kind.value,
        recognition=d.recognition.value,
        graph=_graph(d.graph),
        content_url=f"/api/v1/questions/{question_id}/diagrams/{d.id}/content",
    )


def _student_out(d: StudentDiagram) -> StudentDiagramOut:
    return StudentDiagramOut(
        id=d.id,
        segment_id=d.segment_id,
        region_id=d.region_id,
        version=d.version,
        kind=d.kind.value,
        box=[d.box.x0, d.box.y0, d.box.x1, d.box.y1],
        graph=_graph(d.graph),
    )


# --- reference diagrams ----------------------------------------------------------------------


@router.get(
    "/questions/{question_id}/diagrams/{diagram_id}/graph",
    response_model=ReferenceGraphOut,
    summary="The reference diagram's graph (latest version)",
)
def reference_graph(
    question_id: UUID, diagram_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> ReferenceGraphOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        diagram, _ = unit.bank.read_reference_diagram(
            QuestionId(question_id), ReferenceDiagramId(diagram_id)
        )
    return _reference_out(question_id, diagram)


@router.post(
    "/questions/{question_id}/diagrams/{diagram_id}/graph/edits",
    response_model=ReferenceGraphOut,
    responses=_EDIT_ERRORS,
    summary="Correct the reference graph or change its kind (owning college only)",
    description="Applies the edits in order and stores the next version. Add, remove, relabel or "
    "reshape nodes; add, remove or relabel edges; reverse an arrow; re-attach an arrow's ends. "
    "Removing a node removes its edges. Scores already made keep the version they used.",
)
def edit_reference_graph(
    question_id: UUID,
    diagram_id: UUID,
    body: ReferenceGraphEditIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> ReferenceGraphOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.bank.read_reference_diagram(QuestionId(question_id), ReferenceDiagramId(diagram_id))
        diagram = unit.reference_diagrams.edit(
            who.college_id,
            who.user_id,
            ReferenceDiagramId(diagram_id),
            expected_version=body.expected_version,
            edits=_edits(body.edits),
            kind=None if body.kind is None else DiagramKind(body.kind),
        )
    return _reference_out(question_id, diagram)


@router.post(
    "/questions/{question_id}/diagrams/{diagram_id}/recognize",
    status_code=status.HTTP_202_ACCEPTED,
    response_class=Response,
    summary="Read the reference PNG again (owning college only)",
    description="Queues recognition; the worker stores the result as the next version, "
    "replacing any edits in that version (earlier versions are kept).",
)
def recognize_reference(
    question_id: UUID, diagram_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        diagram, _ = unit.bank.read_reference_diagram(
            QuestionId(question_id), ReferenceDiagramId(diagram_id)
        )
        ensure_can_edit(who.college_id, diagram)
        queue_reference_recognition(unit.scope.jobs, who.college_id, who.user_id, diagram)
    return Response(status_code=status.HTTP_202_ACCEPTED)


# --- student drawings ------------------------------------------------------------------------


@router.get(
    "/booklets/{booklet_id}/diagrams",
    response_model=list[StudentDiagramOut],
    summary="The diagrams recognised in a booklet",
)
def booklet_diagrams(
    booklet_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> list[StudentDiagramOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.scope.booklets.get(who.college_id, BookletId(booklet_id))
        diagrams = unit.scope.booklets.diagrams(who.college_id, BookletId(booklet_id))
    return [_student_out(d) for d in diagrams]


@router.post(
    "/booklets/{booklet_id}/diagrams/{diagram_id}/edits",
    response_model=StudentDiagramOut,
    responses={**_EDIT_ERRORS, 423: {"model": ErrorOut}},
    summary="Correct a recognised drawing; its answer is re-scored",
    description="Applies the edits in order (as for the reference) and bumps the version. Needs "
    "the booklet's lock (423). The answer is re-scored in the background; an approved answer "
    "must be reopened first (409).",
)
def edit_student_diagram(
    booklet_id: UUID,
    diagram_id: UUID,
    body: StudentGraphEditIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> StudentDiagramOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        diagram = unit.student_diagrams(BookletId(booklet_id)).edit(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            StudentDiagramId(diagram_id),
            expected_version=body.expected_version,
            edits=_edits(body.edits),
        )
    return _student_out(diagram)


@router.get(
    "/booklets/{booklet_id}/answers/{answer_id}/diagram-comparisons",
    response_model=list[DiagramComparisonOut],
    summary="The R6 comparison documents of the answer's latest score",
    description="One per diagram criterion that was compared; criteria left to the teacher "
    "(no reference graph, no drawing) have none.",
)
def diagram_comparisons(
    booklet_id: UUID, answer_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> list[DiagramComparisonOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        answer = unit.scope.booklets.get_answer(who.college_id, AnswerId(answer_id))
        if answer.booklet_id != BookletId(booklet_id):
            raise NotFoundError(f"answer {answer_id}")
        scores = unit.scope.scores.scores(who.college_id, answer.id)
    if not scores:
        return []
    return [
        DiagramComparisonOut(
            criterion_id=c.criterion.id,
            criterion_version=c.criterion.version,
            credit=float(c.credit),
            similarity=c.similarity,
            flags=list(c.flags),
            document=c.detail,
        )
        for c in scores[-1].criterion_scores
        if isinstance(c.detail, dict)
    ]
