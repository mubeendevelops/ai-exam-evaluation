"""JSON forms of structured domain values stored in ``jsonb`` columns.

Decimals are strings so they round-trip exactly. These are storage forms: the public,
versioned blueprint JSON Schema is defined in P6 and may differ."""

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast
from uuid import UUID

from tarn_core.domain.blueprint import (
    AllOf,
    AnyN,
    BlueprintItem,
    ChoiceRule,
    EvaluationMethod,
    OrGroup,
    QuestionSlot,
    Section,
    Step,
    SubPart,
)
from tarn_core.domain.booklet import PageMetrics, SegmentSpan, SourceFile
from tarn_core.domain.common import BlobKey, Box, ContentKind, ContentRef, EngineRef, JsonValue
from tarn_core.domain.content import (
    CriterionParams,
    CriterionType,
    DiagramComponent,
    DiagramParams,
    ListItem,
    ListParams,
    LlmParams,
    NumericParams,
    SemanticParams,
)
from tarn_core.domain.diagram import DiagramEdge, DiagramGraph, DiagramNode, NodeShape
from tarn_core.domain.review import ResultLine
from tarn_core.domain.scoring import CriterionReason
from tarn_core.ids import PageId, QuestionId, ReferenceDiagramId

type Json = JsonValue


class CodecError(ValueError):
    """A stored JSON value does not have the expected shape."""


# --- reading helpers --------------------------------------------------------------------------


def _obj(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise CodecError(f"expected a JSON object, got {type(value).__name__}")
    return cast(Mapping[str, object], value)


def _list(value: object) -> Sequence[object]:
    if not isinstance(value, list):
        raise CodecError(f"expected a JSON array, got {type(value).__name__}")
    return cast(Sequence[object], value)


def _str(value: object) -> str:
    if not isinstance(value, str):
        raise CodecError(f"expected a string, got {type(value).__name__}")
    return value


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CodecError(f"expected an integer, got {type(value).__name__}")
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CodecError(f"expected a number, got {type(value).__name__}")
    return float(value)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise CodecError(f"expected a boolean, got {type(value).__name__}")
    return value


def _dec(value: object) -> Decimal:
    return Decimal(_str(value))


def _uuid(value: object) -> UUID:
    return UUID(_str(value))


# --- small values -----------------------------------------------------------------------------


def box_to_list(box: Box) -> list[int]:
    return [box.x0, box.y0, box.x1, box.y1]


def box_from_list(value: Sequence[object]) -> Box:
    x0, y0, x1, y1 = (_int(v) for v in value)
    return Box(x0=x0, y0=y0, x1=x1, y1=y1)


def ref_to_json(ref: ContentRef) -> Json:
    return {"kind": ref.kind.value, "id": str(ref.id), "version": ref.version}


def ref_from_json(value: object) -> ContentRef:
    o = _obj(value)
    return ContentRef(
        kind=ContentKind(_str(o["kind"])), id=_uuid(o["id"]), version=_int(o["version"])
    )


def refs_to_json(refs: frozenset[ContentRef]) -> Json:
    ordered = sorted(refs, key=lambda r: (r.kind.value, str(r.id), r.version))
    return [ref_to_json(r) for r in ordered]


def refs_from_json(value: object) -> frozenset[ContentRef]:
    return frozenset(ref_from_json(v) for v in _list(value))


def _engine_to_json(engine: EngineRef) -> Json:
    return {"name": engine.name, "version": engine.version}


def _engine_from_json(value: object) -> EngineRef:
    o = _obj(value)
    return EngineRef(name=_str(o["name"]), version=_str(o["version"]))


# --- rubric criterion params -----------------------------------------------------------------


def reason_to_json(reason: CriterionReason) -> Json:
    return {
        "summary": reason.summary,
        "matched": list(reason.matched),
        "missing": list(reason.missing),
        "sentences": list(reason.sentences),
        "found": reason.found,
        "expected": reason.expected,
    }


def reason_from_json(value: object) -> CriterionReason:
    d = _obj(value)
    return CriterionReason(
        summary=_str(d["summary"]),
        matched=tuple(_str(x) for x in _list(d.get("matched", []))),
        missing=tuple(_str(x) for x in _list(d.get("missing", []))),
        sentences=tuple(_int(x) for x in _list(d.get("sentences", []))),
        found=None if d.get("found") is None else _str(d["found"]),
        expected=None if d.get("expected") is None else _str(d["expected"]),
    )


def params_to_json(params: CriterionParams) -> Json:
    match params:
        case ListParams():
            return {
                "items": [{"term": i.term, "synonyms": list(i.synonyms)} for i in params.items],
                "required_count": params.required_count,
            }
        case NumericParams():
            return {
                "expected": str(params.expected),
                "tolerance": str(params.tolerance),
                "unit": params.unit,
            }
        case SemanticParams():
            return {"reference_statement": params.reference_statement}
        case DiagramParams():
            return {
                "reference_diagram_id": str(params.reference_diagram_id),
                "component": params.component.value,
            }
        case LlmParams():
            return {"instructions": params.instructions}


def params_from_json(kind: CriterionType, value: object) -> CriterionParams:
    o = _obj(value)
    match kind:
        case CriterionType.LIST:
            items = tuple(
                ListItem(
                    term=_str(_obj(i)["term"]),
                    synonyms=tuple(_str(s) for s in _list(_obj(i)["synonyms"])),
                )
                for i in _list(o["items"])
            )
            return ListParams(items=items, required_count=_int(o["required_count"]))
        case CriterionType.NUMERIC:
            return NumericParams(
                expected=_dec(o["expected"]), tolerance=_dec(o["tolerance"]), unit=_str(o["unit"])
            )
        case CriterionType.SEMANTIC:
            return SemanticParams(reference_statement=_str(o["reference_statement"]))
        case CriterionType.DIAGRAM:
            return DiagramParams(
                reference_diagram_id=ReferenceDiagramId(_uuid(o["reference_diagram_id"])),
                component=DiagramComponent(_str(o["component"])),
            )
        case CriterionType.LLM:
            return LlmParams(instructions=_str(o["instructions"]))


# --- diagram graphs ---------------------------------------------------------------------------


def graph_to_json(graph: DiagramGraph) -> Json:
    return {
        "nodes": [
            {
                "id": n.id,
                "shape": n.shape.value,
                "label": n.label,
                "box": None if n.box is None else list[Json](box_to_list(n.box)),
                "confidence": n.confidence,
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
            }
            for e in graph.edges
        ],
        "recognizer": None if graph.recognizer is None else _engine_to_json(graph.recognizer),
        "edited_by_teacher": graph.edited_by_teacher,
    }


def graph_from_json(value: object) -> DiagramGraph:
    o = _obj(value)
    nodes = tuple(
        DiagramNode(
            id=_str(n["id"]),
            shape=NodeShape(_str(n["shape"])),
            label=_str(n["label"]),
            box=None if n["box"] is None else box_from_list(_list(n["box"])),
            confidence=_float(n["confidence"]),
        )
        for n in map(_obj, _list(o["nodes"]))
    )
    edges = tuple(
        DiagramEdge(
            id=_str(e["id"]),
            source=_str(e["source"]),
            target=_str(e["target"]),
            label=_str(e["label"]),
            directed=_bool(e["directed"]),
            confidence=_float(e["confidence"]),
        )
        for e in map(_obj, _list(o["edges"]))
    )
    recognizer = None if o["recognizer"] is None else _engine_from_json(o["recognizer"])
    return DiagramGraph(
        nodes=nodes,
        edges=edges,
        recognizer=recognizer,
        edited_by_teacher=_bool(o["edited_by_teacher"]),
    )


# --- blueprint sections -----------------------------------------------------------------------


def _steps_to_json(steps: tuple[Step, ...]) -> Json:
    return [{"label": s.label, "marks": str(s.marks)} for s in steps]


def _steps_from_json(value: object) -> tuple[Step, ...]:
    return tuple(
        Step(label=_str(s["label"]), marks=_dec(s["marks"])) for s in map(_obj, _list(value))
    )


def _slot_to_json(slot: QuestionSlot) -> Json:
    return {
        "label": slot.label,
        "marks": str(slot.marks),
        "question_id": None if slot.question_id is None else str(slot.question_id),
        "parts": [
            {
                "label": p.label,
                "marks": str(p.marks),
                "question_id": None if p.question_id is None else str(p.question_id),
                "steps": _steps_to_json(p.steps),
            }
            for p in slot.parts
        ],
        "steps": _steps_to_json(slot.steps),
        "negative_marks": str(slot.negative_marks),
    }


def _slot_from_json(value: object) -> QuestionSlot:
    o = _obj(value)
    parts = tuple(
        SubPart(
            label=_str(p["label"]),
            marks=_dec(p["marks"]),
            question_id=None if p["question_id"] is None else QuestionId(_uuid(p["question_id"])),
            steps=_steps_from_json(p["steps"]),
        )
        for p in map(_obj, _list(o["parts"]))
    )
    return QuestionSlot(
        label=_str(o["label"]),
        marks=_dec(o["marks"]),
        question_id=None if o["question_id"] is None else QuestionId(_uuid(o["question_id"])),
        parts=parts,
        steps=_steps_from_json(o["steps"]),
        negative_marks=_dec(o["negative_marks"]),
    )


def _item_to_json(item: BlueprintItem) -> Json:
    if isinstance(item, OrGroup):
        return {"or": [_slot_to_json(a) for a in item.alternatives]}
    return {"slot": _slot_to_json(item)}


def _item_from_json(value: object) -> BlueprintItem:
    o = _obj(value)
    if "or" in o:
        return OrGroup(alternatives=tuple(_slot_from_json(a) for a in _list(o["or"])))
    return _slot_from_json(o["slot"])


def _rule_to_json(rule: ChoiceRule) -> Json:
    return {"any": rule.n} if isinstance(rule, AnyN) else {"all": True}


def _rule_from_json(value: object) -> ChoiceRule:
    o = _obj(value)
    return AnyN(_int(o["any"])) if "any" in o else AllOf()


def sections_to_json(sections: tuple[Section, ...]) -> Json:
    return [
        {
            "label": s.label,
            "title": s.title,
            "method": s.method.value,
            "rule": _rule_to_json(s.rule),
            "items": [_item_to_json(i) for i in s.items],
        }
        for s in sections
    ]


def sections_from_json(value: object) -> tuple[Section, ...]:
    return tuple(
        Section(
            label=_str(s["label"]),
            title=_str(s["title"]),
            method=EvaluationMethod(_str(s["method"])),
            rule=_rule_from_json(s["rule"]),
            items=tuple(_item_from_json(i) for i in _list(s["items"])),
        )
        for s in map(_obj, _list(value))
    )


# --- segments and result sheets ---------------------------------------------------------------


def spans_to_json(spans: tuple[SegmentSpan, ...]) -> Json:
    return [{"page_id": str(s.page_id), "box": list[Json](box_to_list(s.box))} for s in spans]


def spans_from_json(value: object) -> tuple[SegmentSpan, ...]:
    return tuple(
        SegmentSpan(page_id=PageId(_uuid(s["page_id"])), box=box_from_list(_list(s["box"])))
        for s in map(_obj, _list(value))
    )


def lines_to_json(lines: tuple[ResultLine, ...]) -> Json:
    return [
        {
            "section_label": ln.section_label,
            "slot_label": ln.slot_label,
            "mark": None if ln.mark is None else str(ln.mark),
            "counted": ln.counted,
            "reason": ln.reason,
        }
        for ln in lines
    ]


def lines_from_json(value: object) -> tuple[ResultLine, ...]:
    return tuple(
        ResultLine(
            section_label=_str(o["section_label"]),
            slot_label=_str(o["slot_label"]),
            mark=None if o["mark"] is None else _dec(o["mark"]),
            counted=_bool(o["counted"]),
            reason=_str(o["reason"]),
        )
        for o in map(_obj, _list(value))
    )


def sources_to_json(sources: tuple[SourceFile, ...]) -> Json:
    return [
        {"key": s.key.value, "media_type": s.media_type, "size_bytes": s.size_bytes}
        for s in sources
    ]


def sources_from_json(value: object) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(
            key=BlobKey(_str(_obj(item)["key"])),
            media_type=_str(_obj(item)["media_type"]),
            size_bytes=_int(_obj(item)["size_bytes"]),
        )
        for item in _list(value)
    )


def metrics_to_json(m: PageMetrics) -> Json:
    return {
        "sharpness": m.sharpness,
        "glare_share": m.glare_share,
        "page_found": m.page_found,
        "page_area_share": m.page_area_share,
        "source_width": m.source_width,
        "source_height": m.source_height,
        "rotation_degrees": m.rotation_degrees,
        "rotation_guessed": m.rotation_guessed,
        "skew_degrees": m.skew_degrees,
        "cropped": m.cropped,
        "perspective_corrected": m.perspective_corrected,
        "neighbour_removed": m.neighbour_removed,
    }


def metrics_from_json(value: object) -> PageMetrics:
    o = _obj(value)
    sharpness = o["sharpness"]
    return PageMetrics(
        sharpness=None if sharpness is None else _float(sharpness),
        glare_share=_float(o["glare_share"]),
        page_found=_bool(o["page_found"]),
        page_area_share=_float(o["page_area_share"]),
        source_width=_int(o["source_width"]),
        source_height=_int(o["source_height"]),
        rotation_degrees=_int(o["rotation_degrees"]),
        rotation_guessed=_bool(o["rotation_guessed"]),
        skew_degrees=_float(o["skew_degrees"]),
        cropped=_bool(o["cropped"]),
        perspective_corrected=_bool(o["perspective_corrected"]),
        neighbour_removed=_bool(o["neighbour_removed"]),
    )
