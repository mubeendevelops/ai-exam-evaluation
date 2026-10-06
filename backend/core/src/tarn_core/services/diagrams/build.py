"""From detections and text to a graph (design.md "Diagram comparison", step 1).

- Shapes become nodes (``n1``, ``n2``… in reading order), below a confidence floor left out.
- Each arrow end attaches to the nearest shape whose box it touches or nearly touches
  (``policy.attach`` × the diagram's diagonal, at least ``attach_min_px``); an end near no
  shape stays None (a dangling arrow). When both ends land on one shape, the farther end tries
  the next shape.
- Each text line (read best-of-N by the OCR framework) inside a shape labels it; outside every
  shape it labels the nearest arrow (``yes``/``no`` beside a decision) or, failing that, the
  nearest shape (a name beside a circle in a network); else it is a free label."""

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


@dataclass
class _Pieces:
    texts: list[DiagramText] = field(default_factory=list)

    def label(self) -> tuple[str, float | None]:
        ordered = sorted(self.texts, key=lambda t: (t.box.y0, t.box.x0))
        text = " ".join(" ".join(t.text.split()) for t in ordered).strip()
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

        node_texts = [_Pieces() for _ in shapes]
        edge_texts = [_Pieces() for _ in arrows]
        free: list[DiagramText] = []
        for t in texts:
            if not t.text.strip():
                continue
            c = centre(t.box)
            holders = [i for i, s in enumerate(shapes) if inside(c, s.box)]
            if holders:
                smallest = min(holders, key=lambda i: shapes[i].box.area)
                node_texts[smallest].texts.append(t)
                continue
            near_arrow = _nearest(t.box, [a.box for a in arrows])
            near_shape = _nearest(t.box, [s.box for s in shapes])
            if (
                near_arrow is not None
                and near_arrow[1] <= beside
                and (near_shape is None or near_arrow[1] <= near_shape[1])
            ):
                edge_texts[near_arrow[0]].texts.append(t)
            elif near_shape is not None and near_shape[1] <= beside:
                node_texts[near_shape[0]].texts.append(t)
            else:
                free.append(t)

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
        for k, (arrow, pieces) in enumerate(zip(arrows, edge_texts, strict=True), 1):
            source, target = self._ends(arrow, shapes, attach)
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


def _nearest(box: Box, boxes: Sequence[Box]) -> tuple[int, float] | None:
    best: tuple[int, float] | None = None
    for i, other in enumerate(boxes):
        d = box_distance(box, other)
        if best is None or d < best[1]:
            best = (i, d)
    return best
