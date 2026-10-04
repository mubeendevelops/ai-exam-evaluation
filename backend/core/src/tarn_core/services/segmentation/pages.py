"""Page order from the page numbers students write (design.md "Segmentation" step 1).

A page number is a lone number (``3``, ``-3-``, ``(3)``, ``Page 3``, ``Pg. 3``, ``3/12``) in
the top or bottom band of the page and away from the left margin, where answer labels sit.
The upload order is changed only when the numbers are trustworthy: at least two pages carry
one, together at least ``min_share`` of the pages, all different and no larger than the
booklet could be. Unnumbered pages keep their place after the page uploaded before them; pages
before the first numbered page (a cover, an admission ticket) stay first."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.ids import PageId, RegionId
from tarn_core.services.segmentation.lines import PageLines

_PAGE_NUMBER = re.compile(
    r"^[\s\-\u2013(\[]*(?:page|pg|p)?\s*[.:#\-]?\s*(?P<n>\d{1,2})\s*(?:/\s*\d{1,2})?[\s\-\u2013)\].]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PageOrder:
    order: tuple[PageId, ...]
    """Pages in reading order."""
    written: dict[PageId, int]
    """The number found on each page that has one."""
    reordered: bool


def _candidates(lines: PageLines, band: float) -> list[tuple[RegionId, int]]:
    page = lines.page
    found: list[tuple[RegionId, int]] = []
    for line in lines.lines:
        match = _PAGE_NUMBER.match(line.text)
        if match is None:
            continue
        cx = (line.box.x0 + line.box.x1) / 2
        cy = (line.box.y0 + line.box.y1) / 2
        if cx < max(0.35 * page.width, lines.text_left + 0.2 * page.width):
            continue  # left side: an answer label, not a page number
        if cy <= band * page.height or cy >= (1 - band) * page.height:
            found.append((line.region_id, int(match.group("n"))))
    return [(rid, n) for rid, n in found if n > 0]


def written_number(lines: PageLines, band: float = 0.1) -> int | None:
    numbers = {n for _, n in _candidates(lines, band)}
    if len(numbers) != 1:
        return None  # none, or several different ones: not trusted
    return numbers.pop()


def page_number_line(lines: PageLines, band: float = 0.1) -> RegionId | None:
    """The line holding the written page number, when there is exactly one."""
    found = _candidates(lines, band)
    return found[0][0] if len(found) == 1 else None


def page_order(pages: Sequence[PageLines], min_share: float = 0.4) -> PageOrder:
    """``pages`` in upload order."""
    upload = tuple(p.page.page_id for p in pages)
    written = {p.page.page_id: n for p in pages if (n := written_number(p)) is not None}
    numbers = list(written.values())
    trusted = (
        len(written) >= 2
        and len(written) >= min_share * len(pages)
        and len(set(numbers)) == len(numbers)
        and max(numbers) <= len(pages) + 2
    )
    if not trusted:
        return PageOrder(order=upload, written=written, reordered=False)
    prefix: list[PageId] = []
    groups: list[tuple[int, list[PageId]]] = []
    for page_id in upload:
        if page_id in written:
            groups.append((written[page_id], [page_id]))
        elif groups:
            groups[-1][1].append(page_id)
        else:
            prefix.append(page_id)
    groups.sort(key=lambda g: g[0])
    order = tuple(prefix + [p for _, members in groups for p in members])
    return PageOrder(order=order, written=written, reordered=order != upload)
