"""Teacher edits of a graph (U6 Q20): add, remove, relabel or reshape nodes; add, remove or
relabel edges, reverse an arrow, re-attach its ends. Pure: a graph and a list of edits in, the
edited graph out (marked ``edited_by_teacher``). Errors name ids, never labels."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from tarn_core.domain.common import Box
from tarn_core.domain.diagram import DiagramEdge, DiagramGraph, DiagramNode, NodeShape
from tarn_core.errors import InvariantError

MAX_NODES = 200
MAX_EDGES = 400
MAX_LABEL = 200
MAX_EDITS = 500


class EditOp(StrEnum):
    ADD_NODE = "add_node"
    REMOVE_NODE = "remove_node"
    RELABEL_NODE = "relabel_node"
    RESHAPE_NODE = "reshape_node"
    ADD_EDGE = "add_edge"
    REMOVE_EDGE = "remove_edge"
    RELABEL_EDGE = "relabel_edge"
    REVERSE_EDGE = "reverse_edge"
    SET_EDGE_ENDS = "set_edge_ends"


@dataclass(frozen=True, slots=True, kw_only=True)
class GraphEdit:
    op: EditOp
    id: str | None = None
    """The node or edge acted on (for an add: the new id, or None to have one made)."""
    shape: NodeShape | None = None
    label: str | None = None
    source: str | None = None
    target: str | None = None
    directed: bool | None = None
    box: Box | None = None


def _label(value: str | None) -> str:
    text = " ".join((value or "").split())
    if len(text) > MAX_LABEL:
        raise InvariantError(f"a label is longer than {MAX_LABEL} characters")
    return text


def _fresh(prefix: str, taken: set[str]) -> str:
    k = 1
    while f"{prefix}{k}" in taken:
        k += 1
    return f"{prefix}{k}"


def apply_edits(graph: DiagramGraph, edits: Sequence[GraphEdit]) -> DiagramGraph:
    if not edits:
        raise InvariantError("no edits given")
    if len(edits) > MAX_EDITS:
        raise InvariantError(f"at most {MAX_EDITS} edits at once")
    nodes: dict[str, DiagramNode] = {n.id: n for n in graph.nodes}
    edges: dict[str, DiagramEdge] = {e.id: e for e in graph.edges}

    def node(node_id: str | None) -> DiagramNode:
        if node_id is None or node_id not in nodes:
            raise InvariantError(f"no node {node_id!r}")
        return nodes[node_id]

    def edge(edge_id: str | None) -> DiagramEdge:
        if edge_id is None or edge_id not in edges:
            raise InvariantError(f"no edge {edge_id!r}")
        return edges[edge_id]

    def end(node_id: str | None) -> str | None:
        return None if node_id is None else node(node_id).id

    for k, e in enumerate(edits, 1):
        match e.op:
            case EditOp.ADD_NODE:
                new_id = e.id or _fresh("t", set(nodes) | set(edges))
                if new_id in nodes or new_id in edges:
                    raise InvariantError(f"edit {k}: id {new_id!r} is already used")
                nodes[new_id] = DiagramNode(
                    id=new_id,
                    shape=e.shape or NodeShape.PROCESS,
                    label=_label(e.label),
                    box=e.box,
                )
            case EditOp.REMOVE_NODE:
                gone = node(e.id).id
                del nodes[gone]
                for edge_id in [x.id for x in edges.values() if gone in (x.source, x.target)]:
                    del edges[edge_id]
            case EditOp.RELABEL_NODE:
                n = node(e.id)
                nodes[n.id] = replace(n, label=_label(e.label), label_confidence=None)
            case EditOp.RESHAPE_NODE:
                n = node(e.id)
                if e.shape is None:
                    raise InvariantError(f"edit {k}: reshape needs a shape")
                nodes[n.id] = replace(n, shape=e.shape, confidence=1.0)
            case EditOp.ADD_EDGE:
                new_id = e.id or _fresh("u", set(nodes) | set(edges))
                if new_id in nodes or new_id in edges:
                    raise InvariantError(f"edit {k}: id {new_id!r} is already used")
                edges[new_id] = DiagramEdge(
                    id=new_id,
                    source=node(e.source).id,
                    target=node(e.target).id,
                    label=_label(e.label),
                    directed=True if e.directed is None else e.directed,
                )
            case EditOp.REMOVE_EDGE:
                del edges[edge(e.id).id]
            case EditOp.RELABEL_EDGE:
                x = edge(e.id)
                edges[x.id] = replace(x, label=_label(e.label))
            case EditOp.REVERSE_EDGE:
                x = edge(e.id)
                edges[x.id] = replace(x, source=x.target, target=x.source, tail=x.head, head=x.tail)
            case EditOp.SET_EDGE_ENDS:
                x = edge(e.id)
                edges[x.id] = replace(
                    x,
                    source=end(e.source),
                    target=end(e.target),
                    directed=x.directed if e.directed is None else e.directed,
                    confidence=1.0,
                )
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise InvariantError(f"a diagram holds at most {MAX_NODES} nodes and {MAX_EDGES} edges")
    return replace(
        graph,
        nodes=tuple(nodes.values()),
        edges=tuple(edges.values()),
        edited_by_teacher=True,
    )
