"""Arrow ends from a detected arrow box, its head box (if any) and the ink inside: the detector
finds boxes, the graph needs points.

With a head: the tail is the ink in the arrow box farthest from the head box's centre (a
straight or once-bent arrow ends there), the head point is the ink in the head box farthest from
the tail (the tip). Without a head (a line in a tree or network): the two ends are the ink
pixels farthest apart (approximately: farthest from the box centre, then farthest from that).
With no usable ink (an empty crop), the box's corners or edge middles stand in."""

import math

import cv2
import numpy as np
from numpy.typing import NDArray

from tarn_core.domain.common import Box
from tarn_core.domain.diagram import Point

type Gray = NDArray[np.uint8]
type FBox = tuple[float, float, float, float]


def ink_points(gray: Gray, box: FBox, *, limit: int = 4000) -> NDArray[np.float64]:
    """Dark pixels inside ``box`` (image coordinates), at most ``limit`` evenly picked."""
    h, w = gray.shape[:2]
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(w, math.ceil(box[2]) + 1), min(h, math.ceil(box[3]) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return np.zeros((0, 2))
    crop = gray[y0:y1, x0:x1]
    if int(crop.max()) - int(crop.min()) < 30:  # flat: no strokes
        return np.zeros((0, 2))
    _, binary = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    ys, xs = np.nonzero(binary)
    if len(xs) == 0:
        return np.zeros((0, 2))
    step = max(1, len(xs) // limit)
    return np.stack([xs[::step] + x0, ys[::step] + y0], axis=1).astype(np.float64)


def _corners(box: FBox) -> NDArray[np.float64]:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return np.array(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (cx, y0), (x1, cy), (cx, y1), (x0, cy)],
        np.float64,
    )


def _farthest(points: NDArray[np.float64], origin: tuple[float, float]) -> tuple[float, float]:
    d = (points[:, 0] - origin[0]) ** 2 + (points[:, 1] - origin[1]) ** 2
    x, y = points[int(np.argmax(d))]
    return float(x), float(y)


def arrow_ends(
    gray: Gray, arrow: FBox, head: FBox | None
) -> tuple[tuple[float, float], tuple[float, float]]:
    """(tail, head) points of an arrow, or the two ends of a line when ``head`` is None."""
    ink = ink_points(gray, arrow)
    candidates = ink if len(ink) else _corners(arrow)
    if head is not None:
        hc = ((head[0] + head[2]) / 2, (head[1] + head[3]) / 2)
        tail = _farthest(candidates, hc)
        head_ink = ink_points(gray, head)
        tip = _farthest(head_ink, tail) if len(head_ink) else hc
        return tail, tip
    centre = ((arrow[0] + arrow[2]) / 2, (arrow[1] + arrow[3]) / 2)
    first = _farthest(candidates, centre)
    second = _farthest(candidates, first)
    return first, second


def head_for(arrow: FBox, heads: list[tuple[FBox, float]]) -> int | None:
    """The index of the most confident head box whose centre lies in the arrow box (widened by
    a tenth), or None."""
    x0, y0, x1, y1 = arrow
    mx, my = (x1 - x0) * 0.1 + 4, (y1 - y0) * 0.1 + 4
    best: tuple[float, int] | None = None
    for k, (hb, score) in enumerate(heads):
        cx, cy = (hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2
        if (
            x0 - mx <= cx <= x1 + mx
            and y0 - my <= cy <= y1 + my
            and (best is None or score > best[0])
        ):
            best = (score, k)
    return None if best is None else best[1]


def to_point(p: tuple[float, float], width: int, height: int) -> Point:
    return Point(
        x=min(max(0, round(p[0])), max(0, width - 1)),
        y=min(max(0, round(p[1])), max(0, height - 1)),
    )


def to_box(b: FBox, width: int, height: int) -> Box | None:
    x0, y0 = max(0, math.floor(b[0])), max(0, math.floor(b[1]))
    x1, y1 = min(width, math.ceil(b[2])), min(height, math.ceil(b[3]))
    if x1 <= x0 or y1 <= y0:
        return None
    return Box(x0=x0, y0=y0, x1=x1, y1=y1)


def iou(a: FBox, b: FBox) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return 0.0 if union <= 0 else inter / union


def nms(boxes: list[FBox], scores: list[float], threshold: float) -> list[int]:
    """Indices kept by greedy non-maximum suppression, best first."""
    order = sorted(range(len(boxes)), key=lambda i: -scores[i])
    kept: list[int] = []
    for i in order:
        if all(iou(boxes[i], boxes[j]) < threshold for j in kept):
            kept.append(i)
    return kept
