"""Tests for core/table_evaluator.py — alignment, per-cell verdicts, and the
structural/content score split.

Fast: no DB, no OCR, no model load. That is the point of
core/table_evaluator.py being rules-and-difflib only (see its module
docstring) — the whole scoring path is unit-testable without any of the
heavy machinery the extraction side needs.
"""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from core import table_evaluator as te

REFERENCE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "media" / "tables" / "reference_table.json"
)


def grid_to_table(rows: list[list[str]], has_header: bool = True) -> dict:
    """Builds the extractor's grid schema from a plain list-of-lists, so a
    test reads as the table it is testing."""
    return {
        "rows": len(rows), "cols": len(rows[0]) if rows else 0,
        "has_header": has_header,
        "cells": [{"row": r, "col": c, "text": text}
                  for r, row in enumerate(rows) for c, text in enumerate(row)],
    }


@pytest.fixture
def reference_table():
    return json.loads(REFERENCE_PATH.read_text())


# ---------------------------------------------------------------------------
# compare_cells — the verdict precedence ladder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reference, student, verdict", [
    ("FCFS", "FCFS", "exact"),
    ("FCFS", "  fcfs  ", "exact"),                 # normalized, case/space-insensitive
    ("Round Robin", "Round   Robin", "exact"),      # internal whitespace collapsed
    ("Preemptive", "Preemtive", "fuzzy"),           # dropped character
    ("SJF", "SUF", "fuzzy"),                        # short-string edit-distance path
    ("8.0", "8", "exact"),                          # numerically equal
    ("6.2", "6.25", "numeric_close"),               # within relative tolerance
    ("8.0", "8.5", "miss"),                         # outside tolerance
    ("No", "Yes", "miss"),
    ("8.0", "", "not_extracted"),                   # nothing read, not "blank"
])
def test_compare_cells_verdicts(reference, student, verdict):
    assert te.compare_cells(reference, student)[0] == verdict


def test_numeric_cells_skip_fuzzy_matching():
    """The module's headline rule: difflib rates "10.0"/"10.5" at 0.75, which
    clears CELL_FUZZY_THRESHOLD — so a wrong number must NOT be allowed to
    reach the fuzzy stage and score as a near-match on spelling."""
    assert te.text_match.fuzzy_ratio("10.0", "10.5") >= te.CELL_FUZZY_THRESHOLD
    verdict, credit, _ = te.compare_cells("10.0", "10.5")
    assert verdict == "miss"
    assert credit == 0.0


def test_credit_matches_the_published_table():
    for verdict, expected in te.CREDIT_BY_VERDICT.items():
        assert 0.0 <= expected <= 1.0, verdict
    assert te.CREDIT_BY_VERDICT["exact"] > te.CREDIT_BY_VERDICT["fuzzy"]
    assert te.CREDIT_BY_VERDICT["fuzzy"] > te.CREDIT_BY_VERDICT["numeric_close"]
    assert te.CREDIT_BY_VERDICT["numeric_close"] > te.CREDIT_BY_VERDICT["miss"]


def test_parse_number_rejects_non_plain_numbers():
    assert te.parse_number("1,234") == 1234.0
    assert te.parse_number("8.0") == 8.0
    for text in ("~5", "5 ms", "n/a", "", "yes"):
        assert te.parse_number(text) is None, text


# ---------------------------------------------------------------------------
# compare_tables — perfect match
# ---------------------------------------------------------------------------

def test_perfect_match_scores_one(reference_table):
    result = te.compare_tables(copy.deepcopy(reference_table), reference_table)

    assert result["overall_accuracy"] == 1.0
    assert result["structural_score"] == 1.0
    assert result["content_score"] == 1.0
    assert result["content_score_on_aligned"] == 1.0
    assert result["verdict_counts"]["exact"] == 12
    assert result["missing_rows"] == [] and result["extra_rows"] == []
    assert result["missing_cols"] == [] and result["extra_cols"] == []
    assert result["needs_review"] is False
    assert all(v == "exact" for row in result["verdict_grid"] for v in row)


# ---------------------------------------------------------------------------
# compare_tables — row alignment: rows are NOT matched index-for-index
# ---------------------------------------------------------------------------

def test_reordered_body_rows_align_by_leading_cell(reference_table):
    """The whole reason this module exists: the student wrote SJF before
    FCFS, so index-for-index comparison would mark four cells wrong."""
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["SJF", "No", "5.5"],
        ["FCFS", "No", "8.0"],
        ["Round Robin", "Yes", "6.2"],
    ])
    result = te.compare_tables(student, reference_table)

    assert result["content_score"] == 1.0
    assert result["structural_score"] == 1.0
    pairs = {p["reference_row"]: p["student_row"] for p in result["row_alignment"]["pairs"]}
    assert pairs == {0: 0, 1: 2, 2: 1, 3: 3}   # FCFS(ref 1) -> student row 2


def test_missing_row_is_reported_and_splits_the_two_content_scores(reference_table):
    """content_score counts cells lost to a missing row as zero;
    content_score_on_aligned doesn't. The GAP between them is the signal
    that the shape, not the values, is what went wrong."""
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["FCFS", "No", "8.0"],
        ["Round Robin", "Yes", "6.2"],
    ])
    result = te.compare_tables(student, reference_table)

    assert [r["leading_cell"] for r in result["missing_rows"]] == ["SJF"]
    assert result["extra_rows"] == []
    assert result["verdict_counts"]["missing"] == 3
    assert result["content_score_on_aligned"] == 1.0
    assert result["content_score"] == pytest.approx(9 / 12)
    assert result["content_score"] < result["content_score_on_aligned"]
    assert result["structural_score"] < 1.0


def test_extra_row_is_reported_and_costs_structural_precision(reference_table):
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["FCFS", "No", "8.0"],
        ["SJF", "No", "5.5"],
        ["Round Robin", "Yes", "6.2"],
        ["Priority", "Yes", "7.1"],
    ])
    result = te.compare_tables(student, reference_table)

    assert [r["leading_cell"] for r in result["extra_rows"]] == ["Priority"]
    assert result["missing_rows"] == []
    assert result["content_score"] == 1.0          # every reference cell is right
    assert result["row_alignment"]["precision"] < 1.0
    assert result["structural_score"] < 1.0        # ...but the shape isn't


# ---------------------------------------------------------------------------
# compare_tables — column alignment by header
# ---------------------------------------------------------------------------

def test_reordered_columns_align_by_header_text(reference_table):
    student = grid_to_table([
        ["Avg Wait", "Algorithm", "Preemptive"],
        ["8.0", "FCFS", "No"],
        ["5.5", "SJF", "No"],
        ["6.2", "Round Robin", "Yes"],
    ])
    result = te.compare_tables(student, reference_table)

    pairs = {p["reference_col"]: p["student_col"] for p in result["column_alignment"]["pairs"]}
    assert pairs == {0: 1, 1: 2, 2: 0}
    assert result["content_score"] == 1.0
    assert result["column_alignment"]["method"].startswith("header text")


def test_missing_column_is_reported(reference_table):
    student = grid_to_table([
        ["Algorithm", "Avg Wait"],
        ["FCFS", "8.0"],
        ["SJF", "5.5"],
        ["Round Robin", "6.2"],
    ])
    result = te.compare_tables(student, reference_table)

    assert [c["header"] for c in result["missing_cols"]] == ["Preemptive"]
    assert result["extra_cols"] == []
    assert result["verdict_counts"]["missing"] == 4   # the whole Preemptive column


def test_headerless_tables_align_positionally(reference_table):
    headerless_reference = dict(reference_table, has_header=False)
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["FCFS", "No", "8.0"],
        ["SJF", "No", "5.5"],
        ["Round Robin", "Yes", "6.2"],
    ], has_header=False)
    result = te.compare_tables(student, headerless_reference)

    assert result["column_alignment"]["method"].startswith("positional")
    assert result["content_score"] == 1.0


def test_header_disagreement_flags_review(reference_table):
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["FCFS", "No", "8.0"],
        ["SJF", "No", "5.5"],
        ["Round Robin", "Yes", "6.2"],
    ], has_header=False)
    result = te.compare_tables(student, reference_table)

    assert result["needs_review"] is True
    assert any("header inference disagrees" in r for r in result["review_reasons"])


# ---------------------------------------------------------------------------
# compare_tables — review routing and edge cases
# ---------------------------------------------------------------------------

def test_unread_cell_flags_review_rather_than_asserting_a_blank(reference_table):
    """An empty extracted cell means 'nothing was reliably read', not 'the
    student left it blank' — it scores zero but must set needs_review."""
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["FCFS", "No", ""],
        ["SJF", "No", "5.5"],
        ["Round Robin", "Yes", "6.2"],
    ])
    result = te.compare_tables(student, reference_table)

    assert result["verdict_counts"]["not_extracted"] == 1
    assert result["needs_review"] is True
    assert any("not confirmed blank" in r.lower() for r in result["review_reasons"])


def test_empty_student_table_scores_zero_without_raising(reference_table):
    empty = {"rows": 0, "cols": 0, "has_header": False, "cells": []}
    result = te.compare_tables(empty, reference_table)

    assert result["overall_accuracy"] == 0.0
    assert result["content_score"] == 0.0
    assert result["verdict_counts"]["missing"] == 12
    assert len(result["missing_rows"]) == 4
    assert len(result["missing_cols"]) == 3


def test_overall_accuracy_is_the_published_weighted_blend(reference_table):
    student = grid_to_table([
        ["Algorithm", "Preemptive", "Avg Wait"],
        ["FCFS", "No", "8.0"],
        ["Round Robin", "Yes", "6.2"],
    ])
    result = te.compare_tables(student, reference_table)

    expected = (te.CONTENT_WEIGHT * result["content_score"]
                + te.STRUCTURAL_WEIGHT * result["structural_score"])
    assert result["overall_accuracy"] == pytest.approx(expected, abs=5e-5)


def test_verdict_grid_matches_the_reference_shape_and_cell_verdicts(reference_table):
    student = grid_to_table([
        ["Algorithm", "Preemtive", "Avg Wait"],
        ["FCFS", "No", "8.5"],
        ["SJF", "No", "5.5"],
        ["Round Robin", "Yes", "6.25"],
    ])
    result = te.compare_tables(student, reference_table)

    assert len(result["verdict_grid"]) == reference_table["rows"]
    assert all(len(row) == reference_table["cols"] for row in result["verdict_grid"])
    for cell in result["cell_verdicts"]:
        assert result["verdict_grid"][cell["reference_row"]][cell["reference_col"]] == cell["verdict"]
    # This student hits all four "wrote something" verdicts at once.
    counts = result["verdict_counts"]
    assert counts["exact"] == 9 and counts["fuzzy"] == 1
    assert counts["numeric_close"] == 1 and counts["miss"] == 1


def test_stub_compare_is_deterministic_and_shaped_like_a_real_result(reference_table):
    first = te.stub_compare({}, reference_table)
    second = te.stub_compare({}, reference_table)
    assert first == second
    assert first["overall_accuracy"] == 0.7
    assert set(te.compare_tables(copy.deepcopy(reference_table), reference_table)) <= set(first)
