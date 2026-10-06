"""From detections and text to a graph (design.md "Diagram comparison", step 1).

- Shapes become nodes (``n1``, ``n2``… in reading order), below a confidence floor left out.
- Each arrow end attaches to the nearest shape whose box it touches or nearly touches
  (``policy.attach`` × the diagram's diagonal, at least ``attach_min_px``); an end near no
  shape stays None (a dangling arrow). When both ends land on one shape, the farther end tries
  the next shape.
- Each text line (read best-of-N by the OCR framework) inside a shape labels it; outside every
  shape it labels the nearest arrow (``yes``/``no`` beside a decision) or, failing that, the
  nearest shape (a name beside a circle in a network); else it is a free label."""

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.diagram import (
    DetectedArrow,
    DetectedShape,
    DiagramDetection,
    DiagramEdge,
    DiagramGraph,
    DiagramNode,
    DiagramText,
    FreeLabel,
    Point,
)
from tarn_core.services.diagrams.policy import DiagramPolicy


def point_box_distance(p: Point, box: Box) -> float:
    dx = max(box.x0 - p.x, 0, p.x - box.x1)
    dy = max(box.y0 - p.y, 0, p.y - box.y1)
    return math.hypot(dx, dy)


def box_distance(a: Box, b: Box) -> float:
    dx = max(b.x0 - a.x1, 0, a.x0 - b.x1)
    dy = max(b.y0 - a.y1, 0, a.y0 - b.y1)
    return math.hypot(dx, dy)


def centre(box: Box) -> Point:
    return Point(x=(box.x0 + box.x1) // 2, y=(box.y0 + box.y1) // 2)


def inside(p: Point, box: Box) -> bool:
    return box.x0 <= p.x <= box.x1 and box.y0 <= p.y <= box.y1


_STRAY = frozenset(".,;:()[]{}'\"`|_-~")


def _stray(word: str) -> bool:
    """A lone mark the OCR made of a speck or an outline ("(", ".", "|"); signs that carry
    meaning in a diagram (``> < = + ? !``) are kept."""
    return all(ch in _STRAY for ch in word)


@dataclass
class _Pieces:
    texts: list[DiagramText] = field(default_factory=list)

    def label(self) -> tuple[str, float | None]:
        ordered = sorted(self.texts, key=lambda t: (t.box.y0, t.box.x0))
        words = [w for t in ordered for w in t.text.split()]
        text = " ".join(w for w in words if not _stray(w)).strip()
        confidences = [t.confidence for t in ordered if t.confidence is not None]
        return text, (min(confidences) if confidences else None)


class GraphBuilder:
    def __init__(self, policy: DiagramPolicy | None = None) -> None:
        self.policy = policy or DiagramPolicy()

    def build(
        self,
        detection: DiagramDetection,
        texts: Sequence[DiagramText],
        *,
        recognizer: EngineRef,
        label_engines: Sequence[str] = (),
        region: Box | None = None,
    ) -> DiagramGraph:
        """``region``: the part of the image the diagram covers (the whole image if None);
        its diagonal scales the distances."""
        p = self.policy
        width = detection.width if region is None else region.x1 - region.x0
        height = detection.height if region is None else region.y1 - region.y0
        diagonal = math.hypot(width, height)
        attach = max(float(p.attach_min_px), p.attach * diagonal)
        beside = max(float(p.attach_min_px), p.beside * diagonal)

        shapes = sorted(
            (s for s in detection.shapes if s.confidence >= p.min_shape_confidence),
            key=lambda s: (s.box.y0, s.box.x0),
        )
        node_ids = [f"n{i}" for i in range(1, len(shapes) + 1)]
        arrows = sorted(
            (a for a in detection.arrows if a.confidence >= p.min_arrow_confidence),
            key=lambda a: (a.tail.y, a.tail.x),
        )

        ends = [self._ends(a, shapes, attach) for a in arrows]
        node_texts = [_Pieces() for _ in shapes]
        edge_texts = [_Pieces() for _ in arrows]
        free: list[DiagramText] = []
        lines = [piece for t in texts for piece in split_across_shapes(t, [s.box for s in shapes])]
        lines = [t for t in _without_specks(lines) if not _arrow_mark(t, arrows)]
        outside: list[DiagramText] = []
        for t in lines:
            c = centre(t.box)
            holders = [i for i, s in enumerate(shapes) if inside(c, s.box)]
            if holders:
                smallest = min(holders, key=lambda i: shapes[i].box.area)
                node_texts[smallest].texts.append(t)
            else:
                outside.append(t)
        for block in _blocks(outside):
            box = _union([t.box for t in block])
            alongside = _alongside(box, arrows)
            if alongside is not None and not _at_open_end(
                block[0], arrows[alongside], ends[alongside], beside
            ):
                edge_texts[alongside].texts.extend(block)  # written beside the arrow's line
                continue
            near_arrow = _nearest(box, [a.box for a in arrows])
            near_shape = _nearest(box, [s.box for s in shapes])
            if (
                near_arrow is not None
                and near_arrow[1] <= beside
                and (near_shape is None or near_arrow[1] <= near_shape[1])
            ):
                if _at_open_end(block[0], arrows[near_arrow[0]], ends[near_arrow[0]], beside):
                    free.extend(block)  # "Sensors" where an arrow starts: a name, not a label
                else:
                    edge_texts[near_arrow[0]].texts.extend(block)
            elif (
                near_shape is not None
                and near_shape[1] <= beside
                and not node_texts[near_shape[0]].texts
            ):  # a name beside an empty shape (a node of a network); a labelled shape keeps
                node_texts[near_shape[0]].texts.extend(block)  # its own text only
            else:
                free.extend(block)

        nodes: list[DiagramNode] = []
        for node_id, shape, pieces in zip(node_ids, shapes, node_texts, strict=True):
            label, confidence = pieces.label()
            nodes.append(
                DiagramNode(
                    id=node_id,
                    shape=shape.shape,
                    label=label,
                    box=shape.box,
                    confidence=round(shape.confidence, 4),
                    label_confidence=confidence,
                )
            )
        edges: list[DiagramEdge] = []
        for k, (arrow, pieces, (source, target)) in enumerate(
            zip(arrows, edge_texts, ends, strict=True), 1
        ):
            label, _ = pieces.label()
            edges.append(
                DiagramEdge(
                    id=f"e{k}",
                    source=None if source is None else node_ids[source],
                    target=None if target is None else node_ids[target],
                    label=label,
                    directed=arrow.has_head,
                    confidence=round(arrow.confidence, 4),
                    box=arrow.box,
                    tail=arrow.tail,
                    head=arrow.head,
                )
            )
        return DiagramGraph(
            nodes=tuple(nodes),
            edges=tuple(edges),
            recognizer=recognizer,
            label_engines=tuple(label_engines),
            free_labels=tuple(
                FreeLabel(text=" ".join(t.text.split()), box=t.box, confidence=t.confidence)
                for t in sorted(free, key=lambda t: (t.box.y0, t.box.x0))
            ),
        )

    @staticmethod
    def _ends(
        arrow: DetectedArrow, shapes: Sequence[DetectedShape], attach: float
    ) -> tuple[int | None, int | None]:
        def ranked(point: Point) -> list[tuple[float, int]]:
            found = sorted((point_box_distance(point, s.box), i) for i, s in enumerate(shapes))
            return [(d, i) for d, i in found if d <= attach]

        tails, heads = ranked(arrow.tail), ranked(arrow.head)
        tail = tails[0][1] if tails else None
        head = heads[0][1] if heads else None
        if tail is not None and tail == head:
            # both ends on one shape: the end farther from it moves to its next shape
            if tails[0][0] >= heads[0][0]:
                tail = tails[1][1] if len(tails) > 1 else None
            else:
                head = heads[1][1] if len(heads) > 1 else None
        return tail, head


def _union(boxes: Sequence[Box]) -> Box:
    return Box(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )


def _without_specks(texts: Sequence[DiagramText]) -> list[DiagramText]:
    """Leaves out empty lines and specks read as text ("0.", "ie,"): two characters or fewer
    in a box under 60 % of the typical line height."""
    kept = [t for t in texts if t.text.strip()]
    if not kept:
        return []
    heights = sorted(t.box.y1 - t.box.y0 for t in kept)
    typical = heights[len(heights) // 2]
    return [
        t
        for t in kept
        if sum(ch.isalnum() for ch in t.text) > 2 or (t.box.y1 - t.box.y0) >= 0.6 * typical
    ]


def _blocks(texts: Sequence[DiagramText]) -> list[list[DiagramText]]:
    """Lines stacked into one block of text (a label written over several lines): each next
    line starts within one line height below the last and overlaps it sideways."""
    ordered = sorted(texts, key=lambda t: (t.box.y0, t.box.x0))
    blocks: list[list[DiagramText]] = []
    for t in ordered:
        h = t.box.y1 - t.box.y0
        for block in blocks:
            last = block[-1].box
            overlap = min(last.x1, t.box.x1) - max(last.x0, t.box.x0)
            narrower = min(last.x1 - last.x0, t.box.x1 - t.box.x0)
            if 0 <= t.box.y0 - last.y1 + h * 0.3 <= h * 1.3 and overlap >= 0.3 * narrower:
                block.append(t)
                break
        else:
            blocks.append([t])
    return blocks


def _alongside(box: Box, arrows: Sequence[DetectedArrow]) -> int | None:
    """The arrow a block of text is written along: the block's middle falls between the
    arrow's two ends (projected onto its line), no farther from the line than the block's own
    size across the line (its height beside a horizontal arrow, its width beside a vertical
    one). Nearest such arrow, or None."""
    c = centre(box)
    w, h = box.x1 - box.x0, box.y1 - box.y0
    best: tuple[float, int] | None = None
    for k, a in enumerate(arrows):
        dx, dy = a.head.x - a.tail.x, a.head.y - a.tail.y
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        t = ((c.x - a.tail.x) * dx + (c.y - a.tail.y) * dy) / (length * length)
        if not 0.1 <= t <= 0.9:
            continue
        d = abs((c.x - a.tail.x) * dy - (c.y - a.tail.y) * dx) / length
        across = abs(dy) / length * w + abs(dx) / length * h
        if d <= across and (best is None or d < best[0]):
            best = (d, k)
    return None if best is None else best[1]


def _arrow_mark(text: DiagramText, arrows: Sequence[DetectedArrow]) -> bool:
    """One character on an arrow head ("V", ">"): the OCR reading the arrow itself."""
    if len("".join(text.text.split())) > 1:
        return False
    return any(
        point_box_distance(a.head, text.box) <= max(8, (text.box.y1 - text.box.y0)) for a in arrows
    )


def _at_open_end(
    text: DiagramText,
    arrow: DetectedArrow,
    ends: tuple[int | None, int | None],
    near: float,
) -> bool:
    """The text sits at an end of the arrow that touches no shape (a name such as "Sensors"
    or "Actuators" where the arrow starts or stops)."""
    source, target = ends
    open_points = [p for p, e in ((arrow.tail, source), (arrow.head, target)) if e is None]
    return any(point_box_distance(p, text.box) <= near for p in open_points)


def split_across_shapes(text: DiagramText, shapes: Sequence[Box]) -> list[DiagramText]:
    """A line the OCR joined across shapes drawn side by side ("How it evolves" → "What the
    world is now") is cut back into one piece per shape. Handwriting has no word boxes here,
    so each word's place is estimated from its character position along the line, and the
    cut between two shapes goes at the word boundary nearest the middle of the gap between
    them. Each piece takes the part of the line's box over its shape. Lines inside one
    shape, or touching fewer than two, stay whole."""
    words = text.text.split()
    box = text.box
    height = box.y1 - box.y0
    beside = sorted(
        (
            b
            for b in shapes
            if min(box.y1, b.y1) - max(box.y0, b.y0) >= 0.5 * height
            and min(box.x1, b.x1) > max(box.x0, b.x0)
        ),
        key=lambda b: b.x0,
    )
    if len(words) < 2 or len(beside) < 2:
        return [text]
    joined = " ".join(words)
    width = box.x1 - box.x0
    boundaries: list[float] = []  # x of the space after word i
    at = 0
    for w in words[:-1]:
        at += len(w)
        boundaries.append(box.x0 + width * (at + 0.5) / len(joined))
        at += 1
    cuts: list[int] = []
    for left, right in itertools.pairwise(beside):
        gap = (left.x1 + right.x0) / 2
        cut = min(range(len(boundaries)), key=lambda i: abs(boundaries[i] - gap)) + 1
        if not cuts or cut > cuts[-1]:
            cuts.append(cut)
    if len(cuts) != len(beside) - 1:
        return [text]
    out: list[DiagramText] = []
    starts = [0, *cuts]
    ends = [*cuts, len(words)]
    for shape, start, end in zip(beside, starts, ends, strict=True):
        x0, x1 = max(box.x0, shape.x0), min(box.x1, shape.x1)
        if x1 <= x0 or start >= end:
            return [text]
        out.append(
            DiagramText(
                text=" ".join(words[start:end]),
                box=Box(x0=x0, y0=box.y0, x1=x1, y1=box.y1),
                confidence=text.confidence,
            )
        )
    return out


def _nearest(box: Box, boxes: Sequence[Box]) -> tuple[int, float] | None:
    best: tuple[int, float] | None = None
    for i, other in enumerate(boxes):
        d = box_distance(box, other)
        if best is None or d < best[1]:
            best = (i, d)
    return best
