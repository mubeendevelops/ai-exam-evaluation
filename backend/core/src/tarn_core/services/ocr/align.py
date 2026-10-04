"""Match each engine's readings onto the detected lines by box overlap (design.md "Alignment").

Local engines read our line crops, so their readings sit exactly on the lines. Cloud engines
read the whole page and return their own lines or words; for those:

* a reading whose box has IoU ≥ ``iou`` with a detected line is that line;
* otherwise a reading lying mostly inside one line (intersection over its own area ≥
  ``inside``: a word, or a piece of a line the detector drew longer) joins that line;
* several readings of one engine on one line are joined left to right, their confidence
  weighted by text length;
* readings that match no detected line become extra lines (clustered the same way across
  engines), read only by the engines that found them."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box

DEFAULT_IOU = 0.5
DEFAULT_INSIDE = 0.6


def intersection(a: Box, b: Box) -> int:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    return w * h if w > 0 and h > 0 else 0


def iou(a: Box, b: Box) -> float:
    inter = intersection(a, b)
    return inter / (a.area + b.area - inter) if inter else 0.0


def union_box(a: Box, b: Box) -> Box:
    return Box(x0=min(a.x0, b.x0), y0=min(a.y0, b.y0), x1=max(a.x1, b.x1), y1=max(a.y1, b.y1))


@dataclass
class AlignedLine:
    """One line of the page with at most one reading per engine."""

    box: Box
    detected: bool
    """False for an extra line that only an engine's own layout found."""
    readings: dict[str, LineReading] = field(default_factory=dict)


def _match(box: Box, lines: Sequence[Box], iou_min: float, inside_min: float) -> int | None:
    best, best_iou = None, 0.0
    for i, line in enumerate(lines):
        value = iou(box, line)
        if value > best_iou:
            best, best_iou = i, value
    if best is not None and best_iou >= iou_min:
        return best
    best, best_inside = None, 0.0
    for i, line in enumerate(lines):
        value = intersection(box, line) / box.area
        if value > best_inside:
            best, best_inside = i, value
    return best if best is not None and best_inside >= inside_min else None


def merge(readings: Sequence[LineReading], box: Box) -> LineReading:
    """Join one engine's pieces of one line, left to right, as a reading of ``box``."""
    if len(readings) == 1:
        only = readings[0]
        return LineReading(
            engine=only.engine,
            text=only.text,
            box=box,
            confidence=only.confidence,
            char_confidences=only.char_confidences,
        )
    pieces = sorted(readings, key=lambda r: (r.box.x0, r.box.y0))
    text = " ".join(p.text.strip() for p in pieces if p.text.strip())
    total = sum(max(len(p.text.strip()), 1) for p in pieces)
    confidence = sum(p.confidence * max(len(p.text.strip()), 1) for p in pieces) / total
    return LineReading(
        engine=pieces[0].engine, text=text, box=box, confidence=min(1.0, max(0.0, confidence))
    )


def align(
    lines: Sequence[Box],
    readings: Mapping[str, Sequence[LineReading]],
    *,
    iou_min: float = DEFAULT_IOU,
    inside_min: float = DEFAULT_INSIDE,
) -> list[AlignedLine]:
    """The detected lines (in their order) with each engine's reading, then extra lines in
    top-to-bottom order. ``readings`` maps engine name → that engine's readings of the page."""
    assigned: list[dict[str, list[LineReading]]] = [{} for _ in lines]
    extras: list[tuple[Box, dict[str, list[LineReading]]]] = []
    for engine, found in readings.items():
        for reading in found:
            index = _match(reading.box, lines, iou_min, inside_min)
            if index is not None:
                assigned[index].setdefault(engine, []).append(reading)
                continue
            boxes = [box for box, _ in extras]
            extra = _match(reading.box, boxes, iou_min, inside_min)
            if extra is None:
                # A box inside the reading (the reading is a whole line, the extra a word).
                extra = next(
                    (
                        i
                        for i, b in enumerate(boxes)
                        if intersection(b, reading.box) / b.area >= inside_min
                    ),
                    None,
                )
            if extra is None:
                extras.append((reading.box, {engine: [reading]}))
            else:
                box, members = extras[extra]
                members.setdefault(engine, []).append(reading)
                extras[extra] = (union_box(box, reading.box), members)
    result = [
        AlignedLine(
            box=line,
            detected=True,
            readings={e: merge(rs, line) for e, rs in assigned[i].items()},
        )
        for i, line in enumerate(lines)
    ]
    for box, members in sorted(extras, key=lambda item: (item[0].y0, item[0].x0)):
        result.append(
            AlignedLine(
                box=box, detected=False, readings={e: merge(rs, box) for e, rs in members.items()}
            )
        )
    return result
