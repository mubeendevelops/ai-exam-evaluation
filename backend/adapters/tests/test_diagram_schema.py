"""Contract: the published ``docs/api/diagram-comparison.schema.json`` and
``diagram-graph.schema.json`` (R6, R3) accept what the core writes and refuse what it never
writes."""

import copy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.diagram import (
    DiagramEdge,
    DiagramGraph,
    DiagramKind,
    DiagramNode,
    FreeLabel,
    NodeShape,
    Point,
)
from tarn_core.services.diagrams.compare import GraphComparator
from tarn_core.services.diagrams.graph_json import graph_from_json, graph_to_json
from tarn_core.services.diagrams.result import comparison_document

DOCS = Path(__file__).resolve().parents[3] / "docs" / "api"


def validator(name: str) -> Draft202012Validator:
    schema = json.loads((DOCS / name).read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


COMPARISON = validator("diagram-comparison.schema.json")
GRAPH = validator("diagram-graph.schema.json")
REC = EngineRef(name="shape-detector", version="1")


def node(i: str, shape: NodeShape, label: str, x: int = 10) -> DiagramNode:
    return DiagramNode(
        id=i,
        shape=shape,
        label=label,
        box=Box(x0=x, y0=10, x1=x + 50, y1=40),
        confidence=0.8,
        label_confidence=0.7 if label else None,
    )


REFERENCE = DiagramGraph(
    nodes=(
        node("r1", NodeShape.TERMINAL, "start"),
        node("r2", NodeShape.DECISION, "is n > 0?"),
        node("r3", NodeShape.IO, "print n"),
        node("r4", NodeShape.TERMINAL, "stop"),
    ),
    edges=(
        DiagramEdge(id="a", source="r1", target="r2"),
        DiagramEdge(id="b", source="r2", target="r3", label="yes"),
        DiagramEdge(id="c", source="r3", target="r4"),
        DiagramEdge(id="d", source="r2", target="r4", label="no"),
    ),
)

STUDENT = DiagramGraph(
    nodes=(
        node("s1", NodeShape.TERMINAL, "strat"),
        node("s2", NodeShape.DECISION, "is n>0"),
        node("s3", NodeShape.PROCESS, "print n"),
        node("s9", NodeShape.PROCESS, "print x"),
    ),
    edges=(
        DiagramEdge(
            id="e1",
            source="s1",
            target="s2",
            box=Box(x0=1, y0=1, x1=5, y1=9),
            tail=Point(x=2, y=2),
            head=Point(x=3, y=8),
            confidence=0.6,
        ),
        DiagramEdge(id="e2", source="s3", target="s2", label="yes"),
        DiagramEdge(id="e3", source="s3", target=None),
        DiagramEdge(id="e4", source=None, target=None, directed=False),
    ),
    recognizer=REC,
    label_engines=("trocr", "paddle"),
    free_labels=(FreeLabel(text="my note", confidence=0.5),),
)


def document(reference: DiagramGraph, student: DiagramGraph, **kw: Any) -> dict[str, Any]:
    c = GraphComparator().compare(reference, student, ["start", "stop"])
    args: dict[str, Any] = {
        "question_code": "SYN-Q2a",
        "slot_label": "2.a",
        "kind": DiagramKind.FLOWCHART,
        "reference_diagram_id": str(uuid4()),
        "reference_version": 3,
        "student_diagram_id": str(uuid4()),
        "student_version": 1,
        **kw,
    }
    return comparison_document(c, **args)


@pytest.mark.parametrize(
    ("reference", "student"),
    [
        (REFERENCE, STUDENT),
        (REFERENCE, REFERENCE),
        (REFERENCE, DiagramGraph(nodes=())),
        (DiagramGraph(nodes=()), STUDENT),
    ],
)
def test_the_core_writes_what_the_schema_describes(
    reference: DiagramGraph, student: DiagramGraph
) -> None:
    doc = document(reference, student)
    errors = [e.message for e in COMPARISON.iter_errors(doc)]
    assert errors == []


def test_the_document_covers_every_r6_output() -> None:
    doc = document(REFERENCE, STUDENT)
    assert 0 < doc["similarity"] < 1
    assert set(doc["sub_scores"]) == {"nodes", "edges", "labels"}
    assert {x["match"] for x in doc["labels"]} >= {"close", "none"}  # (b)
    assert doc["missing"]["nodes"] == ["r4"]  # (c)
    types = {a["type"] for a in doc["anomalies"]}  # (d)
    assert {"extra_node", "dangling_arrow", "reversed_edge", "extra_label"} <= types
    assert doc["reference_nodes"][2]["shape_match"] is False  # (e) io drawn as a process
    assert [e["status"] for e in doc["edges"]][:4] == ["present", "reversed", "missing", "missing"]
    assert doc["engines"] == {
        "recognizer": "shape-detector-v1",
        "label_ocr": ["trocr", "paddle"],
        "comparator": "graph-compare-v1",
    }  # (g)
    extra = next(a for a in doc["anomalies"] if a["type"] == "extra_node")
    assert extra == {"type": "extra_node", "student": "s9", "label": "print x"}


def test_documents_without_a_student_diagram_id_are_valid() -> None:
    doc = document(REFERENCE, STUDENT, student_diagram_id=None, student_version=None)
    assert COMPARISON.is_valid(doc)


@pytest.mark.parametrize(
    "break_it",
    [
        lambda d: d.pop("similarity"),
        lambda d: d.update(similarity=1.5),
        lambda d: d.update(schema_version="2.0"),
        lambda d: d["edges"][0].update(status="sideways"),
        lambda d: d["anomalies"].append({"type": "extra_node", "student": ["s1"]}),
        lambda d: d["anomalies"].append({"type": "dangling_arrow", "student": "e3"}),
        lambda d: d["nodes"][0].update(label_match="maybe"),
        lambda d: d.update(surprise=True),
        lambda d: d["missing"]["edges"].append(["r1"]),
    ],
)
def test_the_schema_refuses_what_the_core_never_writes(break_it: Any) -> None:
    doc = copy.deepcopy(document(REFERENCE, STUDENT))
    break_it(doc)
    assert not COMPARISON.is_valid(doc)


def test_graph_json_round_trip_and_schema() -> None:
    for graph in (REFERENCE, STUDENT, DiagramGraph(nodes=())):
        doc = graph_to_json(graph)
        assert [e.message for e in GRAPH.iter_errors(doc)] == []
        assert graph_from_json(json.loads(json.dumps(doc))) == graph


def test_graph_json_reads_rows_written_before_p14() -> None:
    old = {
        "nodes": [{"id": "n", "shape": "block", "label": "A", "box": None, "confidence": 1.0}],
        "edges": [],
        "recognizer": None,
        "edited_by_teacher": False,
    }
    graph = graph_from_json(old)
    assert graph.nodes[0].label == "A" and graph.free_labels == ()
    assert GRAPH.is_valid(graph_to_json(graph))


def test_the_graph_schema_refuses_bad_graphs() -> None:
    doc: dict[str, Any] = graph_to_json(STUDENT)
    for bad in (
        {**doc, "nodes": [{**doc["nodes"][0], "shape": "hexagon"}]},
        {**doc, "edges": [{**doc["edges"][0], "box": [1, 2, 3]}]},
        {**doc, "extra": 1},
    ):
        assert not GRAPH.is_valid(bad)


def test_the_published_example_is_valid() -> None:
    example = json.loads((DOCS / "diagram-comparison.example.json").read_text())
    assert [e.message for e in COMPARISON.iter_errors(example)] == []
