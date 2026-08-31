"""Tests for core/table_extractor.py and core/plugins/table_extraction.py.

Split by cost, following this suite's existing convention:
  - the geometry tests (line/whitespace detection, header inference, grid
    building) run on the committed media/tables/ fixtures with NO OCR — the
    OCR ensemble is stubbed, so they test the part this module actually
    owns: where the grid boundaries are.
  - the full end-to-end extraction tests are marked `slow`, since they load
    PaddleOCR/Tesseract. `pytest -m "not slow"` skips them.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core import table_extractor as tx
from core.plugins.base import ExtractionResult
from core.plugins.table_extraction import TableExtractionPlugin, TableReference

TABLES_DIR = pathlib.Path(__file__).resolve().parent.parent / "media" / "tables"
IMAGES_DIR = TABLES_DIR / "images"

#: The fixture style used wherever one image is enough — the layout variant,
#: not the handwriting style, is what these tests vary.
STYLE = "PatrickHand"


@pytest.fixture(scope="module")
def ground_truth():
    return json.loads((TABLES_DIR / "ground_truth.json").read_text())


@pytest.fixture(scope="module")
def reference_table():
    return json.loads((TABLES_DIR / "reference_table.json").read_text())


def load_gray(name: str):
    import numpy as np
    from PIL import Image
    return np.array(Image.open(IMAGES_DIR / name).convert("L"))


class StubOCR:
    """Stands in for core/ocr_fallback.FallbackOCR, returning a fixed read
    per call so grid-geometry tests never load a model. Records how many
    crops it was handed, which is exactly the cell count."""

    def __init__(self, text="cell"):
        self.text = text
        self.crops = []

    def recognize(self, crop, **kwargs):
        self.crops.append(crop.size)

        class _Result:
            text = self.text
            confidence = 0.9
            engine = "stub"
        return _Result()


# ---------------------------------------------------------------------------
# Ruling-line detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant, expected_rows, expected_cols", [
    ("bordered", 5, 4),      # 4 data rows -> 5 horizontal lines; 3 cols -> 4 vertical
    ("shifted", 5, 4),
    ("missing_row", 4, 4),   # 3 data rows -> 4 horizontal lines
])
def test_detect_ruling_lines_finds_every_line(variant, expected_rows, expected_cols):
    rows, cols = tx.detect_ruling_lines(load_gray(f"{STYLE}_{variant}.png"))
    assert len(rows) == expected_rows
    assert len(cols) == expected_cols


def test_detect_ruling_lines_finds_nothing_on_a_borderless_table():
    """The borderless fallback is only correct if the ruled path reports
    honestly that it found nothing, rather than picking up rows of text."""
    rows, cols = tx.detect_ruling_lines(load_gray(f"{STYLE}_borderless.png"))
    assert len(rows) < 2 or len(cols) < 2


@pytest.mark.parametrize("style", ["PatrickHand", "Caveat", "IndieFlower"])
def test_ruling_line_detection_holds_across_handwriting_styles(style):
    rows, cols = tx.detect_ruling_lines(load_gray(f"{style}_bordered.png"))
    assert (len(rows), len(cols)) == (5, 4)


def test_perpendicular_dilation_is_load_bearing():
    """Documented in the module docstring as the fix without which drifting
    ruling lines are missed outright. Pinned so a future "simplification"
    that drops it fails here instead of silently losing rows."""
    assert tx.PERP_DILATE_PX >= 3


# ---------------------------------------------------------------------------
# Whitespace (borderless) fallback
# ---------------------------------------------------------------------------

def test_whitespace_grid_finds_the_borderless_layout():
    rows, cols = tx.detect_whitespace_grid(load_gray(f"{STYLE}_borderless.png"))
    assert len(rows) - 1 == 4      # boundaries bound 4 rows
    assert len(cols) - 1 == 3      # ...and 3 columns


def test_whitespace_column_gap_exceeds_the_row_gap():
    """Inter-WORD whitespace competes with inter-COLUMN whitespace on the x
    axis and nothing competes on the y axis — so a column gap threshold at
    or below the row one would split "Round Robin" into two columns."""
    assert tx.MIN_COL_GAP_PX > tx.MIN_ROW_GAP_PX


# ---------------------------------------------------------------------------
# Header inference
# ---------------------------------------------------------------------------

def cells_from(rows):
    return [{"row": r, "col": c, "text": t}
            for r, row in enumerate(rows) for c, t in enumerate(row)]


def test_header_inferred_from_non_numeric_row_over_numeric_body():
    has_header, reason = tx._infer_header(
        cells_from([["Algorithm", "Avg Wait"], ["FCFS", "8.0"]]), rows=2)
    assert has_header is True
    assert "numeric body" in reason


def test_numeric_first_row_is_data_not_a_header():
    has_header, reason = tx._infer_header(
        cells_from([["1", "8.0"], ["2", "5.5"]]), rows=2)
    assert has_header is False
    assert "numeric" in reason


def test_single_row_grid_has_no_header():
    has_header, _ = tx._infer_header(cells_from([["A", "B"]]), rows=1)
    assert has_header is False


def test_all_text_table_infers_a_header_but_says_it_is_weak():
    has_header, reason = tx._infer_header(
        cells_from([["Name", "Type"], ["FCFS", "Batch"]]), rows=2)
    assert has_header is True
    assert "weak" in reason


# ---------------------------------------------------------------------------
# Grid building (no OCR)
# ---------------------------------------------------------------------------

def test_build_grid_emits_a_dense_contiguous_grid(monkeypatch):
    from PIL import Image
    image = Image.open(IMAGES_DIR / f"{STYLE}_bordered.png").convert("RGB")
    rows, cols = tx.detect_ruling_lines(load_gray(f"{STYLE}_bordered.png"))

    ocr = StubOCR()
    cells, warnings = tx._build_grid(image, rows, cols, ocr)

    assert len(cells) == 12
    assert len(ocr.crops) == 12
    assert {(c["row"], c["col"]) for c in cells} == {(r, c) for r in range(4) for c in range(3)}
    assert all(c["ocr_engine"] == "stub" for c in cells)
    assert all(len(c["bbox"]) == 4 for c in cells)
    assert warnings == []


def test_unread_cells_produce_a_warning_that_forbids_the_blank_reading(monkeypatch):
    from PIL import Image
    image = Image.open(IMAGES_DIR / f"{STYLE}_bordered.png").convert("RGB")
    rows, cols = tx.detect_ruling_lines(load_gray(f"{STYLE}_bordered.png"))

    cells, warnings = tx._build_grid(image, rows, cols, StubOCR(text=""))

    assert all(c["text"] == "" and c["confidence"] == 0.0 for c in cells)
    assert any("NOT 'the student" in w for w in warnings)


def test_extract_returns_an_empty_table_rather_than_guessing(monkeypatch):
    """A blank image resolves to no grid on either path — the module must
    say so, not invent one."""
    from PIL import Image
    monkeypatch.setattr(tx, "_get_fallback_ocr", lambda: StubOCR())
    blank = Image.new("RGB", (400, 300), (255, 255, 255))
    result = tx.extract_table_structure(blank)

    assert result["rows"] == 0 and result["cols"] == 0
    assert result["cells"] == []
    assert any("rather than guessing" in w for w in result["extraction_warnings"])


def test_borderless_image_falls_through_to_whitespace_with_a_warning(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(tx, "_get_fallback_ocr", lambda: StubOCR())
    image = Image.open(IMAGES_DIR / f"{STYLE}_borderless.png").convert("RGB")
    result = tx.extract_table_structure(image)

    assert result["detection_method"] == "whitespace"
    assert (result["rows"], result["cols"]) == (4, 3)
    assert any("whitespace-projection path" in w for w in result["extraction_warnings"])


def test_stub_extract_is_deterministic_and_needs_no_source():
    first, second = tx.stub_extract(), tx.stub_extract("anything")
    assert first == second
    assert first["detection_method"] == "stub"
    assert all("[STUB]" in c["text"] or c["text"].replace(".", "").isdigit()
               for c in first["cells"])


# ---------------------------------------------------------------------------
# Full extraction — real OCR ensemble
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("variant", ["bordered", "borderless", "missing_row", "shifted"])
def test_real_extraction_matches_the_fixture_shape(variant, ground_truth):
    from PIL import Image
    expected = ground_truth[f"{STYLE}_{variant}.png"]
    result = tx.extract_table_structure(
        Image.open(IMAGES_DIR / f"{STYLE}_{variant}.png").convert("RGB"))

    assert (result["rows"], result["cols"]) == (expected["rows"], expected["cols"])
    assert result["has_header"] == expected["has_header"]
    assert result["detection_method"] == ("ruled" if expected["bordered"] else "whitespace")
    assert len(result["cells"]) == expected["rows"] * expected["cols"]


@pytest.mark.slow
def test_real_extraction_then_scoring_is_a_perfect_match(reference_table):
    """The fixture whose content IS the reference should score 1.0 all the
    way through — the end-to-end check that extraction and scoring agree on
    the schema between them."""
    from PIL import Image
    from core import table_evaluator

    student = tx.extract_table_structure(
        Image.open(IMAGES_DIR / f"{STYLE}_bordered.png").convert("RGB"))
    result = table_evaluator.compare_tables(student, reference_table)

    assert result["structural_score"] == 1.0
    assert result["content_score"] == 1.0
    assert result["overall_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# The plugin wrapper
# ---------------------------------------------------------------------------

def test_plugin_supports_only_tables():
    plugin = TableExtractionPlugin()
    assert plugin.supports("table") is True
    for other in ("text", "diagram", "formula"):
        assert plugin.supports(other) is False


def test_plugin_is_registered_for_the_table_block_type():
    from core.plugins import registry
    names = [p.name for p in registry.plugins_for("table")]
    assert "table_extraction" in names


def test_plugin_stub_paths_need_no_image_or_model(reference_table):
    plugin = TableExtractionPlugin()
    extracted = plugin.extract("dummy-storage/nothing/here.png", stub=True)
    assert isinstance(extracted, ExtractionResult)
    assert extracted.metrics["mode"] == "stub"

    result = plugin.evaluate(
        extracted, TableReference(table=reference_table, marks_max=10.0), stub=True)
    assert result.score == 7.0            # stub_compare's fixed 0.7 * marks_max
    assert result.metrics["evaluator_model"] == "stub"


def test_plugin_scales_overall_accuracy_to_marks(reference_table):
    """The one place a fraction becomes marks — core/table_evaluator.py
    deliberately never sees marks_max."""
    import copy
    plugin = TableExtractionPlugin()
    extracted = ExtractionResult(content=copy.deepcopy(reference_table),
                                 confidence=1.0, metrics={"mode": "test"})
    result = plugin.evaluate(extracted, TableReference(table=reference_table, marks_max=8.0))

    assert result.score == 8.0
    assert result.max_score == 8.0
    assert result.metrics["comparison"]["overall_accuracy"] == 1.0


def test_plugin_metrics_satisfy_the_persistence_contract(reference_table):
    """core/plugins/persistence.py refuses to write a row whose metrics are
    missing any of these, so a plugin that drops one can never persist."""
    import copy
    import json as json_mod
    from core.plugins.base import REQUIRED_METRIC_KEYS

    plugin = TableExtractionPlugin()
    extracted = ExtractionResult(content=copy.deepcopy(reference_table),
                                 confidence=1.0, metrics={"mode": "test"})
    result = plugin.evaluate(extracted, TableReference(table=reference_table, marks_max=5.0))

    for key in REQUIRED_METRIC_KEYS:
        assert key in result.metrics, key
    json_mod.dumps(result.metrics)   # must be JSONB-serializable (migration 010)


def test_plugin_caps_confidence_when_the_comparison_needs_review(reference_table):
    plugin = TableExtractionPlugin()
    unread = {
        **reference_table,
        "cells": [dict(c, text="" if (c["row"], c["col"]) == (1, 2) else c["text"])
                  for c in reference_table["cells"]],
    }
    extracted = ExtractionResult(content=unread, confidence=0.99, metrics={"mode": "test"})
    result = plugin.evaluate(extracted, TableReference(table=reference_table, marks_max=5.0))

    assert result.metrics["comparison"]["needs_review"] is True
    assert result.confidence <= 0.5
    assert "FLAGGED FOR REVIEW" in result.explanation
