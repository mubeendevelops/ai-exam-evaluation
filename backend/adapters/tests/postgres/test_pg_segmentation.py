"""Segmentation on PostgreSQL (P12): segments keep their regions, flags, written order and
proposed question; the CHECKs refuse what the domain refuses; segmenting and the teacher's
edits run through ``tarn_app`` under row-level security, and another college sees nothing."""

from dataclasses import replace

import psycopg
import pytest

from tarn_adapters.postgres.testing import Opener, TestDatabase, World
from tarn_core.domain.booklet import (
    BookletStatus,
    Region,
    RegionKind,
    Segment,
    SegmentFlag,
    SegmentSource,
    SegmentSpan,
)
from tarn_core.domain.common import Box
from tarn_core.errors import NotFoundError
from tarn_core.ids import RegionId, SegmentId
from tarn_core.services.segmentation.edits import SegmentEditor
from tarn_core.services.segmentation.service import BookletSegmenter
from tarn_core.services.segmentation.similarity import TrigramEmbedder

pytestmark = pytest.mark.integration

BOX = Box(x0=10, y0=20, x1=900, y1=70)


def _lines(session: Opener, world: World, texts: list[str]) -> list[RegionId]:
    """Replace page 1 of college A's booklet with lines of ``texts`` (labels at the margin)."""
    with session(world.a.id) as s:
        page = s.booklets.pages(world.a.id, world.a.booklet.id)[0]
        regions = [
            Region(
                id=RegionId(s.ids.new()),
                college_id=world.a.id,
                page_id=page.id,
                kind=RegionKind.TEXT_LINE,
                box=Box(
                    x0=70 if t[:1].isdigit() else 260, y0=100 + 70 * k, x1=1100, y1=150 + 70 * k
                ),
                teacher_text=t,
            )
            for k, t in enumerate(texts)
        ]
        s.booklets.replace_regions(world.a.id, page.id, regions)
        others = s.booklets.pages(world.a.id, world.a.booklet.id)[1:]
        for other in others:
            s.booklets.replace_regions(world.a.id, other.id, [])
        booklet = s.booklets.get(world.a.id, world.a.booklet.id)
        for answer in s.booklets.answers(world.a.id, booklet.id):
            s.scores.delete_for_answers(world.a.id, [answer.id])
        s.booklets.save(
            world.a.id,
            replace(booklet, status=BookletStatus.TEXT_READY, version=booklet.version + 1),
        )
        return [r.id for r in regions]


def _segmenter(s: object) -> BookletSegmenter:
    return BookletSegmenter(
        booklets=s.booklets,  # type: ignore[attr-defined]
        content=s.content,  # type: ignore[attr-defined]
        embedder=TrigramEmbedder(),
        runtime=s.runtime,  # type: ignore[attr-defined]
        jobs=s.jobs,  # type: ignore[attr-defined]
    )


def test_segment_round_trip(session: Opener, world: World) -> None:
    with session(world.a.id) as s:
        page = s.booklets.pages(world.a.id, world.a.booklet.id)[0]
        segment = Segment(
            id=SegmentId(s.ids.new()),
            college_id=world.a.id,
            booklet_id=world.a.booklet.id,
            slot_label=None,
            spans=(SegmentSpan(page_id=page.id, box=BOX), SegmentSpan(page_id=page.id, box=BOX)),
            source=SegmentSource.SIMILARITY,
            match_score=0.31,
            region_ids=(RegionId(s.ids.new()), RegionId(s.ids.new())),
            flags=(SegmentFlag.NUMBER_UNREAD, SegmentFlag.BEFORE_FIRST_ANSWER),
            position=7,
            proposed_label="12.a",
        )
        s.booklets.save_segment(world.a.id, segment)
    with session(world.a.id) as s:
        stored = next(
            x for x in s.booklets.segments(world.a.id, world.a.booklet.id) if x.id == segment.id
        )
        assert stored == segment
        s.booklets.delete_segment(world.a.id, segment.id)
        s.booklets.delete_segment(world.a.id, segment.id)  # already gone: no error
        assert segment.id not in {x.id for x in s.booklets.segments(world.a.id, world.a.booklet.id)}


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE segments SET flags = ARRAY['made_up'] WHERE college_id = %(c)s",
        "UPDATE segments SET proposed_label = '3' "
        "WHERE college_id = %(c)s AND slot_label IS NOT NULL",
        "UPDATE segments SET source = 'guess' WHERE college_id = %(c)s",
        "UPDATE segments SET position = -1 WHERE college_id = %(c)s",
        "UPDATE pages SET written_number = 0 WHERE college_id = %(c)s",
        "UPDATE pages SET reading_order = -1 WHERE college_id = %(c)s",
    ],
)
def test_checks_refuse_what_the_domain_refuses(
    test_database: TestDatabase, world: World, sql: str
) -> None:
    with test_database.owner() as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(sql, {"c": world.a.id})


def test_segmenting_and_editing_under_row_level_security(session: Opener, world: World) -> None:
    _lines(
        session,
        world,
        [
            "1. freedom of speech and expression and assembly",
            "freedom to move and reside anywhere in the country",
            "2. national emergency, state emergency and financial emergency",
            "three types of emergency in the constitution",
        ],
    )
    with session(world.a.id) as s:
        assert _segmenter(s).step(world.a.id, world.a.booklet.id)
    with session(world.a.id) as s:
        segments = list(s.booklets.segments(world.a.id, world.a.booklet.id))
        assert [x.slot_label for x in segments][-2:] == ["1", "2"]
        assert s.booklets.get(world.a.id, world.a.booklet.id).status is BookletStatus.SEGMENTED
        labels = {a.slot_label for a in s.booklets.answers(world.a.id, world.a.booklet.id)}
        assert {"1", "2"} <= labels
    with session(world.b.id) as s:
        assert all(
            x.booklet_id != world.a.booklet.id
            for x in s.booklets.segments(world.b.id, world.a.booklet.id)
        )
        with pytest.raises(NotFoundError):
            _segmenter(s).step(world.b.id, world.a.booklet.id)
    with session(world.a.id) as s:
        one, two = segments[-2], segments[-1]
        editor = SegmentEditor(
            booklets=s.booklets, scores=s.scores, content=s.content, runtime=s.runtime
        )
        result = editor.merge(
            world.a.id, world.a.college.teacher.id, world.a.booklet.id, one.id, two.id
        )
        assert len(result.rescore) == 1 and len(result.emptied) == 1
    with session(world.a.id) as s:
        after = [x.slot_label for x in s.booklets.segments(world.a.id, world.a.booklet.id)]
        assert after[-1] == "1" and "2" not in after
        assert "2" not in {a.slot_label for a in s.booklets.answers(world.a.id, world.a.booklet.id)}
