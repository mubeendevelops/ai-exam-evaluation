"""Geometry of the layout: pieces of text joined into lines, reading order, regions inside
regions, and the row/column grid of a table (from its ruling lines, or from where its text
sits when it has none). Pure functions on boxes, apart from the ruling-line finder."""

import itertools
from collections.abc import Sequence
from statistics import median

import cv2

from tarn_adapters.ocr.images import Image, as_image
from tarn_core.domain.common import Box
from tarn_core.ports.engines import TableCell
from tarn_core.services.ocr.align import intersection, union_box


def _height(box: Box) -> int:
    return box.y1 - box.y0


def _vertical_overlap(a: Box, b: Box) -> float:
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    return max(0, overlap) / max(1, min(_height(a), _height(b)))


def join_into_lines(pieces: Sequence[Box], *, gap_factor: float = 1.5) -> list[Box]:
    """Join text pieces (words, phrases) that sit side by side into lines: they overlap
    vertically by at least half the smaller height and the gap between them is at most
    ``gap_factor`` line heights. Returned in reading order."""
    lines: list[Box] = []
    for piece in sorted(pieces, key=lambda b: (b.x0, b.y0)):
        best = None
        for i, line in enumerate(lines):
            if _vertical_overlap(line, piece) < 0.5:
                continue
            gap = piece.x0 - line.x1
            if gap <= gap_factor * max(_height(line), _height(piece)):
                best = i
                break
        if best is None:
            lines.append(piece)
        else:
            lines[best] = union_box(lines[best], piece)
    return reading_order(lines)


def reading_order(boxes: Sequence[Box]) -> list[Box]:
    """Top to bottom; boxes on the same visual row (centres within half a line height) left to
    right."""
    if not boxes:
        return []
    typical = median(_height(b) for b in boxes)
    ordered = sorted(boxes, key=lambda b: (b.y0 + b.y1) / 2)
    rows: list[list[Box]] = []
    for box in ordered:
        centre = (box.y0 + box.y1) / 2
        if rows:
            last = rows[-1]
            last_centre = sum((b.y0 + b.y1) / 2 for b in last) / len(last)
            if abs(centre - last_centre) <= typical / 2:
                last.append(box)
                continue
        rows.append([box])
    return [b for row in rows for b in sorted(row, key=lambda b: b.x0)]


def inside(inner: Box, outer: Box, share: float = 0.6) -> bool:
    return intersection(inner, outer) >= share * inner.area


def _cluster(values: Sequence[float], gap: float) -> list[float]:
    """Centres of 1-D clusters: sorted values split where the step exceeds ``gap``."""
    groups: list[list[float]] = []
    for value in sorted(values):
        if groups and value - groups[-1][-1] <= gap:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(g) / len(g) for g in groups]


def _nearest(centres: Sequence[float], value: float) -> int:
    return min(range(len(centres)), key=lambda i: abs(centres[i] - value))


def cells_from_text(lines: Sequence[Box]) -> tuple[TableCell, ...]:
    """A table without ruling lines: rows from the lines' vertical centres, columns from their
    left edges; each line is a cell (two lines in the same cell are joined)."""
    if not lines:
        return ()
    typical = median(_height(b) for b in lines)
    rows = _cluster([(b.y0 + b.y1) / 2 for b in lines], typical * 0.6)
    cols = _cluster([b.x0 for b in lines], typical * 1.5)
    cells: dict[tuple[int, int], Box] = {}
    for box in lines:
        key = (_nearest(rows, (box.y0 + box.y1) / 2), _nearest(cols, box.x0))
        cells[key] = union_box(cells[key], box) if key in cells else box
    return tuple(TableCell(row=r, col=c, box=b) for (r, c), b in sorted(cells.items()))


def ruling_positions(mask: Image, axis: int, min_length: float) -> list[int]:
    """Positions of the long straight lines in a binary mask: rows (axis 1 summed) or
    columns (axis 0 summed) where at least ``min_length`` pixels are set."""
    counts = (mask > 0).sum(axis=axis)
    positions = [i for i, c in enumerate(counts.tolist()) if c >= min_length]
    merged: list[list[int]] = []
    for p in positions:
        if merged and p - merged[-1][-1] <= 3:
            merged[-1].append(p)
        else:
            merged.append([p])
    return [round(sum(g) / len(g)) for g in merged]


def cells_from_rulings(table: Image, origin: Box) -> tuple[TableCell, ...]:
    """A ruled table: the grid between its long horizontal and vertical lines (at least two
    of each); empty when there is no such grid."""
    gray = cv2.cvtColor(table, cv2.COLOR_BGR2GRAY) if table.ndim == 3 else table
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 25, 15
    )
    height, width = binary.shape[:2]
    horizontal = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(10, width // 4), 1))
    )
    vertical = cv2.morphologyEx(
        binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(10, height // 4)))
    )
    ys = ruling_positions(as_image(horizontal), axis=1, min_length=width * 0.5)
    xs = ruling_positions(as_image(vertical), axis=0, min_length=height * 0.5)
    if len(ys) < 2 or len(xs) < 2:
        return ()
    cells = []
    for r, (top, bottom) in enumerate(itertools.pairwise(ys)):
        for c, (left, right) in enumerate(itertools.pairwise(xs)):
            if bottom - top < 8 or right - left < 8:
                continue
            cells.append(
                TableCell(
                    row=r,
                    col=c,
                    box=Box(
                        x0=origin.x0 + left + 2,
                        y0=origin.y0 + top + 2,
                        x1=origin.x0 + right - 1,
                        y1=origin.y0 + bottom - 1,
                    ),
                )
            )
    return tuple(cells)
