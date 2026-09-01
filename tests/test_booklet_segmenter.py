"""Tests for core/booklet_segmenter.py.

Split by cost, as this suite does elsewhere:
  - the marker-parsing and region-assignment tests are pure logic over plain
    dicts. No layout model, no OCR, no images — they test the part this
    module actually owns, which is the association rules.
  - the end-to-end segmentation of the real fixture booklet is marked `slow`,
    since it loads PP-DocLayout_plus-L and the OCR ensemble.
    `pytest -m "not slow"` skips it.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core import booklet_segmenter as bs

BOOKLETS_DIR = (pathlib.Path(__file__).resolve().parent.parent
                / "media" / "booklets")
BOOKLET_PDF = BOOKLETS_DIR / "sample_booklet.pdf"


def region(page, y, block_type="text", *, x=100, w=800, h=100, confidence=0.9):
    return {
        "page_number": page, "bbox": [x, y, w, h], "block_type": block_type,
        "layout_label": block_type, "confidence": confidence,
        "needs_review": False, "review_reasons": [], "segmenter": "test",
    }


def marker(page, y, kind, value, *, x=100, w=60, h=50, confidence=0.95):
    return {
        "page_number": page, "bbox": [x, y, w, h], "raw_text": value,
        "kind": kind, "value": value, "confidence": confidence,
        "ocr_engine": "test",
    }


# --- marker parsing ------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Q1", ("numeric", "1")),
    ("Q1.", ("numeric", "1")),
    ("q3)", ("numeric", "3")),
    ("1.", ("numeric", "1")),
    ("12.", ("numeric", "12")),
    ("(4)", ("numeric", "4")),
    ("Q 2 .", ("numeric", "2")),
    ("07.", ("numeric", "7")),
    ("a)", ("subpart", "a")),
    ("(b)", ("subpart", "b")),
    ("c.", ("subpart", "c")),
])
def test_markers_that_should_parse(text, expected):
    assert bs._normalize_marker(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "FCFS", "RR", "SJF",
    "4 ms of quantum",          # prose starting with a digit
    "1234",                     # no terminator, too long to be a question
    "z)",                       # outside the a-h sub-part range
    "Table 1",
    "8.0",                      # a numeric table cell
])
def test_markers_that_should_not_parse(text):
    assert bs._normalize_marker(text) is None


# --- assignment ----------------------------------------------------------

def test_regions_are_assigned_to_the_preceding_marker():
    regions = [region(1, 200), region(1, 400)]
    markers = [marker(1, 100, "numeric", "1")]
    assigned = bs.assign_regions(regions, markers)
    assert [r["assigned_question"] for r in assigned] == ["Q1", "Q1"]


def test_assignment_carries_across_a_page_break():
    """The whole reason assignment is not a per-page operation: an answer
    started on page 1 continues onto page 2 with no marker of its own."""
    regions = [region(1, 300), region(2, 100), region(2, 500)]
    markers = [marker(1, 100, "numeric", "1"), marker(2, 400, "numeric", "2")]
    assigned = bs.assign_regions(regions, markers)
    assert [r["assigned_question"] for r in assigned] == ["Q1", "Q1", "Q2"]


def test_subpart_composes_with_the_last_numeric_marker():
    regions = [region(1, 200), region(1, 500)]
    markers = [marker(1, 100, "numeric", "2"), marker(1, 400, "subpart", "a")]
    assigned = bs.assign_regions(regions, markers)
    assert [r["assigned_question"] for r in assigned] == ["Q2", "Q2a"]


def test_region_before_any_marker_is_flagged_not_guessed():
    regions = [region(1, 50)]
    markers = [marker(1, 300, "numeric", "1")]
    assigned = bs.assign_regions(regions, markers)
    assert assigned[0]["assigned_question"] is None
    assert assigned[0]["unassigned_reason"] == "no_preceding_marker"
    assert assigned[0]["needs_review"] is True


def test_subpart_without_a_question_number_is_flagged():
    regions = [region(1, 300)]
    markers = [marker(1, 100, "subpart", "a")]
    assigned = bs.assign_regions(regions, markers)
    assert assigned[0]["assigned_question"] is None
    assert assigned[0]["unassigned_reason"] == "subpart_without_question"


def test_low_confidence_marker_flags_rather_than_assigns():
    regions = [region(1, 300)]
    markers = [marker(1, 100, "numeric", "1", confidence=0.10)]
    assigned = bs.assign_regions(regions, markers)
    assert assigned[0]["assigned_question"] is None
    assert assigned[0]["unassigned_reason"] == "low_confidence_marker"


def test_two_markers_at_the_same_height_are_ambiguous():
    regions = [region(1, 400)]
    markers = [marker(1, 100, "numeric", "1"),
               marker(1, 100 + bs.MARKER_AMBIGUITY_PX - 1, "numeric", "2")]
    assigned = bs.assign_regions(regions, markers)
    assert assigned[0]["assigned_question"] is None
    assert assigned[0]["unassigned_reason"] == "ambiguous_marker"


def test_a_region_that_is_just_the_marker_is_dropped():
    """The layout model boxes 'Q1.' as its own paragraph_title region; it is
    the marker, not an answer, so it must not become an answer_block."""
    marker_row = marker(1, 100, "numeric", "1", x=100, w=60, h=50)
    regions = [region(1, 100, x=100, w=60, h=50), region(1, 300)]
    assigned = bs.assign_regions(regions, [marker_row])
    assert len(assigned) == 1
    assert assigned[0]["bbox"][1] == 300


def test_assign_regions_does_not_mutate_its_input():
    regions = [region(1, 300)]
    original = json.loads(json.dumps(regions))
    bs.assign_regions(regions, [marker(1, 100, "numeric", "1")])
    assert regions == original


# --- classification mapping ---------------------------------------------

def test_every_mapped_label_lands_on_a_real_block_type():
    """block_type is an enum in migration 001; a typo here would only surface
    as a failed INSERT at ingestion time."""
    assert set(bs.LAYOUT_TO_BLOCK_TYPE.values()) <= {"text", "table", "diagram", "formula"}


def test_page_furniture_is_not_also_mapped():
    assert not (bs.IGNORED_LAYOUT_LABELS & set(bs.LAYOUT_TO_BLOCK_TYPE))


def test_formula_is_flagged_as_unsupported():
    """'formula' is a valid enum value but no plugin registers for it."""
    assert "formula" in bs.UNSUPPORTED_BLOCK_TYPES
    assert bs.LAYOUT_TO_BLOCK_TYPE["formula"] == "formula"


def test_heuristic_confidence_can_never_clear_the_review_threshold():
    assert bs.HEURISTIC_CONFIDENCE < bs.DEFAULT_MIN_CONFIDENCE


def test_stub_segment_needs_no_model_and_is_deterministic():
    first, second = bs.stub_segment(), bs.stub_segment()
    assert first == second
    assert {r["block_type"] for r in first} <= {"text", "table", "diagram", "formula"}


def test_classify_page_falls_back_when_the_model_is_unavailable(monkeypatch):
    """A missing/broken layout model must degrade to the flagged heuristic
    path, not take the booklet down."""
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (600, 800), "white")
    draw = ImageDraw.Draw(image)
    for index in range(5):
        draw.rectangle([60, 60 + index * 90, 540, 110 + index * 90], fill=(0, 0, 0))

    def boom():
        raise RuntimeError("no model here")
    monkeypatch.setattr(bs, "_get_layout_model", boom)

    regions = bs.classify_page(image)
    assert regions, "fallback produced no regions at all"
    assert all(r["segmenter"] == "heuristic" for r in regions)
    assert all(r["needs_review"] for r in regions)
    assert all(any("layout_model_unavailable" in reason
                   for reason in r["review_reasons"]) for r in regions)


# --- end-to-end (loads the real models) ---------------------------------

@pytest.mark.slow
@pytest.mark.skipif(not BOOKLET_PDF.exists(),
                    reason="run scripts/generate_booklet_benchmark.py first")
def test_end_to_end_matches_the_committed_ground_truth():
    from core import booklet_ingest

    truth = json.loads((BOOKLETS_DIR / "ground_truth.json").read_text())
    expected_questions = [
        r["question"] for page in truth["pages"] for r in page["regions"]
        if r["type"] != "marker"
    ]
    expected_types = [
        r["type"] for page in truth["pages"] for r in page["regions"]
        if r["type"] != "marker"
    ]

    ingested = booklet_ingest.ingest_booklet(
        BOOKLET_PDF, storage_mode="dummy", skip_denoise=True)
    segmented = bs.segment_booklet(ingested["pages"])
    regions = segmented["regions"]

    assert len(regions) == len(expected_questions)
    assert [r["assigned_question"] for r in regions] == expected_questions
    assert [r["block_type"] for r in regions] == expected_types
    assert len(segmented["markers"]) == 5
