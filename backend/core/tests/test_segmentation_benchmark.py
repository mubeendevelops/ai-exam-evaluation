"""The segmentation benchmark's measures, checked by hand, and its report holding no text."""

from uuid import UUID

from tarn_core.domain.booklet import SegmentFlag, SegmentSource, SegmentSpan
from tarn_core.domain.common import Box, EngineRef
from tarn_core.ids import PageId, RegionId
from tarn_core.services.segmentation.benchmark import (
    BookletRun,
    BookletTruth,
    EmbedderRuns,
    TruthPage,
    render_report,
    score_booklet,
    summarise,
)
from tarn_core.services.segmentation.pages import PageOrder
from tarn_core.services.segmentation.segmenter import ProposedSegment, SegmentationResult

PAGES = [PageId(UUID(int=k + 1)) for k in range(3)]
BOX = Box(x0=0, y0=0, x1=10, y1=10)


def segment(
    label: str | None, pages: list[int], position: int, *flags: SegmentFlag
) -> ProposedSegment:
    return ProposedSegment(
        slot_label=label,
        source=SegmentSource.RULE,
        region_ids=(RegionId(UUID(int=100 + position)),),
        spans=tuple(SegmentSpan(page_id=PAGES[p - 1], box=BOX) for p in pages),
        position=position,
        flags=flags,
    )


TRUTH = BookletTruth(
    booklet="x",
    paper="QP-CI",
    pages=(
        TruthPage(page=1, continues=None, starts=("1", "2")),
        TruthPage(page=2, continues="2", starts=("5",)),
        TruthPage(page=3, continues="5", starts=()),
    ),
    written_numbers=(1, 2, None),
)

RESULT = SegmentationResult(
    order=PageOrder(order=tuple(PAGES), written={PAGES[0]: 1, PAGES[1]: 3}, reordered=False),
    segments=(
        segment(None, [1], 0, SegmentFlag.BEFORE_FIRST_ANSWER),
        segment("1", [1], 1),
        segment("2", [1, 2], 2),
        segment(None, [2, 3], 3),  # answer 5 left unassigned
    ),
    slivers=0,
    embedder=EngineRef(name="trigram", version="1"),
)
NUMBERS = {pid: k + 1 for k, pid in enumerate(PAGES)}


def test_scores_by_hand() -> None:
    score = score_booklet(TRUTH, RESULT, NUMBERS)
    assert score.truth_order == ("1", "2", "5")
    assert score.predicted_order == ("1", "2", "?")
    assert score.order_distance == 1 and not score.order_exact
    assert (score.true_starts, score.predicted_starts, score.correct_starts) == (3, 3, 2)
    assert score.continuation_pages == 2 and score.correct_continuations == 1  # p2 yes, p3 no
    # page 1: {1,2} vs {1,2}; page 2: {2,5} vs {2,?} = 1/3; page 3: {5} vs {?} = 0
    assert abs(score.page_jaccard - (1 + 1 / 3 + 0) / 3) < 1e-9
    assert score.unassigned == 1
    summary = summarise([score])
    assert summary["start_precision"] == 2 / 3 and summary["orders_exact"] == 0


def test_report_holds_labels_and_counts_only() -> None:
    run = BookletRun(
        booklet="x", paper="QP-CI", result=RESULT, page_numbers=NUMBERS, seconds=0.5, truth=TRUTH
    )
    assert run.written_found == {1: 1, 2: 3}
    text = render_report(
        date="2026-10-04",
        compared=[EmbedderRuns(name="trigram 1", runs=(run,))],
        policy={"switch_cost": 3.0},
        unlabelled=["y: no paper known"],
    )
    assert "| x | QP-CI | 1, 2, 5 | 1, 2, ? | 1 |" in text
    assert "| x | 2 | 1 | 1 | no |" in text  # page 2 was read as 3
    assert "switch_cost" in text and "y: no paper known" in text
