"""The R6 result document (design.md "Diagram comparison (R6)"), published as
``docs/api/diagram-comparison.schema.json``. Keys of the design's example keep their meaning;
the rest make the seven outputs explicit:

- (a) ``similarity``, ``sub_scores`` (+ ``edit_distance``);
- (b) ``labels``: every label the student wrote with its glossary match;
- (c) ``missing``: reference nodes, edges and labels with no student match;
- (d) ``anomalies``: unmatched student elements, reversed arrows, disconnected parts,
  dangling arrows;
- (e) ``nodes`` (per student node) and ``reference_nodes`` (per reference node);
- (f) ``edges``: per reference edge present / missing / reversed, then extra student edges;
- (g) ``engines``, confidences and boxes on nodes and edges, ``edited_by_teacher``."""

from tarn_core.domain.common import Box, EngineRef, JsonValue
from tarn_core.domain.diagram import DiagramKind
from tarn_core.services.diagrams.compare import (
    Anomaly,
    AnomalyType,
    DiagramComparison,
    EdgeResult,
    LabelResult,
    NodeResult,
    ReferenceNodeResult,
)

SCHEMA_VERSION = "1.0"


def engine_name(ref: EngineRef | None) -> str | None:
    """``shape-detector`` version ``1`` → ``shape-detector-v1``."""
    return None if ref is None else f"{ref.name}-v{ref.version}"


def comparison_document(
    comparison: DiagramComparison,
    *,
    question_code: str,
    slot_label: str | None,
    kind: DiagramKind,
    reference_diagram_id: str,
    reference_version: int,
    student_diagram_id: str | None,
    student_version: int | None,
) -> dict[str, JsonValue]:
    c = comparison
    return {
        "schema_version": SCHEMA_VERSION,
        "question_id": question_code,
        "slot_label": slot_label,
        "diagram_kind": kind.value,
        "reference_diagram_id": reference_diagram_id,
        "reference_graph_version": reference_version,
        "student_diagram_id": student_diagram_id,
        "student_graph_version": student_version,
        "similarity": c.similarity,
        "edit_distance": c.edit_distance,
        "sub_scores": {
            "nodes": c.sub_scores.nodes,
            "edges": c.sub_scores.edges,
            "labels": c.sub_scores.labels,
        },
        "nodes": [_node(n) for n in c.nodes],
        "reference_nodes": [_ref_node(r) for r in c.reference_nodes],
        "edges": [_edge(e) for e in c.edges],
        "labels": [_label(x) for x in c.labels],
        "missing": {
            "nodes": list(c.missing_nodes),
            "edges": [list(e) for e in c.missing_edges],
            "labels": list(c.missing_labels),
        },
        "anomalies": [_anomaly(a) for a in c.anomalies],
        "engines": {
            "recognizer": engine_name(c.recognizer),
            "label_ocr": list(c.label_engines),
            "comparator": engine_name(c.comparator),
        },
        "min_confidence": round(c.min_confidence, 4),
        "edited_by_teacher": c.edited_by_teacher,
    }


def _box(box: Box | None) -> JsonValue:
    return None if box is None else [box.x0, box.y0, box.x1, box.y1]


def _node(n: NodeResult) -> dict[str, JsonValue]:
    return {
        "id": n.id,
        "shape": n.shape.value,
        "label": n.label,
        "matched_ref": n.matched_ref,
        "label_match": None if n.label_match is None else n.label_match.value,
        "glossary_term": n.glossary_term,
        "glossary_match": n.glossary_match.value,
        "shape_match": n.shape_match,
        "confidence": round(n.confidence, 4),
        "label_confidence": None if n.label_confidence is None else round(n.label_confidence, 4),
        "box": _box(n.box),
    }


def _ref_node(r: ReferenceNodeResult) -> dict[str, JsonValue]:
    return {
        "id": r.id,
        "shape": r.shape.value,
        "label": r.label,
        "matched_student": r.matched_student,
        "label_match": None if r.label_match is None else r.label_match.value,
        "shape_match": r.shape_match,
    }


def _edge(e: EdgeResult) -> dict[str, JsonValue]:
    return {
        "ref": None if e.ref is None else list(e.ref),
        "student": None if e.student is None else list(e.student),
        "status": e.status.value,
        "ref_edge": e.ref_edge,
        "student_edge": e.student_edge,
        "label": e.label,
        "label_match": None if e.label_match is None else e.label_match.value,
        "directed": e.directed,
        "confidence": None if e.confidence is None else round(e.confidence, 4),
        "box": _box(e.box),
    }


def _label(x: LabelResult) -> dict[str, JsonValue]:
    return {
        "element": x.element,
        "id": x.id,
        "label": x.label,
        "glossary_term": x.glossary_term,
        "match": x.match.value,
        "confidence": None if x.confidence is None else round(x.confidence, 4),
    }


def _anomaly(a: Anomaly) -> dict[str, JsonValue]:
    """As in the design's example: one student id for an extra node or a dangling arrow, the
    student's two ends for an edge, the list of nodes for a disconnected part."""
    student: JsonValue
    if a.type in (AnomalyType.EXTRA_NODE, AnomalyType.DANGLING_ARROW):
        student = a.student[0]
    elif a.type is AnomalyType.EXTRA_LABEL:
        student = None
    else:
        student = list(a.student)
    out: dict[str, JsonValue] = {"type": a.type.value, "student": student}
    if a.edge is not None:
        out["edge"] = a.edge
    if a.label:
        out["label"] = a.label
    if a.ref:
        out["ref"] = list(a.ref)
    if a.end is not None:
        out["end"] = a.end
    return out
