"""A page's regions as lines in reading order, and the page's geometry.

Text lines, labels and **table cells** are all lines: the layout model takes many ruled answer
pages for tables (O44), so a "table" must not hide the answer labels in its first column.
Lines are ordered top to bottom by rows (lines that overlap vertically by half the smaller
height share a row), left to right within a row.

``text_left`` is where the body of the writing starts (a lower-middle percentile of the left
edges of the wide lines: slanted handwriting drifts right down a page), so "near the left
margin" means near it, whatever the margins of the photo, and a label hanging left of the body
(the usual place, left of a ruled margin) stands out. A short
line touching the left or right edge of the image is a sliver of the neighbouring page (O35):
it is never a label and joins no answer."""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from tarn_core.domain.booklet import Region, RegionKind
from tarn_core.domain.common import Box
from tarn_core.ids import PageId, RegionId

_TEXT_KINDS = (RegionKind.TEXT_LINE, RegionKind.LABEL, RegionKind.TEXT_BLOCK)
_ATTACHED_KINDS = (RegionKind.DIAGRAM, RegionKind.TABLE)


@dataclass(frozen=True, slots=True, kw_only=True)
class PageInput:
    """One read page as segmentation sees it (``index`` = upload order)."""

    page_id: PageId
    index: int
    width: int
    height: int
    regions: tuple[Region, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    region_id: RegionId
    page_id: PageId
    box: Box
    text: str
    row: int
    first_in_row: bool
    in_figure: bool
    """Text inside a diagram (its labels): never an answer label."""


@dataclass(frozen=True, slots=True, kw_only=True)
class PageLines:
    page: PageInput
    lines: tuple[Line, ...]
    figures: tuple[Region, ...]
    """Diagram and table regions, attached to the answer they sit in."""
    slivers: tuple[RegionId, ...]
    text_left: int
    line_pitch: float
    """Median distance between the tops of consecutive rows (0 with fewer than 3 rows)."""


PAGE_SIZED = 0.6
"""A figure covering at least this share of the page is taken for the page itself."""


def _area(box: Box) -> int:
    return (box.x1 - box.x0) * (box.y1 - box.y0)


def _centre_inside(box: Box, outer: Box) -> bool:
    cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
    return outer.x0 <= cx <= outer.x1 and outer.y0 <= cy <= outer.y1


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1) + 0.5))]


def is_sliver(box: Box, width: int) -> bool:
    touches = box.x0 <= 0.012 * width or box.x1 >= 0.988 * width
    return touches and (box.x1 - box.x0) <= 0.12 * width


def page_lines(page: PageInput) -> PageLines:
    figures = tuple(r for r in page.regions if r.kind in _ATTACHED_KINDS)
    area = page.width * page.height
    # The layout model takes many photographed ruled pages for one big picture (or table,
    # O44): a figure covering most of the page is the page, and its lines are ordinary lines.
    diagrams = [
        r.box for r in figures if r.kind is RegionKind.DIAGRAM and _area(r.box) < PAGE_SIZED * area
    ]
    candidates: list[Region] = []
    slivers: list[RegionId] = []
    for region in page.regions:
        if region.kind not in _TEXT_KINDS:
            continue
        if is_sliver(region.box, page.width):
            slivers.append(region.id)
        else:
            candidates.append(region)

    candidates.sort(key=lambda r: (r.box.y0 + r.box.y1) / 2)
    rows: list[list[Region]] = []
    for region in candidates:
        if rows:
            band = rows[-1]
            top = min(r.box.y0 for r in band)
            bottom = max(r.box.y1 for r in band)
            overlap = min(bottom, region.box.y1) - max(top, region.box.y0)
            smaller = min(bottom - top, region.box.y1 - region.box.y0)
            if overlap >= 0.5 * smaller:
                band.append(region)
                continue
        rows.append([region])

    lines: list[Line] = []
    for number, band in enumerate(rows):
        for k, region in enumerate(sorted(band, key=lambda r: r.box.x0)):
            lines.append(
                Line(
                    region_id=region.id,
                    page_id=page.page_id,
                    box=region.box,
                    text=(region.text or "").strip(),
                    row=number,
                    first_in_row=k == 0,
                    in_figure=any(_centre_inside(region.box, d) for d in diagrams),
                )
            )

    wide = [ln.box.x0 for ln in lines if ln.box.x1 - ln.box.x0 >= 0.35 * page.width]
    lefts = wide or [ln.box.x0 for ln in lines]
    text_left = int(_percentile(lefts, 0.3)) if lefts else 0
    tops = [min(r.box.y0 for r in band) for band in rows]
    gaps = sorted(b - a for a, b in pairwise(tops) if b > a)
    pitch = float(gaps[len(gaps) // 2]) if len(gaps) >= 2 else 0.0
    return PageLines(
        page=page,
        lines=tuple(lines),
        figures=figures,
        slivers=tuple(slivers),
        text_left=text_left,
        line_pitch=pitch,
    )
