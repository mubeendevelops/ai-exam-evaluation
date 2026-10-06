"""Graph comparison (design.md "Diagram comparison (R6)", steps 2-5).

1. **Labels** are normalised and the student's snapped to the question's glossary (teacher
   terms + the reference's labels).
2. **Nodes** are matched one to one by an optimal assignment (Hungarian) over the bipartite
   edit-cost matrix of Riesen and Bunke: substituting reference node *r* by student node *s*
   costs ``w_label·label distance + w_shape·shape mismatch + w_degree·degree difference`` (at
   most 1), deleting or inserting a node costs 1. A pair dearer than ``policy.accept`` is never
   made: both nodes stay unmatched. The degree term only steers the matching (unlabelled
   trees); in the edit distance a matched pair costs its label and shape mismatch alone,
   because missing or extra edges are counted as edge edits.
3. **Edges** between matched nodes are ``present``, ``reversed`` (a directed edge drawn the
   other way) or ``missing``; student edges left over are ``extra``. Lines without a head
   (trees, networks) are compared without direction.
4. **Similarity** = 1 − normalised graph edit distance, where the edit distance is the cost of
   the edit path the matching implies (node substitutions and insertions/deletions, 1 per
   missing, extra or reversed edge) and the normaliser is ``|V_ref| + |E_ref| + |V_student| +
   |E_student|`` (the cost of deleting one graph and inserting the other), so identical
   graphs give 1 and graphs with nothing in common give 0. The edit distance of the bipartite
   matching is an upper bound of the exact one (exact GED is NP-hard).

Sub-scores: ``nodes`` = Dice over node match quality (label and shape), ``edges`` = Dice over
present edges (a reversed edge counts ½), ``labels`` = mean similarity of each reference label
to the label in the matched student place (0 when unmatched)."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.diagram import (
    NODE_AND_EDGE_KINDS,
    DiagramEdge,
    DiagramGraph,
    DiagramKind,
    NodeShape,
)
from tarn_core.services.diagrams.hungarian import assign
from tarn_core.services.diagrams.labels import (
    Glossary,
    LabelMatch,
    SnappedLabel,
    compare_labels,
    label_distance,
    label_similarity,
    normalise_label,
)
from tarn_core.services.diagrams.policy import DiagramPolicy

_FORBIDDEN = 2.5  # dearer than deleting and inserting (2): the assignment never picks it
_COMPATIBLE = frozenset({frozenset({NodeShape.PROCESS, NodeShape.BLOCK})})


class EdgeStatus(StrEnum):
    PRESENT = "present"
    MISSING = "missing"
    REVERSED = "reversed"
    EXTRA = "extra"


class AnomalyType(StrEnum):
    EXTRA_NODE = "extra_node"
    EXTRA_EDGE = "extra_edge"
    EXTRA_LABEL = "extra_label"
    REVERSED_EDGE = "reversed_edge"
    DISCONNECTED = "disconnected"
    DANGLING_ARROW = "dangling_arrow"


def shape_cost(a: NodeShape, b: NodeShape) -> float:
    """0 same (a process box and a block are the same drawing), ½ when either is unknown, 1
    otherwise."""
    if a == b or frozenset({a, b}) in _COMPATIBLE:
        return 0.0
    if NodeShape.OTHER in (a, b):
        return 0.5
    return 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class NodeResult:
    """One student node (R6 e)."""

    id: str
    shape: NodeShape
    label: str
    matched_ref: str | None
    label_match: LabelMatch | None
    """Against the matched reference node's label; None when unmatched."""
    glossary_term: str | None
    glossary_match: LabelMatch
    shape_match: bool | None
    confidence: float
    label_confidence: float | None
    box: Box | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceNodeResult:
    id: str
    shape: NodeShape
    label: str
    matched_student: str | None
    label_match: LabelMatch | None
    shape_match: bool | None


@dataclass(frozen=True, slots=True, kw_only=True)
class EdgeResult:
    """One reference edge (present, reversed or missing) or one extra student edge (R6 f)."""

    status: EdgeStatus
    ref: tuple[str | None, str | None] | None
    student: tuple[str | None, str | None] | None
    ref_edge: str | None = None
    student_edge: str | None = None
    label: str = ""
    """The reference edge's label (the student's for an extra edge)."""
    label_match: LabelMatch | None = None
    directed: bool = True
    confidence: float | None = None
    box: Box | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LabelResult:
    """One label the student wrote, against the glossary (R6 b)."""

    element: str  # node | edge | free
    id: str | None
    label: str
    glossary_term: str | None
    match: LabelMatch
    confidence: float | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Anomaly:
    type: AnomalyType
    student: tuple[str, ...]
    """The student node (extra node), edge (dangling arrow), the edge's two ends (extra or
    reversed edge), the part's nodes (disconnected); empty for an extra free label."""
    edge: str | None = None
    """The student edge of an extra or reversed edge."""
    label: str = ""
    ref: tuple[str, ...] = ()
    end: str | None = None
    """For a dangling arrow: ``tail``, ``head`` or ``both`` (``end``/``both`` for a line)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SubScores:
    nodes: float
    edges: float
    labels: float


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramComparison:
    similarity: float
    sub_scores: SubScores
    edit_distance: float
    nodes: tuple[NodeResult, ...]
    reference_nodes: tuple[ReferenceNodeResult, ...]
    edges: tuple[EdgeResult, ...]
    missing_nodes: tuple[str, ...]
    missing_edges: tuple[tuple[str | None, str | None], ...]
    missing_labels: tuple[str, ...]
    anomalies: tuple[Anomaly, ...]
    labels: tuple[LabelResult, ...]
    recognizer: EngineRef | None
    label_engines: tuple[str, ...]
    edited_by_teacher: bool
    min_confidence: float
    comparator: EngineRef

    @property
    def matched_nodes(self) -> int:
        return sum(1 for r in self.reference_nodes if r.matched_student is not None)

    def counts(self, status: EdgeStatus) -> int:
        return sum(1 for e in self.edges if e.status is status)


class DiagramComparator:
    """Compares a student graph with the reference graph of the same kind. A plug-in point:
    later kinds (circuits, plots, labelled drawings) bring their own comparator."""

    ref: EngineRef
    kinds: frozenset[DiagramKind]

    def compare(
        self, reference: DiagramGraph, student: DiagramGraph, glossary: Sequence[str]
    ) -> DiagramComparison:
        raise NotImplementedError


class GraphComparator(DiagramComparator):
    """Flowcharts, block diagrams, networks and trees."""

    ref = EngineRef(name="graph-compare", version="1")
    kinds = NODE_AND_EDGE_KINDS

    def __init__(self, policy: DiagramPolicy | None = None) -> None:
        self.policy = policy or DiagramPolicy()

    def compare(
        self, reference: DiagramGraph, student: DiagramGraph, glossary: Sequence[str]
    ) -> DiagramComparison:
        p = self.policy
        terms = Glossary([*glossary, *reference.labels], close=p.snap)
        snapped = {s.id: terms.snap(s.label) for s in student.nodes}
        ref_nodes, stu_nodes = reference.nodes, student.nodes
        n, m = len(ref_nodes), len(stu_nodes)
        ref_deg, stu_deg = _degrees(reference), _degrees(student)

        def label_cost(i: int, j: int) -> float:
            r, s = ref_nodes[i], stu_nodes[j]
            if not r.label and not s.label:
                return 0.0
            return 1.0 - label_similarity(r.label, snapped[s.id])

        def sub_cost(i: int, j: int) -> float:
            r, s = ref_nodes[i], stu_nodes[j]
            dr, ds = ref_deg[r.id], stu_deg[s.id]
            degree = abs(dr - ds) / max(1, dr, ds)
            return (
                p.label_weight * label_cost(i, j)
                + p.shape_weight * shape_cost(r.shape, s.shape)
                + p.degree_weight * degree
            )

        size = n + m
        inf = float("inf")
        matrix = [[0.0] * size for _ in range(size)]
        costs: dict[tuple[int, int], float] = {}
        for i in range(size):
            for j in range(size):
                if i < n and j < m:
                    c = sub_cost(i, j)
                    costs[i, j] = c
                    matrix[i][j] = c if c <= p.accept else _FORBIDDEN
                elif i < n:
                    matrix[i][j] = 1.0 if j - m == i else inf
                elif j < m:
                    matrix[i][j] = 1.0 if i - n == j else inf
        chosen = assign(matrix) if size else []
        to_student: dict[str, str] = {}
        to_ref: dict[str, str] = {}
        node_cost = 0.0
        quality = 0.0
        for i in range(n):
            j = chosen[i]
            if j < m and costs[i, j] <= p.accept:
                to_student[ref_nodes[i].id] = stu_nodes[j].id
                to_ref[stu_nodes[j].id] = ref_nodes[i].id
                q = 1.0 - (
                    p.label_weight * label_cost(i, j)
                    + p.shape_weight * shape_cost(ref_nodes[i].shape, stu_nodes[j].shape)
                ) / (p.label_weight + p.shape_weight)
                quality += q
                node_cost += 1.0 - q  # structure is paid for by the edges, not twice
        node_cost += (n - len(to_student)) + (m - len(to_ref))

        edges, used = _match_edges(reference, student, to_student, p.close)
        present = sum(1 for e in edges if e.status is EdgeStatus.PRESENT)
        reversed_ = sum(1 for e in edges if e.status is EdgeStatus.REVERSED)
        missing = sum(1 for e in edges if e.status is EdgeStatus.MISSING)
        extra_edges = [e for e in student.edges if e.id not in used]
        edges.extend(
            EdgeResult(
                status=EdgeStatus.EXTRA,
                ref=None,
                student=(e.source, e.target),
                student_edge=e.id,
                label=e.label,
                directed=e.directed,
                confidence=e.confidence,
                box=e.box,
            )
            for e in extra_edges
        )
        edge_cost = float(missing + reversed_ + len(extra_edges))
        ged = node_cost + edge_cost
        denominator = n + m + len(reference.edges) + len(student.edges)
        similarity = 1.0 if denominator == 0 else max(0.0, 1.0 - ged / denominator)

        node_score = 1.0 if n + m == 0 else 2 * quality / (n + m)
        edge_total = len(reference.edges) + len(student.edges)
        edge_score = 1.0 if edge_total == 0 else 2 * (present + 0.5 * reversed_) / edge_total
        label_score, missing_labels = _label_scores(
            reference, student, to_student, snapped, edges, p.close
        )

        stu_by_id = {s.id: s for s in stu_nodes}
        ref_by_id = {r.id: r for r in ref_nodes}
        node_results = tuple(
            NodeResult(
                id=s.id,
                shape=s.shape,
                label=s.label,
                matched_ref=to_ref.get(s.id),
                label_match=(
                    None
                    if s.id not in to_ref
                    else compare_labels(ref_by_id[to_ref[s.id]].label, snapped[s.id], close=p.close)
                ),
                glossary_term=snapped[s.id].term,
                glossary_match=snapped[s.id].match,
                shape_match=(
                    None
                    if s.id not in to_ref
                    else shape_cost(ref_by_id[to_ref[s.id]].shape, s.shape) == 0
                ),
                confidence=s.confidence,
                label_confidence=s.label_confidence,
                box=s.box,
            )
            for s in stu_nodes
        )
        reference_results = tuple(
            ReferenceNodeResult(
                id=r.id,
                shape=r.shape,
                label=r.label,
                matched_student=to_student.get(r.id),
                label_match=(
                    None
                    if r.id not in to_student
                    else compare_labels(r.label, snapped[to_student[r.id]], close=p.close)
                ),
                shape_match=(
                    None
                    if r.id not in to_student
                    else shape_cost(r.shape, stu_by_id[to_student[r.id]].shape) == 0
                ),
            )
            for r in ref_nodes
        )
        labels = _student_labels(student, snapped, terms)
        anomalies = _anomalies(reference, student, to_ref, edges, extra_edges, labels)
        return DiagramComparison(
            similarity=round(similarity, 4),
            sub_scores=SubScores(
                nodes=round(node_score, 4), edges=round(edge_score, 4), labels=round(label_score, 4)
            ),
            edit_distance=round(ged, 4),
            nodes=node_results,
            reference_nodes=reference_results,
            edges=tuple(edges),
            missing_nodes=tuple(r.id for r in ref_nodes if r.id not in to_student),
            missing_edges=tuple(e.ref for e in edges if e.status is EdgeStatus.MISSING and e.ref),
            missing_labels=missing_labels,
            anomalies=anomalies,
            labels=labels,
            recognizer=student.recognizer,
            label_engines=student.label_engines,
            edited_by_teacher=student.edited_by_teacher,
            min_confidence=student.min_confidence,
            comparator=self.ref,
        )


def _degrees(graph: DiagramGraph) -> dict[str, int]:
    degree = {n.id: 0 for n in graph.nodes}
    for e in graph.edges:
        for end in (e.source, e.target):
            if end is not None:
                degree[end] += 1
    return degree


def _match_edges(
    reference: DiagramGraph,
    student: DiagramGraph,
    to_student: dict[str, str],
    close: float,
) -> tuple[list[EdgeResult], set[str]]:
    """Each reference edge in order: the same edge first, else the reversed one, else missing.
    Every student edge is used at most once."""
    used: set[str] = set()
    results: list[EdgeResult] = []
    candidates = [e for e in student.edges if not e.dangling]

    def find(source: str, target: str, *, either_way: bool) -> DiagramEdge | None:
        for e in candidates:
            if e.id in used:
                continue
            if (e.source, e.target) == (source, target):
                return e
            if either_way and (e.source, e.target) == (target, source):
                return e
        return None

    for r in reference.edges:
        s_src = None if r.source is None else to_student.get(r.source)
        s_tgt = None if r.target is None else to_student.get(r.target)
        found: DiagramEdge | None = None
        status = EdgeStatus.MISSING
        if s_src is not None and s_tgt is not None:
            found = find(s_src, s_tgt, either_way=False)
            if found is not None and r.directed and not found.directed:
                status = EdgeStatus.PRESENT  # a line where an arrow was asked: still there
            elif found is not None:
                status = EdgeStatus.PRESENT
            else:
                found = find(s_tgt, s_src, either_way=False)
                if found is not None:
                    both = r.directed and found.directed
                    status = EdgeStatus.REVERSED if both else EdgeStatus.PRESENT
        if found is not None:
            used.add(found.id)
        label_match: LabelMatch | None = None
        if found is not None and (r.label or found.label):
            raw = normalise_label(found.label)
            snapped = SnappedLabel(raw=found.label, normal=raw, term=None, match=LabelMatch.NONE)
            label_match = compare_labels(r.label, snapped, close=close)
        results.append(
            EdgeResult(
                status=status,
                ref=(r.source, r.target),
                student=None if found is None else (found.source, found.target),
                ref_edge=r.id,
                student_edge=None if found is None else found.id,
                label=r.label,
                label_match=label_match,
                directed=r.directed,
                confidence=None if found is None else found.confidence,
                box=None if found is None else found.box,
            )
        )
    return results, used


def _label_scores(
    reference: DiagramGraph,
    student: DiagramGraph,
    to_student: dict[str, str],
    snapped: dict[str, SnappedLabel],
    edges: Sequence[EdgeResult],
    close: float,
) -> tuple[float, tuple[str, ...]]:
    """Mean similarity of every reference label to the label in its matched place, and the
    reference labels the student wrote nowhere (R6 c)."""
    student_edges = {e.id: e for e in student.edges}
    sims: list[float] = []
    for r in reference.nodes:
        if not r.label:
            continue
        s_id = to_student.get(r.id)
        sims.append(0.0 if s_id is None else label_similarity(r.label, snapped[s_id]))
    for e in edges:
        if e.ref_edge is None or not e.label:
            continue
        if e.student_edge is None:
            sims.append(0.0)
            continue
        written = student_edges[e.student_edge].label
        sims.append(1.0 - label_distance(normalise_label(e.label), normalise_label(written)))
    score = 1.0 if not sims else sum(sims) / len(sims)

    written_all = [
        normalise_label(t)
        for t in (
            *(n.label for n in student.nodes),
            *(e.label for e in student.edges),
            *(f.text for f in student.free_labels),
        )
        if t.strip()
    ]
    written_all += [s.normal for s in snapped.values() if s.normal]
    missing: list[str] = []
    seen: set[str] = set()
    for label in reference.labels:
        normal = normalise_label(label)
        if not normal or normal in seen:
            continue
        seen.add(normal)
        if not any(label_distance(normal, w) <= close for w in written_all):
            missing.append(label)
    return score, tuple(missing)


def _student_labels(
    student: DiagramGraph, snapped: dict[str, SnappedLabel], terms: Glossary
) -> tuple[LabelResult, ...]:
    out: list[LabelResult] = [
        LabelResult(
            element="node",
            id=n.id,
            label=n.label,
            glossary_term=snapped[n.id].term,
            match=snapped[n.id].match,
            confidence=n.label_confidence,
        )
        for n in student.nodes
        if n.label.strip()
    ]
    for e in student.edges:
        if e.label.strip():
            s = terms.snap(e.label)
            out.append(
                LabelResult(
                    element="edge", id=e.id, label=e.label, glossary_term=s.term, match=s.match
                )
            )
    for f in student.free_labels:
        s = terms.snap(f.text)
        out.append(
            LabelResult(
                element="free",
                id=None,
                label=f.text,
                glossary_term=s.term,
                match=s.match,
                confidence=f.confidence,
            )
        )
    return tuple(out)


def _anomalies(
    reference: DiagramGraph,
    student: DiagramGraph,
    to_ref: dict[str, str],
    edges: Sequence[EdgeResult],
    extra_edges: Sequence[DiagramEdge],
    labels: Sequence[LabelResult],
) -> tuple[Anomaly, ...]:
    """Student elements with no reference match, and wrong structure (C29)."""
    out: list[Anomaly] = [
        Anomaly(type=AnomalyType.EXTRA_NODE, student=(n.id,), label=n.label)
        for n in student.nodes
        if n.id not in to_ref
    ]
    for e in extra_edges:
        if e.dangling:
            if e.source is None and e.target is None:
                end = "both"
            elif not e.directed:
                end = "end"
            else:
                end = "tail" if e.source is None else "head"
            out.append(
                Anomaly(type=AnomalyType.DANGLING_ARROW, student=(e.id,), label=e.label, end=end)
            )
        else:
            ends = tuple(x for x in (e.source, e.target) if x is not None)
            out.append(Anomaly(type=AnomalyType.EXTRA_EDGE, student=ends, edge=e.id, label=e.label))
    for r in edges:
        if r.status is EdgeStatus.REVERSED and r.student is not None and r.ref is not None:
            out.append(
                Anomaly(
                    type=AnomalyType.REVERSED_EDGE,
                    student=tuple(x for x in r.student if x is not None),
                    edge=r.student_edge,
                    ref=tuple(x for x in r.ref if x is not None),
                )
            )
    out.extend(
        Anomaly(type=AnomalyType.EXTRA_LABEL, student=(), label=label.label)
        for label in labels
        if label.element == "free" and label.match is LabelMatch.NONE
    )
    ref_parts = _components(reference)
    parts = _components(student)
    if len(parts) > max(1, len(ref_parts)):
        main = max(parts, key=lambda c: (sum(1 for x in c if x in to_ref), len(c)))
        out.extend(
            Anomaly(type=AnomalyType.DISCONNECTED, student=tuple(c)) for c in parts if c is not main
        )
    return tuple(out)


def _components(graph: DiagramGraph) -> list[list[str]]:
    """Connected parts (direction ignored), each in graph order; ordered by first node."""
    parent = {n.id: n.id for n in graph.nodes}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in graph.edges:
        if e.source is not None and e.target is not None:
            a, b = root(e.source), root(e.target)
            if a != b:
                parent[b] = a
    groups: dict[str, list[str]] = {}
    for n in graph.nodes:
        groups.setdefault(root(n.id), []).append(n.id)
    return list(groups.values())
