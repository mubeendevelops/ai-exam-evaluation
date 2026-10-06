"""Diagram graphs (R6): reference PNGs and student drawings both become one of these.

A graph holds nodes (shapes with their labels) and edges (arrows or plain lines). An edge end
that touches no shape is ``None``: a dangling arrow (C29). Boxes and points are in the pixels
of the image the graph was read from (the reference PNG, or the student's page)."""

from dataclasses import dataclass
from enum import StrEnum

from tarn_core.domain.common import Box, EngineRef, check_unit_interval
from tarn_core.errors import InvariantError
from tarn_core.ids import BookletId, CollegeId, RegionId, SegmentId, StudentDiagramId


class NodeShape(StrEnum):
    """First scope: flowcharts, block diagrams, networks, trees (D7)."""

    TERMINAL = "terminal"
    PROCESS = "process"
    DECISION = "decision"
    IO = "io"
    CIRCLE = "circle"
    BLOCK = "block"
    OTHER = "other"


class DiagramKind(StrEnum):
    """What a reference diagram is. Each kind is read by a recognizer and compared by a
    comparator registered for it; the first four share the shape-and-arrow detector and the
    graph comparator. Circuits, plots and labelled drawings come later (U6 Q22) as their own
    plug-ins: until one is registered their criteria stay with the teacher."""

    FLOWCHART = "flowchart"
    BLOCK = "block"
    NETWORK = "network"
    TREE = "tree"
    CIRCUIT = "circuit"
    PLOT = "plot"
    LABELLED_DRAWING = "labelled_drawing"


NODE_AND_EDGE_KINDS = frozenset(
    {DiagramKind.FLOWCHART, DiagramKind.BLOCK, DiagramKind.NETWORK, DiagramKind.TREE}
)


class RecognitionState(StrEnum):
    """Where a reference diagram's graph came from."""

    PENDING = "pending"
    """Uploaded; the recognizer has not read it yet (the graph is empty)."""
    RECOGNISED = "recognised"
    FAILED = "failed"
    """The recognizer could not read it (or none is configured); the teacher may draw it."""
    EDITED = "edited"
    """A teacher changed the graph after recognition (or drew it)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Point:
    x: int
    y: int

    def __post_init__(self) -> None:
        if min(self.x, self.y) < 0:
            raise InvariantError("a point must not be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramNode:
    id: str
    shape: NodeShape
    label: str = ""
    box: Box | None = None
    confidence: float = 1.0
    """The recognizer's confidence in the shape (1 for a node drawn by the teacher)."""
    label_confidence: float | None = None
    """The OCR line score of the label (None: no label, or typed by the teacher)."""

    def __post_init__(self) -> None:
        if not self.id:
            raise InvariantError("node id must not be empty")
        check_unit_interval("node confidence", self.confidence)
        if self.label_confidence is not None:
            check_unit_interval("label confidence", self.label_confidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramEdge:
    """An arrow from ``source`` (its tail) to ``target`` (its head), or a plain line when
    ``directed`` is false. An end that touches no shape is None (a dangling arrow)."""

    id: str
    source: str | None
    target: str | None
    label: str = ""
    directed: bool = True
    confidence: float = 1.0
    box: Box | None = None
    tail: Point | None = None
    head: Point | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise InvariantError("edge id must not be empty")
        check_unit_interval("edge confidence", self.confidence)

    @property
    def dangling(self) -> bool:
        return self.source is None or self.target is None


@dataclass(frozen=True, slots=True, kw_only=True)
class FreeLabel:
    """Text inside the diagram that belongs to no shape and no arrow (a caption, a stray
    word): kept for the teacher and compared with the glossary, never matched as a node."""

    text: str
    box: Box | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise InvariantError("a free label must have text")
        if self.confidence is not None:
            check_unit_interval("label confidence", self.confidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramGraph:
    """Nodes, edges and their labels. ``recognizer`` is None for a graph drawn by hand;
    ``label_engines`` are the OCR engines that read the labels."""

    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...] = ()
    recognizer: EngineRef | None = None
    edited_by_teacher: bool = False
    label_engines: tuple[str, ...] = ()
    free_labels: tuple[FreeLabel, ...] = ()

    def __post_init__(self) -> None:
        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise InvariantError("diagram node ids must be unique")
        edge_ids = [e.id for e in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise InvariantError("diagram edge ids must be unique")
        known = set(node_ids)
        for e in self.edges:
            for end in (e.source, e.target):
                if end is not None and end not in known:
                    raise InvariantError(f"edge {e.id} points at an unknown node")

    @property
    def labels(self) -> tuple[str, ...]:
        """Every non-empty node and edge label, in graph order."""
        node_labels = (n.label for n in self.nodes if n.label)
        return (*node_labels, *(e.label for e in self.edges if e.label))

    @property
    def empty(self) -> bool:
        return not self.nodes and not self.edges

    def node(self, node_id: str) -> DiagramNode:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise InvariantError(f"no node {node_id!r}")

    @property
    def min_confidence(self) -> float:
        """The lowest recognition confidence of any node or edge (1 for an empty graph)."""
        values = [n.confidence for n in self.nodes] + [e.confidence for e in self.edges]
        return min(values, default=1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class StudentDiagram:
    """A diagram recognised in a student's answer; ``version`` bumps on every teacher edit.
    ``region_id`` is the page's diagram region it was read from (None: drawn by the teacher)."""

    id: StudentDiagramId
    college_id: CollegeId
    booklet_id: BookletId
    segment_id: SegmentId
    box: Box
    graph: DiagramGraph
    version: int = 1
    region_id: RegionId | None = None
    kind: DiagramKind = DiagramKind.FLOWCHART

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantError("diagram version starts at 1")


# --- what a recognizer sees (before the core builds the graph) --------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class DetectedShape:
    shape: NodeShape
    box: Box
    confidence: float

    def __post_init__(self) -> None:
        check_unit_interval("shape confidence", self.confidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class DetectedArrow:
    """An arrow from ``tail`` to ``head``, or a plain line between ``tail`` and ``head`` when
    ``has_head`` is false (the two ends are then interchangeable)."""

    box: Box
    tail: Point
    head: Point
    has_head: bool
    confidence: float

    def __post_init__(self) -> None:
        check_unit_interval("arrow confidence", self.confidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramDetection:
    """Shapes and arrows found in an image, in its pixels."""

    width: int
    height: int
    shapes: tuple[DetectedShape, ...] = ()
    arrows: tuple[DetectedArrow, ...] = ()

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise InvariantError("a detection needs the image size")


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramText:
    """A line of text read inside a diagram (best-of-N OCR), in the same pixels."""

    text: str
    box: Box
    confidence: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerDiagram:
    """A student's diagram as a scorer sees it: the graph and which version of which stored
    diagram it is (None for a graph given directly, e.g. in tests)."""

    graph: DiagramGraph
    diagram_id: str | None = None
    version: int | None = None
