"""
core/table_evaluator.py — compares an extracted (handwritten/scanned) table
grid against a reference (hand-authored) one, the table-side counterpart to
core/diagram_evaluator.py.

Both grids must be in the shape documented in core/table_extractor.py's
module docstring:
    {"rows": int, "cols": int, "has_header": bool,
     "cells": [{"row": int, "col": int, "text": str, ...}, ...]}
A reference carries no bbox/confidence (it is authored, not extracted —
scripts/load_reference_table.py); everything here reads only row/col/text,
so both sides go through the same code path.

RULES + STRING NLP ONLY, NO LLM — the same v1 posture, for the same reason,
as core/diagram_evaluator.py: scoring must be deterministic and explainable,
so a teacher overriding a per-cell verdict can see exactly which rule
produced it. Every fuzzy comparison delegates to core/text_match.py, the
shared helper that already backs diagram glossary matching and the text
plugin's keyword/rubric coverage.

  DELIBERATE DIFFERENCE FROM core/diagram_evaluator.py: that module matches
  nodes with sentence-transformer EMBEDDINGS, because diagram labels can be
  semantically equivalent while textually unrelated. Table cells are not
  like that — a cell is a short controlled token (a header name, a value, a
  yes/no) where the realistic failure is OCR noise, not synonymy. So cell
  and header matching uses difflib string similarity only, exactly the
  argument core/diagram_evaluator.match_glossary() already makes for
  glossary matching being difflib rather than embeddings. The practical
  payoff: no model load, so table scoring runs without
  sentence-transformers installed at all.

ALIGNMENT — the reason this module exists rather than just zipping the two
grids. A student's row 3 is not the reference's row 3: rows shift when a row
is skipped, and rows get written in a different order. Two independent
passes, headers first:

  1. COLUMNS, by header. When both sides report has_header, each reference
     header cell is matched to a student header cell by fuzzy text, greedy
     best-first (highest-scoring pair claimed first, each column used at
     most once) — the same greedy-not-optimal choice, and the same
     justification, as core/diagram_evaluator._match_nodes(): at realistic
     table widths greedy and an optimal assignment essentially never
     disagree, and greedy needs no scipy. Columns whose headers don't match
     anything fall back to POSITIONAL pairing among the leftovers, so a
     column with an unreadable header still participates instead of being
     written off as missing. When either side has no header, alignment is
     positional throughout.

  2. ROWS, by leading cell. Body rows are matched on the cell in their
     LEADING COLUMN — the reference's column 0 and whichever student column
     aligned to it — because that is the column that names the row in
     essentially every exam table ("FCFS", "SJF", ...). Greedy best-first
     again. Unmatched reference rows are missing_rows; unmatched student
     rows are extra_rows. The header rows, when both sides have one, are
     aligned to each other directly and never enter this pass.

PER-CELL VERDICTS, in precedence order:

    exact          normalized text identical                     credit 1.0
    fuzzy          clears core/text_match.py's similarity bar     credit 0.9
    numeric_close  both parse as numbers, within tolerance        credit 0.5
    miss           an aligned student cell exists and matches
                   none of the above                              credit 0.0
    not_extracted  an aligned student cell exists but is EMPTY    credit 0.0
    missing        no aligned student cell at all (its row or
                   column is absent from the student table)       credit 0.0

  NUMERIC CELLS SKIP THE FUZZY STAGE. String similarity is meaningless
  between numbers and actively wrong here: difflib rates "10.0" vs "10.5" at
  0.75, which clears the default fuzzy threshold, so a genuinely wrong
  answer would score as a near-match on spelling. When either side parses as
  a number, the comparison goes straight from exact to numeric tolerance.
  This is a refinement of "exact > fuzzy > numeric > miss", not a departure
  from it — fuzzy simply has nothing to say about numbers.

  `fuzzy` is credited 0.9 rather than 1.0 because the rule cannot tell an
  OCR misread (not the student's fault, deserves full credit) from a real
  misspelling (the student's, deserves less). `numeric_close` is credited
  0.5 because a rounding-level difference is a partially-correct value, not
  a correct one. Both are tuning knobs a rubric owner may reasonably
  disagree with — they are constants below, not buried literals.

  `not_extracted` scores 0.0 like a miss BUT is counted separately and sets
  needs_review, because an empty extracted cell means "nothing was reliably
  read here", not "the student left it blank" (core/table_extractor.py's
  module docstring, same convention as an undetected diagram edge). A run
  with not_extracted cells is a review candidate, not a final score.

STRUCTURAL VS CONTENT, reported separately (the caller's own requirement,
and the distinction a teacher actually needs — "wrote the wrong shape" and
"wrote the wrong numbers" are different failures deserving different
feedback):

    structural_score  did they get the SHAPE right — the mean of a row F1
                      and a column F1 over the alignment above, so both a
                      missing row and a spurious extra one cost. Mirrors
                      core/diagram_evaluator.py's node/edge F1 scoring.
    content_score     did they get the VALUES right — mean per-cell credit
                      over EVERY reference cell, so cells lost to a missing
                      row count as zero content. This is the honest headline
                      number: a student who omitted half the table did not
                      get the content right.
    content_score_on_aligned
                      mean credit over only the cells that HAD an aligned
                      student cell. Reported alongside because the pair
                      separates the two failure modes: content_score well
                      below content_score_on_aligned means "shape problem,
                      the values that were written were fine".

    overall_accuracy  CONTENT_WEIGHT * content + STRUCTURAL_WEIGHT * structural.
                      Content-weighted, because the values are what is being
                      examined and the shape is the scaffold. Same weighted-
                      blend shape as core/diagram_evaluator.compare_diagrams()'s
                      0.6*node_f1 + 0.4*edge_f1.

Unlike core/diagram_evaluator.compare_diagrams(), this returns FRACTIONS
(0.0-1.0) and takes no marks_max: scaling a fraction to a question's marks
is the plugin's job (core/plugins/table_extraction.py), which keeps this
module free of any notion of marks and directly unit-testable without one.
"""
from __future__ import annotations

from core import text_match

SCHEMA_VERSION = 1

#: Similarity bar for a non-numeric cell/header fuzzy match. Matches
#: core/text_match.DEFAULT_FUZZY_THRESHOLD and core/diagram_evaluator's
#: GLOSSARY_FUZZY_THRESHOLD — the noise being tolerated is the same OCR
#: noise, so the bar is the same.
CELL_FUZZY_THRESHOLD = 0.75

#: Short cells ("SJF", "No", "8") are punished disproportionately by a ratio
#: threshold — one misread character on a 3-letter cell drops difflib to
#: 0.667. Same absolute-edit-distance escape hatch, same values, as
#: core/diagram_evaluator.SHORT_LABEL_MAX_LEN/SHORT_LABEL_MAX_EDITS.
SHORT_CELL_MAX_LEN = 6
SHORT_CELL_MAX_EDITS = 1

#: Credit awarded per verdict. See the module docstring for why fuzzy and
#: numeric_close are worth less than a full match.
EXACT_CREDIT = 1.0
FUZZY_CREDIT = 0.9
NUMERIC_CLOSE_CREDIT = 0.5

#: A numeric cell counts as numeric_close when it is within EITHER of these
#: of the reference value. The relative term carries most cases (it scales
#: with magnitude, so 2% is 2% whether the value is 6.2 or 6200); the
#: absolute term keeps values near zero from needing impossible precision.
NUMERIC_REL_TOLERANCE = 0.02
NUMERIC_ABS_TOLERANCE = 0.05

#: overall_accuracy blend. Content-weighted — see the module docstring.
CONTENT_WEIGHT = 0.6
STRUCTURAL_WEIGHT = 0.4

CREDIT_BY_VERDICT = {
    "exact": EXACT_CREDIT,
    "fuzzy": FUZZY_CREDIT,
    "numeric_close": NUMERIC_CLOSE_CREDIT,
    "miss": 0.0,
    "not_extracted": 0.0,
    "missing": 0.0,
}


def normalize(text: str) -> str:
    """Case-folded, whitespace-collapsed cell text. Collapsing INTERNAL
    whitespace matters here in a way it doesn't for a single-word diagram
    label: OCR routinely returns "Round  Robin" or "Round\\nRobin" for a
    two-word cell, and neither should read as different from "Round Robin"."""
    return " ".join((text or "").split()).lower()


def parse_number(text: str) -> float | None:
    """The cell's numeric value, or None if it isn't a number. Thousands
    separators are stripped; anything else (units, "n/a", "~5") is NOT a
    number, deliberately — a lenient parser here would silently route
    non-numeric cells into numeric tolerance and away from fuzzy matching,
    which is the wrong comparison for them."""
    stripped = (text or "").strip().replace(",", "")
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        return None


def _text_similarity(a: str, b: str) -> tuple[float, str | None]:
    """(score, match_type) for two normalized strings, using the shared
    core/text_match.py primitives — exact, else the short-string edit
    distance escape hatch, else the ratio threshold. match_type is None
    when nothing clears the bar."""
    if not a and not b:
        return 1.0, "exact"
    if not a or not b:
        return 0.0, None
    if a == b:
        return 1.0, "exact"
    if (max(len(a), len(b)) <= SHORT_CELL_MAX_LEN
            and text_match.edit_distance(a, b) <= SHORT_CELL_MAX_EDITS):
        return text_match.fuzzy_ratio(a, b), "fuzzy"
    ratio = text_match.fuzzy_ratio(a, b)
    if ratio >= CELL_FUZZY_THRESHOLD:
        return ratio, "fuzzy"
    return ratio, None


def compare_cells(reference_text: str, student_text: str) -> tuple[str, float, str]:
    """One cell pair -> (verdict, credit, detail). See the module docstring
    for the precedence order and why numeric cells skip fuzzy matching."""
    ref_norm, stu_norm = normalize(reference_text), normalize(student_text)

    if not stu_norm and ref_norm:
        return "not_extracted", 0.0, "no text was read from the aligned student cell"
    if ref_norm == stu_norm:
        return "exact", EXACT_CREDIT, "exact match"

    ref_number, stu_number = parse_number(ref_norm), parse_number(stu_norm)
    if ref_number is not None or stu_number is not None:
        if ref_number is None or stu_number is None:
            return "miss", 0.0, (
                f"one side is numeric and the other is not "
                f"({reference_text!r} vs {student_text!r})"
            )
        if ref_number == stu_number:
            return "exact", EXACT_CREDIT, f"numerically equal ({ref_number})"
        difference = abs(ref_number - stu_number)
        tolerance = max(abs(ref_number) * NUMERIC_REL_TOLERANCE, NUMERIC_ABS_TOLERANCE)
        if difference <= tolerance:
            return "numeric_close", NUMERIC_CLOSE_CREDIT, (
                f"within numeric tolerance (|{ref_number} - {stu_number}| = "
                f"{round(difference, 6)} <= {round(tolerance, 6)})"
            )
        return "miss", 0.0, (
            f"numeric value differs by {round(difference, 6)}, outside the "
            f"{round(tolerance, 6)} tolerance"
        )

    score, match_type = _text_similarity(ref_norm, stu_norm)
    if match_type == "fuzzy":
        return "fuzzy", FUZZY_CREDIT, f"fuzzy text match (ratio {round(score, 4)})"
    return "miss", 0.0, f"no match (best ratio {round(score, 4)})"


def _greedy_align(reference_keys: list[tuple[int, str]],
                   student_keys: list[tuple[int, str]]) -> dict[int, int]:
    """Greedy best-first alignment of two lists of (index, text) by text
    similarity — highest-scoring pair claimed first, each index used at most
    once. Returns {reference_index: student_index}. Same greedy-not-optimal
    trade-off as core/diagram_evaluator._match_nodes()."""
    candidates = []
    for ref_index, ref_text in reference_keys:
        for stu_index, stu_text in student_keys:
            score, match_type = _text_similarity(normalize(ref_text), normalize(stu_text))
            if match_type is not None:
                candidates.append((score, ref_index, stu_index))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))

    mapping: dict[int, int] = {}
    claimed: set[int] = set()
    for _score, ref_index, stu_index in candidates:
        if ref_index in mapping or stu_index in claimed:
            continue
        mapping[ref_index] = stu_index
        claimed.add(stu_index)
    return mapping


def _cell_lookup(table: dict) -> dict[tuple[int, int], str]:
    return {(c["row"], c["col"]): c.get("text", "") for c in table.get("cells", [])}


def _align_columns(reference: dict, student: dict,
                    ref_cells: dict, stu_cells: dict) -> tuple[dict[int, int], str]:
    """Returns ({reference_col: student_col}, method). Header-matched first,
    with positional pairing of whatever is left over — see the module
    docstring's alignment section."""
    ref_cols = list(range(reference.get("cols", 0)))
    stu_cols = list(range(student.get("cols", 0)))

    if not (reference.get("has_header") and student.get("has_header")):
        mapping = {c: c for c in ref_cols if c in stu_cols}
        return mapping, "positional (one or both tables report no header row)"

    mapping = _greedy_align(
        [(c, ref_cells.get((0, c), "")) for c in ref_cols],
        [(c, stu_cells.get((0, c), "")) for c in stu_cols],
    )
    method = "header text"

    # Leftover columns pair positionally among themselves rather than being
    # written off: an unreadable header shouldn't cost its whole column.
    unmatched_ref = [c for c in ref_cols if c not in mapping]
    unmatched_stu = [c for c in stu_cols if c not in set(mapping.values())]
    if unmatched_ref and unmatched_stu:
        for ref_col, stu_col in zip(unmatched_ref, unmatched_stu):
            mapping[ref_col] = stu_col
        method = "header text, with unmatched columns paired positionally"

    return mapping, method


def _align_rows(reference: dict, student: dict, ref_cells: dict, stu_cells: dict,
                 leading_ref_col: int, leading_stu_col: int | None) -> tuple[dict[int, int], str]:
    """Returns ({reference_row: student_row}, method). Header rows (row 0)
    are paired directly when both sides have one; body rows are matched on
    their leading cell."""
    ref_has_header = bool(reference.get("has_header"))
    stu_has_header = bool(student.get("has_header"))
    both_header = ref_has_header and stu_has_header

    ref_rows = list(range(reference.get("rows", 0)))
    stu_rows = list(range(student.get("rows", 0)))
    ref_body = [r for r in ref_rows if not (both_header and r == 0)]
    stu_body = [r for r in stu_rows if not (both_header and r == 0)]

    if leading_stu_col is None:
        mapping = {r: r for r in ref_rows if r in stu_rows}
        return mapping, ("positional (the reference's leading column has no "
                         "aligned student column to match row names against)")

    mapping = _greedy_align(
        [(r, ref_cells.get((r, leading_ref_col), "")) for r in ref_body],
        [(r, stu_cells.get((r, leading_stu_col), "")) for r in stu_body],
    )
    if both_header:
        mapping[0] = 0
    return mapping, "leading cell text" + (" (header rows paired directly)" if both_header else "")


def _f1(precision: float, recall: float) -> float:
    """Harmonic mean, 0.0 when both are 0 — identical helper and identical
    guard to core/diagram_evaluator._f1()."""
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _axis_scores(matched: int, reference_count: int, student_count: int) -> dict:
    recall = matched / reference_count if reference_count else 1.0
    precision = (matched / student_count) if student_count else (1.0 if not reference_count else 0.0)
    return {
        "matched": matched,
        "reference": reference_count,
        "student": student_count,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(_f1(precision, recall), 4),
    }


def compare_tables(student: dict, reference: dict) -> dict:
    """Compares one extracted student table against one reference table.
    Returns the full comparison dict — the whole thing is what callers store
    in evaluation_results.metrics, the same way
    core/diagram_evaluator.compare_diagrams()'s result is stored wholesale.

    Both arguments are the grid shape documented in core/table_extractor.py.
    Scores are FRACTIONS (0.0-1.0); scaling to a question's marks is the
    caller's job (see the module docstring).
    """
    ref_cells = _cell_lookup(reference)
    stu_cells = _cell_lookup(student)
    ref_rows_n = reference.get("rows", 0)
    ref_cols_n = reference.get("cols", 0)
    stu_rows_n = student.get("rows", 0)
    stu_cols_n = student.get("cols", 0)

    col_map, col_method = _align_columns(reference, student, ref_cells, stu_cells)
    leading_ref_col = 0
    leading_stu_col = col_map.get(leading_ref_col)
    row_map, row_method = _align_rows(
        reference, student, ref_cells, stu_cells, leading_ref_col, leading_stu_col
    )

    def header_text(col: int, cells: dict, has_header: bool) -> str | None:
        return cells.get((0, col), "") if has_header else None

    missing_cols = [
        {"reference_col": c, "header": header_text(c, ref_cells, bool(reference.get("has_header")))}
        for c in range(ref_cols_n) if c not in col_map
    ]
    claimed_cols = set(col_map.values())
    extra_cols = [
        {"student_col": c, "header": header_text(c, stu_cells, bool(student.get("has_header")))}
        for c in range(stu_cols_n) if c not in claimed_cols
    ]
    missing_rows = [
        {"reference_row": r, "leading_cell": ref_cells.get((r, leading_ref_col), "")}
        for r in range(ref_rows_n) if r not in row_map
    ]
    claimed_rows = set(row_map.values())
    extra_rows = [
        {"student_row": r,
         "leading_cell": stu_cells.get((r, leading_stu_col if leading_stu_col is not None else 0), "")}
        for r in range(stu_rows_n) if r not in claimed_rows
    ]

    cell_verdicts = []
    verdict_grid = []
    verdict_counts = {name: 0 for name in CREDIT_BY_VERDICT}
    total_credit = 0.0
    aligned_credit = 0.0
    aligned_count = 0

    for ref_row in range(ref_rows_n):
        grid_row = []
        for ref_col in range(ref_cols_n):
            reference_text = ref_cells.get((ref_row, ref_col), "")
            stu_row = row_map.get(ref_row)
            stu_col = col_map.get(ref_col)

            if stu_row is None or stu_col is None:
                absent = "row" if stu_row is None else "column"
                verdict, credit, detail = "missing", 0.0, (
                    f"the student table has no {absent} aligned to reference "
                    f"{absent} {ref_row if absent == 'row' else ref_col}"
                )
                student_text = None
            else:
                student_text = stu_cells.get((stu_row, stu_col), "")
                verdict, credit, detail = compare_cells(reference_text, student_text)
                aligned_credit += credit
                aligned_count += 1

            total_credit += credit
            verdict_counts[verdict] += 1
            grid_row.append(verdict)
            cell_verdicts.append({
                "reference_row": ref_row, "reference_col": ref_col,
                "student_row": stu_row, "student_col": stu_col,
                "reference_text": reference_text, "student_text": student_text,
                "verdict": verdict, "credit": credit, "detail": detail,
            })
        verdict_grid.append(grid_row)

    reference_cell_count = ref_rows_n * ref_cols_n
    content_score = total_credit / reference_cell_count if reference_cell_count else 0.0
    content_on_aligned = aligned_credit / aligned_count if aligned_count else 0.0

    row_scores = _axis_scores(len(row_map), ref_rows_n, stu_rows_n)
    col_scores = _axis_scores(len(col_map), ref_cols_n, stu_cols_n)
    structural_score = (row_scores["f1"] + col_scores["f1"]) / 2

    overall = CONTENT_WEIGHT * content_score + STRUCTURAL_WEIGHT * structural_score

    review_reasons = []
    if verdict_counts["not_extracted"]:
        review_reasons.append(
            f"{verdict_counts['not_extracted']} aligned cell(s) had no text extracted — "
            f"scored 0 but NOT confirmed blank; a human should check the scan before "
            f"this score is final."
        )
    if reference.get("has_header") != student.get("has_header"):
        review_reasons.append(
            f"header inference disagrees (reference has_header="
            f"{reference.get('has_header')}, student has_header="
            f"{student.get('has_header')}) — columns were aligned "
            f"{col_method}, which may be wrong."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "overall_accuracy": round(overall, 4),
        "structural_score": round(structural_score, 4),
        "content_score": round(content_score, 4),
        "content_score_on_aligned": round(content_on_aligned, 4),
        "verdict_grid": verdict_grid,
        "cell_verdicts": cell_verdicts,
        "verdict_counts": verdict_counts,
        "missing_rows": missing_rows,
        "extra_rows": extra_rows,
        "missing_cols": missing_cols,
        "extra_cols": extra_cols,
        "row_alignment": {
            "method": row_method,
            "pairs": [{"reference_row": r, "student_row": s} for r, s in sorted(row_map.items())],
            **row_scores,
        },
        "column_alignment": {
            "method": col_method,
            "pairs": [{"reference_col": r, "student_col": s} for r, s in sorted(col_map.items())],
            **col_scores,
        },
        "structure": {
            "reference_rows": ref_rows_n, "reference_cols": ref_cols_n,
            "student_rows": stu_rows_n, "student_cols": stu_cols_n,
            "reference_has_header": bool(reference.get("has_header")),
            "student_has_header": bool(student.get("has_header")),
        },
        "needs_review": bool(review_reasons),
        "review_reasons": review_reasons,
        "weights": {"content": CONTENT_WEIGHT, "structural": STRUCTURAL_WEIGHT},
        "model": {
            "matching_method": "rules+fuzzy",
            "fuzzy_threshold": CELL_FUZZY_THRESHOLD,
            "numeric_tolerance": {"relative": NUMERIC_REL_TOLERANCE,
                                   "absolute": NUMERIC_ABS_TOLERANCE},
        },
    }


def stub_compare(student: dict, reference: dict) -> dict:
    """Deterministic fake comparison, matching the --stub convention in
    core/diagram_evaluator.stub_compare() and core/evaluator.py. Returns a
    fixed 70% with empty detail, to exercise the DB-write path without
    running alignment."""
    return {
        "schema_version": SCHEMA_VERSION,
        "overall_accuracy": 0.7,
        "structural_score": 0.7,
        "content_score": 0.7,
        "content_score_on_aligned": 0.7,
        "verdict_grid": [],
        "cell_verdicts": [],
        "verdict_counts": {name: 0 for name in CREDIT_BY_VERDICT},
        "missing_rows": [], "extra_rows": [], "missing_cols": [], "extra_cols": [],
        "row_alignment": {"method": "stub", "pairs": []},
        "column_alignment": {"method": "stub", "pairs": []},
        "structure": {},
        "needs_review": False,
        "review_reasons": [],
        "weights": {"content": CONTENT_WEIGHT, "structural": STRUCTURAL_WEIGHT},
        "model": {"matching_method": "stub"},
    }
