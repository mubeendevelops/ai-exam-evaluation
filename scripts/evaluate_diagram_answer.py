#!/usr/bin/env python3
"""
scripts/evaluate_diagram_answer.py — Task 4: score one student's diagram
answer_block against a reference diagram (a content_assets row).

The scoring itself is core/block_evaluation.evaluate_block(), shared with
scripts/evaluate_table_answer.py; this script is argument parsing and the
terminal summary. Everything it does with an image or a graph goes through the
'diagram_evaluation' plugin (core/plugins/diagram_evaluation.py), which
wraps core/diagram_extractor.py and core/diagram_evaluator.py without
changing either. The ledger write goes through
core/plugins/persistence.write_evaluation_result(), like
scripts/evaluate_table_answer.py — see that module's docstring for what it
enforces (migration 012's reference XOR, the required metrics keys, and a
loud RLSVisibilityError instead of an orphaned row on a missing tenant
context). Scores, the stored metrics JSON and the stored explanation are
byte-identical to what this script produced before it was routed this way.

BEHAVIOUR (mirrors scripts/evaluate_answer.py, adapted for diagrams):
  1. Fetches the answer_block's blob_url (block_type must be 'diagram'; the
     blob_url may be empty only under --stub/--stub-extraction, which never
     read the image).
  2. Extracts its structure via the plugin's extract() (core/diagram_extractor
     — real extraction IS implemented, PaddleOCR, see that module's
     docstring); --stub-extraction is only needed for dummy-storage test
     data with no real image behind it.
  3. Fetches the reference asset's structured_data (content_assets,
     asset_type='diagram').
  4. Loads glossary_terms (optionally scoped by --topic-id, plus global
     terms), and calls the plugin's evaluate() — core/diagram_evaluator
     .compare_diagrams(), or stub_compare() with --stub, bypassing
     extraction and glossary lookup entirely.
  5. OPTIONALLY (--explain) runs a second, LLM-only pass over the finished
     comparison (core/diagram_evaluator.explain_comparison) that writes a
     teacher-readable paragraph. It CANNOT change the score — scoring stays
     deterministic and reproducible. --stub-llm fakes it.
  6. Flips is_current=False on any previous evaluation_results for this
     answer, inserts a new row with reference_asset_id set (NOT
     reference_answer_variant_id — see migration 012 / plan.md §4.1),
     score=similarity_score, evaluator_type='ai', metrics=<full comparison
     JSON, covering the task's (a)-(g)>.
  7. Updates answers.status: pending_evaluation -> ai_scored (same
     convention as evaluate_answer.py), with an answer_status_history row.

  The trigger trg_evaluation_reference_matches_answer_question (migration
  012) enforces that the reference asset is linked to the same question as
  the answer via question_asset_links(role='question_source') — no
  application-level check needed here.

  RLS: same as evaluate_answer.py — this is a system-level operation, so it
  sets app.is_platform_admin = 'true' per transaction to bypass tenant
  isolation.

Written as a plain function so a future API endpoint can call it directly.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/evaluate_diagram_answer.py <answer_block_id> <reference_asset_id> --stub-extraction
    python3 scripts/evaluate_diagram_answer.py <answer_block_id> <reference_asset_id> --stub-extraction --dry-run
    python3 scripts/evaluate_diagram_answer.py <answer_block_id> <reference_asset_id> --stub
    python3 scripts/evaluate_diagram_answer.py <answer_block_id> <reference_asset_id> --explain
    python3 scripts/evaluate_diagram_answer.py <answer_block_id> <reference_asset_id> --explain --stub-llm
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import block_evaluation      # noqa: E402
from core import db as db_mod          # noqa: E402
from core.plugins import persistence   # noqa: E402


def evaluate_diagram_answer(conn, answer_block_id: str, reference_asset_id: str,
                             stub_extraction: bool = False, stub: bool = False,
                             topic_id: str | None = None, explain: bool = False,
                             stub_llm: bool = False, dry_run: bool = False) -> dict:
    """Score one diagram answer_block against one reference diagram asset.

    A thin adapter over core/block_evaluation.evaluate_block(), which does
    the work (and owns the commit/rollback, including the dry_run rollback,
    through core/plugins/persistence.py). Takes a CONNECTION whose
    transaction already has RLS bypassed (platform admin) or the correct
    college_id set; this function doesn't set it.

    Returns the comparison FLAT under "result" — the diagram plugin stores
    it flat in evaluation_results.metrics (CLAUDE_CONTEXT.md §7), and
    _print_summary and scripts/evaluate_pending_diagrams.py read it there.
    """
    payload = block_evaluation.evaluate_block(
        conn, answer_block_id, reference_asset_id, block_type="diagram",
        stub=stub, stub_extraction=stub_extraction, dry_run=dry_run,
        topic_id=topic_id, explain=explain, stub_llm=stub_llm,
    )
    payload["result"] = payload.pop("metrics")
    return payload


def _is_noise_token(label: str) -> bool:
    """Display-only heuristic: is this anomaly token worth its own line, or
    is it the kind of single symbol/digit (arrow glyphs, ruled-line strokes,
    stray digits) that floods the terminal without telling a reader
    anything? Used only to decide how _print_summary groups the "Unexpected
    content" list — evaluation_results.metrics always stores the full,
    ungrouped anomalies list regardless of what's printed here."""
    stripped = label.strip()
    return len(stripped) <= 2 or not any(c.isalpha() for c in stripped)


def _print_summary(result: dict) -> None:
    """Compact, human-readable rendering of the comparison — the full
    structured object (plan.md §5) is always stored, untouched, in
    evaluation_results.metrics regardless of what's printed here; this is
    just a terminal-friendly view of it. Use --verbose for the raw JSON."""
    r = result["result"]

    nodes = r.get("node_validation", [])
    matched = [n for n in nodes if n["status"] == "matched"]
    missing = [n for n in nodes if n["status"] != "matched"]
    label_width = max((len(n["reference_label"]) for n in nodes), default=0)

    print(f"\nLabels ({len(matched)}/{len(nodes)} matched)")
    for n in matched:
        pct = f"{round(n['similarity'] * 100)}%"
        print(f"  ✓ {n['reference_label']:<{label_width}}  read as {n['matched_label']!r:<22} {pct}")
    for n in missing:
        print(f"  ✗ {n['reference_label']:<{label_width}}  not found")

    edges = r.get("edge_comparison", [])
    matched_edges = [e for e in edges if e["status"] in ("matched", "direction_reversed")]
    if edges:
        print(f"\nConnections ({len(matched_edges)}/{len(edges)} matched)")
        if not matched_edges:
            print("  edge detection (core/diagram_shapes.py) found no matching connectors "
                  "for this diagram — validated only against box+straight-arrow diagrams "
                  "so far, treat a zero count as 'not reliably detected', not 'confirmed "
                  "absent' (see that module's docstring)")

    anomalies = r.get("anomalies", [])
    if anomalies:
        readable, noise = [], []
        for a in anomalies:
            token = a.split("'")[1] if "'" in a else a
            (noise if _is_noise_token(token) else readable).append(a)

        print(f"\nUnexpected content ({len(anomalies)})")
        for a in readable:
            print(f"  - {a}")
        if noise:
            tokens = ", ".join(repr(a.split("'")[1]) for a in noise)
            print(f"  - {len(noise)} short/symbol read(s), likely arrows or stray "
                  f"strokes: {tokens}")

    glossary = r.get("glossary_matches", [])
    if glossary:
        extracted_width = max(len(repr(g["extracted_label"])) for g in glossary)
        print(f"\nGlossary corrections ({len(glossary)})")
        for g in glossary:
            print(f"  {g['extracted_label']!r:<{extracted_width}} -> "
                  f"{g['canonical_term']} ({g['match_type']})")

    narrative = r.get("explanation_pass")
    if narrative:
        # Printed LAST, and labelled, so it reads as commentary on the
        # numbers above rather than as part of them: this text came from the
        # LLM and had no say in the score printed at the top.
        usage = narrative.get("metrics", {}).get("usage", {})
        token_note = (f", {usage['total_tokens']} tokens" if usage.get("total_tokens")
                      else "")
        print(f"\nExplanation (LLM, severity={narrative['severity']}; "
              f"{narrative['model']}{token_note}) — does not affect the score")
        print(f"  {narrative['explanation']}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("answer_block_id")
    ap.add_argument("reference_asset_id")
    ap.add_argument("--topic-id", default=None,
                    help="scope glossary lookup to this topic (plus global terms)")
    ap.add_argument("--stub-extraction", action="store_true",
                    help="use a deterministic fake image->graph extraction "
                         "(no image fetch, no OCR model load) — needed for "
                         "dummy-storage test rows with no real object behind "
                         "their blob_url")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake comparison entirely (implies stub extraction, "
                         "no glossary/embedding-model load)")
    ap.add_argument("--explain", action="store_true",
                    help="second pass: ask the LLM to write a teacher-readable "
                         "explanation of the finished comparison. Does NOT change "
                         "the score — scoring stays deterministic")
    ap.add_argument("--stub-llm", action="store_true",
                    help="deterministic fake for the --explain pass only (no Groq "
                         "call); ignored without --explain")
    ap.add_argument("--dry-run", action="store_true",
                    help="print result, roll back instead of committing")
    ap.add_argument("--verbose", action="store_true",
                    help="print the full stored JSON instead of the compact summary")
    args = ap.parse_args()

    if args.stub_llm and not args.explain:
        print("NOTE: --stub-llm only affects the --explain pass, which wasn't requested.",
              file=sys.stderr)

    conn = db_mod.get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL app.is_platform_admin = 'true'")
        cur.close()

        result = evaluate_diagram_answer(
            conn, args.answer_block_id, args.reference_asset_id,
            stub_extraction=args.stub_extraction, stub=args.stub,
            topic_id=args.topic_id, explain=args.explain, stub_llm=args.stub_llm,
            dry_run=args.dry_run,
        )
    except (ValueError, NotImplementedError, persistence.RLSVisibilityError) as e:
        conn.rollback()
        conn.close()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()

    header = f"Score: {result['score']} / {result['marks_max']}   (answer {result['answer_id']})"
    print(f"\n{header}\n{'=' * len(header)}")
    if args.verbose:
        print(json.dumps(result["result"], indent=2))
    else:
        _print_summary(result)

    if result["status_changed"]:
        print("\nStatus: pending_evaluation -> ai_scored")

    print()
    if args.dry_run:
        print("[dry-run] rolled back, no changes persisted.")
    else:
        print("Committed.")


if __name__ == "__main__":
    sys.exit(main())
