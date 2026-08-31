#!/usr/bin/env python3
"""
scripts/evaluate_table_answer.py — score one student's table answer_block
against a reference table (a content_assets row).

Mirrors scripts/evaluate_diagram_answer.py, with one deliberate structural
difference: the ledger write goes through
core/plugins/persistence.write_evaluation_result() rather than calling
core/answer_evaluation.record_evaluation() directly. persistence.py is the
one write path built for plugin-produced scores — it enforces migration
012's reference XOR in Python before any SQL runs, enforces
core/plugins/base.REQUIRED_METRIC_KEYS so the row can be traced back to the
plugin version that produced it, and turns a silent RLS miss into a loud
RLSVisibilityError instead of an orphaned row. evaluate_diagram_answer.py
predates that module; new plugin-backed scripts should use it.

BEHAVIOUR:
  1. Fetches the answer_block's blob_url (block_type must be 'table').
  2. Extracts its grid via the 'table_extraction' plugin
     (core/table_extractor.py — ruling-line morphology with a borderless
     whitespace fallback, OCR'd through core/ocr_fallback.py's existing
     multi-engine ensemble). --stub-extraction skips this for
     dummy-storage test data with no real image behind it.
  3. Fetches the reference asset's structured_data (content_assets,
     asset_type='table').
  4. Scores via core/table_evaluator.compare_tables() — rules + fuzzy
     matching only, no LLM, no embeddings — or stub_compare() with --stub.
  5. Transitions answers.status pending_evaluation -> ai_scored (with an
     answer_status_history row), then appends the evaluation_results row
     with reference_asset_id set (NOT reference_answer_variant_id — see
     migration 012). The status transition is issued FIRST, on the same
     transaction, because write_evaluation_result() owns the commit: both
     land together or neither does.

  The trigger trg_evaluation_reference_matches_answer_question (migration
  012) enforces that the reference asset is linked to the same question as
  the answer via question_asset_links(role='question_source') — no
  application-level check is needed here, but note it means an asset loaded
  by load_reference_table.py WITHOUT --question-id cannot be scored against.

  RLS: same as evaluate_diagram_answer.py — a system-level operation, so it
  sets app.is_platform_admin = 'true' on the transaction. This script owns
  the connection directly (rather than using core/db.py's transaction()
  helper) because write_evaluation_result() takes a connection and does its
  own commit/rollback, including the dry_run rollback.

Written as a plain function so a future API endpoint can call it directly.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/evaluate_table_answer.py <answer_block_id> <reference_asset_id>
    python3 scripts/evaluate_table_answer.py <answer_block_id> <reference_asset_id> --stub-extraction
    python3 scripts/evaluate_table_answer.py <answer_block_id> <reference_asset_id> --stub --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import answer_evaluation             # noqa: E402
from core import db as db_mod                   # noqa: E402
from core.plugins import persistence            # noqa: E402
from core.plugins.registry import get_plugin    # noqa: E402
from core.plugins.table_extraction import TableReference   # noqa: E402

#: Verdicts rendered with a tick in the summary grid — everything else is a
#: problem the student (or the scan) has. Kept here rather than in
#: core/table_evaluator.py: which verdicts "count" for a terminal glyph is a
#: display choice, and the stored metrics always carry every verdict verbatim.
_MATCHED_VERDICTS = ("exact", "fuzzy", "numeric_close")

_VERDICT_GLYPH = {
    "exact": "✓", "fuzzy": "~", "numeric_close": "≈",
    "miss": "✗", "not_extracted": "?", "missing": "-",
}


def evaluate_table_answer(conn, answer_block_id: str, reference_asset_id: str,
                           stub_extraction: bool = False, stub: bool = False,
                           dry_run: bool = False) -> dict:
    """Score one table answer_block against one reference table asset.

    Takes a CONNECTION, not a cursor: the ledger write is delegated to
    core/plugins/persistence.write_evaluation_result(), which owns the
    commit/rollback (including the dry_run rollback). The connection's
    transaction must already have RLS bypassed (platform admin) or the
    correct college_id set — this function doesn't set it.
    """
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ab.answer_id, ab.blob_url, ab.block_type, a.status, a.question_id
            FROM answer_blocks ab
            JOIN answers a ON a.answer_id = ab.answer_id
            WHERE ab.block_id = %s
        """, (answer_block_id,))
        row = cur.fetchone()
        if row is None:
            # persistence.write_evaluation_result() raises RLSVisibilityError for
            # exactly this case with a full diagnostic; reuse it rather than
            # reporting "not found" for what is usually a missing tenant context.
            raise persistence.RLSVisibilityError(
                f"answer_block {answer_block_id!r} returned zero rows. answer_blocks is "
                f"RLS-protected and fails closed SILENTLY, so this is EITHER a "
                f"nonexistent block_id OR a missing tenant context on this transaction "
                f"(see core/plugins/persistence.py's module docstring, rule 4)."
            )
        answer_id, blob_url, block_type, answer_status, question_id = row

        if block_type != "table":
            raise ValueError(
                f"answer_block {answer_block_id} has block_type={block_type!r}, expected 'table'"
            )
        if not blob_url and not (stub or stub_extraction):
            raise ValueError(
                f"answer_block {answer_block_id} has no blob_url — cannot extract a "
                f"table from nothing. Use --stub-extraction for test data."
            )

        cur.execute(
            "SELECT asset_type, structured_data FROM content_assets WHERE asset_id = %s",
            (reference_asset_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"reference_asset_id {reference_asset_id} not found")
        asset_type, reference_table = row
        if asset_type != "table":
            raise ValueError(
                f"content_asset {reference_asset_id} has asset_type={asset_type!r}, "
                f"expected 'table'"
            )
        if not reference_table:
            raise ValueError(
                f"content_asset {reference_asset_id} has no structured_data to compare against"
            )

        cur.execute("SELECT marks_max FROM questions WHERE question_id = %s", (question_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"question_id {question_id} not found")
        marks_max = float(row[0])

        plugin = get_plugin("table_extraction")
        extracted = plugin.extract(blob_url, stub=stub or stub_extraction)
        result = plugin.evaluate(
            extracted,
            TableReference(table=reference_table, marks_max=marks_max,
                           asset_id=reference_asset_id),
            stub=stub,
        )

        # Issued BEFORE the ledger write so both land in the same
        # transaction — write_evaluation_result() owns the commit.
        status_changed = answer_evaluation.transition_to_ai_scored(cur, answer_id, answer_status)
    finally:
        cur.close()

    evaluation_id = persistence.write_evaluation_result(
        conn,
        answer_block_id=answer_block_id,
        result=result,
        reference_asset_id=reference_asset_id,
        dry_run=dry_run,
    )

    return {
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
        "reference_asset_id": reference_asset_id,
        "score": result.score,
        "marks_max": marks_max,
        "confidence": result.confidence,
        "explanation": result.explanation,
        "result": result.metrics["comparison"],
        "extraction": result.metrics["extraction"],
        "status_changed": status_changed,
    }


def _print_summary(payload: dict) -> None:
    """Compact terminal view of the comparison. The full structured object
    is always stored untouched in evaluation_results.metrics regardless of
    what is printed here — use --verbose for the raw JSON."""
    comparison = payload["result"]
    extraction = payload["extraction"]

    print(f"\nStructure: {extraction.get('rows')}x{extraction.get('cols')} detected "
          f"({extraction.get('detection_method')} detection) vs "
          f"{comparison['structure'].get('reference_rows')}x"
          f"{comparison['structure'].get('reference_cols')} reference")
    print(f"  structural score {comparison['structural_score']}   "
          f"content score {comparison['content_score']}   "
          f"(content on aligned cells only: {comparison['content_score_on_aligned']})")

    grid = comparison.get("verdict_grid", [])
    if grid:
        print("\nPer-cell verdicts")
        widths = [0] * comparison["structure"].get("reference_cols", 0)
        by_position = {(c["reference_row"], c["reference_col"]): c
                       for c in comparison["cell_verdicts"]}
        for (_r, c), cell in by_position.items():
            widths[c] = max(widths[c], len(cell["reference_text"]))
        for r, verdict_row in enumerate(grid):
            rendered = []
            for c, verdict in enumerate(verdict_row):
                cell = by_position[(r, c)]
                rendered.append(f"{_VERDICT_GLYPH.get(verdict, '?')} "
                                f"{cell['reference_text']:<{widths[c]}}")
            print("  " + " | ".join(rendered))
        legend = "  ".join(f"{glyph} {name}" for name, glyph in _VERDICT_GLYPH.items())
        print(f"  legend: {legend}")

    counts = comparison.get("verdict_counts", {})
    non_matches = [f"{name}={count}" for name, count in counts.items()
                   if count and name not in _MATCHED_VERDICTS]
    if non_matches:
        print("\nNon-matching cells: " + ", ".join(non_matches))
        for cell in comparison["cell_verdicts"]:
            if cell["verdict"] in _MATCHED_VERDICTS:
                continue
            student = "<no aligned cell>" if cell["student_text"] is None else repr(cell["student_text"])
            print(f"  ({cell['reference_row']},{cell['reference_col']}) "
                  f"{cell['reference_text']!r} vs {student}: {cell['detail']}")

    for label, key, index_key, text_key in (
        ("Missing rows", "missing_rows", "reference_row", "leading_cell"),
        ("Extra rows", "extra_rows", "student_row", "leading_cell"),
        ("Missing columns", "missing_cols", "reference_col", "header"),
        ("Extra columns", "extra_cols", "student_col", "header"),
    ):
        items = comparison.get(key, [])
        if items:
            print(f"\n{label} ({len(items)})")
            for item in items:
                label_text = item.get(text_key)
                shown = repr(label_text) if label_text else "<no readable label>"
                print(f"  - index {item[index_key]}: {shown}")

    print(f"\nAlignment: rows by {comparison['row_alignment']['method']}; "
          f"columns by {comparison['column_alignment']['method']}")

    warnings = extraction.get("extraction_warnings", [])
    if warnings:
        print(f"\nExtraction warnings ({len(warnings)})")
        for warning in warnings:
            print(f"  - {warning}")

    if comparison.get("needs_review"):
        print("\nFLAGGED FOR REVIEW")
        for reason in comparison.get("review_reasons", []):
            print(f"  - {reason}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("answer_block_id")
    ap.add_argument("reference_asset_id")
    ap.add_argument("--stub-extraction", action="store_true",
                    help="use a deterministic fake image->grid extraction (no image "
                         "fetch, no OCR model load) — needed for dummy-storage test "
                         "rows with no real object behind their blob_url")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake comparison entirely (implies stub extraction)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print result, roll back instead of committing")
    ap.add_argument("--verbose", action="store_true",
                    help="print the full stored JSON instead of the compact summary")
    args = ap.parse_args()

    conn = db_mod.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL app.is_platform_admin = 'true'")
        cur.close()

        payload = evaluate_table_answer(
            conn, args.answer_block_id, args.reference_asset_id,
            stub_extraction=args.stub_extraction, stub=args.stub,
            dry_run=args.dry_run,
        )
    except (ValueError, persistence.RLSVisibilityError) as e:
        conn.rollback()
        conn.close()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()

    header = (f"Score: {payload['score']} / {payload['marks_max']}   "
              f"(answer {payload['answer_id']})")
    print(f"\n{header}\n{'=' * len(header)}")
    if args.verbose:
        print(json.dumps({"comparison": payload["result"],
                          "extraction": payload["extraction"]}, indent=2))
    else:
        _print_summary(payload)

    if payload["status_changed"]:
        print("\nStatus: pending_evaluation -> ai_scored")

    print()
    if args.dry_run:
        print("[dry-run] rolled back, no changes persisted.")
    else:
        print("Committed.")


if __name__ == "__main__":
    sys.exit(main())
