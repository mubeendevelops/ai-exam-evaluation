"""Alignment of engine readings onto the detected lines by box overlap."""

import pytest

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.services.ocr.align import align, intersection, iou

LINE_1 = Box(x0=100, y0=100, x1=900, y1=150)
LINE_2 = Box(x0=100, y0=170, x1=900, y1=220)


def r(engine: str, text: str, box: Box, confidence: float = 0.9) -> LineReading:
    return LineReading(
        engine=EngineRef(name=engine, version="1"), text=text, box=box, confidence=confidence
    )


def test_iou_and_intersection() -> None:
    a = Box(x0=0, y0=0, x1=10, y1=10)
    b = Box(x0=5, y0=0, x1=15, y1=10)
    assert intersection(a, b) == 50
    assert iou(a, b) == pytest.approx(50 / 150)
    assert iou(a, Box(x0=20, y0=20, x1=30, y1=30)) == 0.0
    assert iou(a, a) == 1.0


def test_local_readings_sit_on_their_lines() -> None:
    lines = align(
        [LINE_1, LINE_2], {"trocr": [r("trocr", "one", LINE_1), r("trocr", "two", LINE_2)]}
    )
    assert [line.readings["trocr"].text for line in lines] == ["one", "two"]
    assert all(line.detected for line in lines)


def test_a_cloud_line_matches_by_iou() -> None:
    shifted = Box(x0=110, y0=105, x1=880, y1=155)  # IoU well above 0.5
    lines = align([LINE_1, LINE_2], {"azure": [r("azure", "first line", shifted)]})
    assert lines[0].readings["azure"].text == "first line"
    assert lines[0].readings["azure"].box == LINE_1  # aligned to our line
    assert "azure" not in lines[1].readings


def test_cloud_words_join_left_to_right_with_length_weighted_confidence() -> None:
    words = [
        r("textract", "descent", Box(x0=400, y0=105, x1=560, y1=145), 0.6),
        r("textract", "gradient", Box(x0=110, y0=105, x1=380, y1=145), 0.9),
    ]
    lines = align([LINE_1], {"textract": words})
    merged = lines[0].readings["textract"]
    assert merged.text == "gradient descent"
    assert merged.confidence == pytest.approx((0.9 * 8 + 0.6 * 7) / 15)


def test_a_line_the_detector_split_is_joined_by_the_cloud_line() -> None:
    left = Box(x0=100, y0=100, x1=480, y1=150)
    right = Box(x0=520, y0=100, x1=900, y1=150)
    whole = Box(x0=100, y0=100, x1=900, y1=150)
    # The cloud's whole line overlaps each half with IoU < 0.5 and is not inside either:
    # it matches neither of our lines and becomes an extra line, never a double assignment.
    lines = align([left, right], {"azure": [r("azure", "left right", whole)]})
    assigned = [line for line in lines if "azure" in line.readings]
    assert len(assigned) == 1
    assert assigned[0].detected is False


def test_unmatched_readings_become_extra_lines_clustered_across_engines() -> None:
    missed = Box(x0=100, y0=400, x1=900, y1=450)
    nearly = Box(x0=105, y0=402, x1=890, y1=452)
    lines = align(
        [LINE_1],
        {
            "azure": [r("azure", "a line we missed", missed)],
            "textract": [r("textract", "a line we mised", nearly)],
        },
    )
    assert len(lines) == 2
    extra = lines[1]
    assert extra.detected is False
    assert set(extra.readings) == {"azure", "textract"}
    assert lines[0].readings == {}


def test_cloud_words_of_a_missed_line_cluster_inside_its_line() -> None:
    line_box = Box(x0=100, y0=400, x1=900, y1=450)
    word = Box(x0=120, y0=405, x1=300, y1=445)
    lines = align(
        [LINE_1],
        {
            "azure": [r("azure", "whole missed line", line_box)],
            "textract": [r("textract", "whole", word)],
        },
    )
    extra = lines[1]
    assert set(extra.readings) == {"azure", "textract"}


def test_extra_lines_are_ordered_top_to_bottom() -> None:
    low = Box(x0=0, y0=900, x1=500, y1=950)
    high = Box(x0=0, y0=500, x1=500, y1=550)
    lines = align([], {"azure": [r("azure", "low", low), r("azure", "high", high)]})
    assert [line.readings["azure"].text for line in lines] == ["high", "low"]


def test_a_taller_cloud_line_matches_by_iou_alone() -> None:
    # Our line lies wholly inside the cloud's box: IoU = 40000 / 72800 = 0.55, and the cloud box
    # is only 55 % inside our line, so only the IoU rule can match it.
    tall = Box(x0=100, y0=80, x1=900, y1=171)
    assert iou(tall, LINE_1) == pytest.approx(0.549, abs=0.001)
    lines = align([LINE_1], {"azure": [r("azure", "matched", tall)]})
    assert len(lines) == 1 and lines[0].readings["azure"].text == "matched"
