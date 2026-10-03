"""Diagram graphs (R6): reference PNGs and student drawings both become one of these."""

from dataclasses import dataclass
from enum import StrEnum

from tarn_core.domain.common import Box, EngineRef, check_unit_interval
from tarn_core.errors import InvariantError
from tarn_core.ids import BookletId, CollegeId, SegmentId, StudentDiagramId


class NodeShape(StrEnum):
    """First scope: flowcharts, block diagrams, networks, trees (D7)."""

    TERMINAL = "terminal"
    PROCESS = "process"
    DECISION = "decision"
    IO = "io"
    CIRCLE = "circle"
    BLOCK = "block"
    OTHER = "other"


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramNode:
    id: str
    shape: NodeShape
    label: str = ""
    box: Box | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.id:
            raise InvariantError("node id must not be empty")
        check_unit_interval("node confidence", self.confidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramEdge:
    id: str
    source: str
    target: str
    label: str = ""
    directed: bool = True
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.id:
            raise InvariantError("edge id must not be empty")
        check_unit_interval("edge confidence", self.confidence)


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramGraph:
    """Nodes, edges and their labels. ``recognizer`` is None for a graph drawn by hand."""

    nodes: tuple[DiagramNode, ...]
    edges: tuple[DiagramEdge, ...] = ()
    recognizer: EngineRef | None = None
    edited_by_teacher: bool = False

    def __post_init__(self) -> None:
        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise InvariantError("diagram node ids must be unique")
        edge_ids = [e.id for e in self.edges]
        if len(set(edge_ids)) != len(edge_ids):
            raise InvariantError("diagram edge ids must be unique")
        known = set(node_ids)
        for e in self.edges:
            if e.source not in known or e.target not in known:
                raise InvariantError(f"edge {e.id} points at an unknown node")

    @property
    def labels(self) -> tuple[str, ...]:
        """Every non-empty node and edge label, in graph order."""
        node_labels = (n.label for n in self.nodes if n.label)
        return (*node_labels, *(e.label for e in self.edges if e.label))


@dataclass(frozen=True, slots=True, kw_only=True)
class StudentDiagram:
    """A diagram recognised in a student's answer; ``version`` bumps on every teacher edit."""

    id: StudentDiagramId
    college_id: CollegeId
    booklet_id: BookletId
    segment_id: SegmentId
    box: Box
    graph: DiagramGraph
    version: int = 1

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantError("diagram version starts at 1")
