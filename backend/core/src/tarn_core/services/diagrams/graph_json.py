"""The graph JSON document (``docs/api/diagram-graph.schema.json``, version 1.0): how a
reference or student graph is stored and shown by the API. Reading is lenient about keys added
after the first rows were written (they take their defaults); everything else must be right."""

from collections.abc import Mapping

from tarn_core.domain.common import Box, EngineRef, JsonValue
from tarn_core.domain.diagram import (
    DiagramEdge,
    DiagramGraph,
    DiagramNode,
    FreeLabel,
    NodeShape,
    Point,
)
from tarn_core.errors import InvariantError

GRAPH_SCHEMA_VERSION = "1.0"


def graph_to_json(graph: DiagramGraph) -> dict[str, JsonValue]:
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "nodes": [
            {
                "id": n.id,
                "shape": n.shape.value,
                "label": n.label,
                "box": _box(n.box),
                "confidence": n.confidence,
                "label_confidence": n.label_confidence,
            }
            for n in graph.nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source,
                "target": e.target,
                "label": e.label,
                "directed": e.directed,
                "confidence": e.confidence,
                "box": _box(e.box),
                "tail": _point(e.tail),
                "head": _point(e.head),
            }
            for e in graph.edges
        ],
        "free_labels": [
            {"text": f.text, "box": _box(f.box), "confidence": f.confidence}
            for f in graph.free_labels
        ],
        "recognizer": None
        if graph.recognizer is None
        else {"name": graph.recognizer.name, "version": graph.recognizer.version},
        "label_engines": list(graph.label_engines),
        "edited_by_teacher": graph.edited_by_teacher,
    }


def graph_from_json(value: object) -> DiagramGraph:
    o = _obj(value)
    recognizer = o.get("recognizer")
    return DiagramGraph(
        nodes=tuple(
            DiagramNode(
                id=_str(n["id"]),
                shape=NodeShape(_str(n["shape"])),
                label=_str(n.get("label", "")),
                box=_box_from(n.get("box")),
                confidence=_float(n.get("confidence", 1.0)),
                label_confidence=_opt_float(n.get("label_confidence")),
            )
            for n in map(_obj, _list(o.get("nodes", [])))
        ),
        edges=tuple(
            DiagramEdge(
                id=_str(e["id"]),
                source=_opt_str(e.get("source")),
                target=_opt_str(e.get("target")),
                label=_str(e.get("label", "")),
                directed=_bool(e.get("directed", True)),
                confidence=_float(e.get("confidence", 1.0)),
                box=_box_from(e.get("box")),
                tail=_point_from(e.get("tail")),
                head=_point_from(e.get("head")),
            )
            for e in map(_obj, _list(o.get("edges", [])))
        ),
        free_labels=tuple(
            FreeLabel(
                text=_str(f["text"]),
                box=_box_from(f.get("box")),
                confidence=_opt_float(f.get("confidence")),
            )
            for f in map(_obj, _list(o.get("free_labels", [])))
        ),
        recognizer=None
        if recognizer is None
        else EngineRef(
            name=_str(_obj(recognizer)["name"]), version=_str(_obj(recognizer)["version"])
        ),
        label_engines=tuple(_str(x) for x in _list(o.get("label_engines", []))),
        edited_by_teacher=_bool(o.get("edited_by_teacher", False)),
    )


def _box(box: Box | None) -> JsonValue:
    return None if box is None else [box.x0, box.y0, box.x1, box.y1]


def _point(point: Point | None) -> JsonValue:
    return None if point is None else [point.x, point.y]


def _box_from(value: object) -> Box | None:
    if value is None:
        return None
    items = [_int(v) for v in _list(value)]
    if len(items) != 4:
        raise InvariantError("a box has four numbers")
    return Box(x0=items[0], y0=items[1], x1=items[2], y1=items[3])


def _point_from(value: object) -> Point | None:
    if value is None:
        return None
    items = [_int(v) for v in _list(value)]
    if len(items) != 2:
        raise InvariantError("a point has two numbers")
    return Point(x=items[0], y=items[1])


def _obj(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise InvariantError("expected an object in the graph")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise InvariantError("expected a list in the graph")
    return value


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise InvariantError("expected text in the graph")
    return value


def _opt_str(value: object) -> str | None:
    return None if value is None else _str(value)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise InvariantError("expected true or false in the graph")
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvariantError("expected a whole number in the graph")
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvariantError("expected a number in the graph")
    return float(value)


def _opt_float(value: object) -> float | None:
    return None if value is None else _float(value)
