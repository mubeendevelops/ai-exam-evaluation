"""Answers made of written lines, for scoring tests: each segment on its own page, each line a
text-line region read by one fake engine. All text is synthetic."""

from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.domain.booklet import (
    Answer,
    Booklet,
    LineReading,
    Page,
    Region,
    RegionKind,
    Segment,
    SegmentFlag,
    SegmentSource,
    SegmentSpan,
)
from tarn_core.domain.common import Box, EngineRef, college_blob_key
from tarn_core.ids import AnswerId, PageId, RegionId, SegmentId
from tarn_core.testing.builders import Backend

ENGINE = EngineRef(name="scripted", version="1")


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    flagged: bool = False
    """Below the OCR line threshold."""
    struck_out: bool = False
    kind: RegionKind = RegionKind.TEXT_LINE


@dataclass(frozen=True, slots=True)
class Written:
    """One segment of an answer."""

    lines: Sequence[str | Line]
    flags: tuple[SegmentFlag, ...] = ()
    position: int | None = None


def written_answer(
    mem: Backend, booklet: Booklet, slot_label: str, *segments: Written | Sequence[str | Line]
) -> Answer:
    """An answer to ``slot_label`` whose segments hold these lines (in this order)."""
    college_id = booklet.college_id
    segment_ids: list[SegmentId] = []
    for k, given in enumerate(segments):
        written = given if isinstance(given, Written) else Written(lines=given)
        index = len(mem.booklets.pages(college_id, booklet.id))
        page = Page(
            id=PageId(mem.ids.new()),
            college_id=college_id,
            booklet_id=booklet.id,
            index=index,
            image=college_blob_key(college_id, "booklet", str(booklet.id), f"p{index}.png"),
            width=1240,
            height=1754,
            text_read=True,
            reading_order=index,
        )
        mem.booklets.save_page(college_id, page)
        mem.blobs.put(page.image, b"synthetic page", "image/png")
        region_ids: list[RegionId] = []
        for n, raw in enumerate(written.lines):
            line = raw if isinstance(raw, Line) else Line(raw)
            box = Box(x0=40, y0=40 + 60 * n, x1=1200, y1=90 + 60 * n)
            region = Region(
                id=RegionId(mem.ids.new()),
                college_id=college_id,
                page_id=page.id,
                kind=line.kind,
                box=box,
                readings=(LineReading(engine=ENGINE, text=line.text, box=box, confidence=0.9),)
                if line.kind is RegionKind.TEXT_LINE
                else (),
                chosen=0 if line.kind is RegionKind.TEXT_LINE else None,
                flagged=line.flagged,
                struck_out=line.struck_out,
            )
            mem.booklets.save_region(college_id, region)
            region_ids.append(region.id)
        segment = Segment(
            id=SegmentId(mem.ids.new()),
            college_id=college_id,
            booklet_id=booklet.id,
            slot_label=slot_label,
            spans=(SegmentSpan(page_id=page.id, box=Box(x0=0, y0=0, x1=1240, y1=1700)),),
            source=SegmentSource.RULE,
            region_ids=tuple(region_ids),
            flags=written.flags,
            position=written.position if written.position is not None else index + k,
        )
        mem.booklets.save_segment(college_id, segment)
        segment_ids.append(segment.id)
    answer = Answer(
        id=AnswerId(mem.ids.new()),
        college_id=college_id,
        booklet_id=booklet.id,
        slot_label=slot_label,
        segment_ids=tuple(segment_ids),
    )
    mem.booklets.save_answer(college_id, answer)
    return answer
