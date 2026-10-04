"""Synthetic booklet pages for segmentation tests: lines written as short strings, laid out
like a ruled answer page (labels hanging in the margin, body lines in the text column).
No sample text: every line is made up.

Line syntax, one string per line, top to bottom:

- ``"> 3. text"``: hangs in the left margin (where labels are written);
- ``"text"``: a body line; ``"  text"``: an indented body line (a list item);
- ``"^ Section B"``: a centred heading;
- ``"~"``: a blank gap of three ruled lines;
- ``"@4"``: the page number 4 written at the top right;
- ``"[diagram]"`` / ``"[table]"``: a drawing or table four lines tall;
- ``"! text"``: a sliver of the neighbouring page at the right edge of the photo."""

import uuid
from collections.abc import Sequence

from tarn_core.domain.booklet import Region, RegionKind
from tarn_core.domain.common import Box
from tarn_core.ids import CollegeId, PageId, RegionId
from tarn_core.services.segmentation.lines import PageInput

WIDTH = 1400
HEIGHT = 2200
TOP = 150
PITCH = 70
LINE = 50
MARGIN_X = 70
BODY_X = 260
INDENT_X = 340


def _id(seed: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"tarn-test-segmentation:{seed}")


def sheet(
    college_id: CollegeId,
    index: int,
    lines: Sequence[str],
    *,
    booklet: str = "b",
    page_id: PageId | None = None,
) -> PageInput:
    """One page of ``lines`` (see the module's syntax)."""
    pid = page_id or PageId(_id(f"{booklet}:{index}"))
    regions: list[Region] = []
    y = TOP

    def add(kind: RegionKind, box: Box, text: str | None) -> None:
        regions.append(
            Region(
                id=RegionId(_id(f"{booklet}:{index}:{len(regions)}")),
                college_id=college_id,
                page_id=pid,
                kind=kind,
                box=box,
                teacher_text=text,
            )
        )

    for raw in lines:
        if raw == "~":
            y += 3 * PITCH
            continue
        if raw.startswith("@"):
            add(RegionKind.TEXT_LINE, Box(x0=1250, y0=40, x1=1320, y1=90), raw[1:])
            continue
        if raw in ("[diagram]", "[table]"):
            kind = RegionKind.DIAGRAM if raw == "[diagram]" else RegionKind.TABLE
            add(kind, Box(x0=BODY_X, y0=y, x1=1100, y1=y + 4 * PITCH - 20), None)
            y += 4 * PITCH
            continue
        if raw.startswith("! "):
            add(RegionKind.TEXT_LINE, Box(x0=WIDTH - 90, y0=y, x1=WIDTH, y1=y + LINE), raw[2:])
            continue
        if raw.startswith("> "):
            x0, text = MARGIN_X, raw[2:]
        elif raw.startswith("^ "):
            x0, text = 560, raw[2:]
        elif raw.startswith("  "):
            x0, text = INDENT_X, raw.strip()
        else:
            x0, text = BODY_X, raw
        x1 = 840 if raw.startswith("^ ") else min(WIDTH - 60, x0 + 120 + 18 * len(text))
        add(RegionKind.TEXT_LINE, Box(x0=x0, y0=y, x1=max(x1, x0 + 60), y1=y + LINE), text)
        y += PITCH
    return PageInput(page_id=pid, index=index, width=WIDTH, height=HEIGHT, regions=tuple(regions))


def booklet_pages(
    college_id: CollegeId, pages: Sequence[Sequence[str]], booklet: str = "b"
) -> list[PageInput]:
    return [sheet(college_id, k, lines, booklet=booklet) for k, lines in enumerate(pages)]
