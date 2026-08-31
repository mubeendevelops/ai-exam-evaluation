#!/usr/bin/env python3
"""
scripts/evaluate_diagram_answer.py — Task 4: score one student's diagram
answer_block against a reference diagram (a content_assets row).

Everything this script does with an image or a graph goes through the
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
  1. Fetches the answer_block's blob_url (block_type must be 'diagram').
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
from core import answer_evaluation     # noqa: E402
from core import db as db_mod          # noqa: E402
from core.plugins import persistence   # noqa: E402
from core.plugins.diagram_evaluation import DiagramReference  # noqa: E402
from core.plugins.registry import get_plugin                   # noqa: E402


def _load_glossary(cur, topic_id: str | None = None) -> list[dict]:
    if topic_id:
        cur.execute("""
            SELECT term_id, canonical_term, aliases FROM glossary_terms
            WHERE topic_id = %s OR topic_id IS NULL
        """, (topic_id,))
    else:
        cur.execute("SELECT term_id, canonical_term, aliases FROM glossary_terms")
    return [
        {"term_id": str(r[0]), "canonical_term": r[1], "aliases": r[2] or []}
        for r in cur.fetchall()
    ]


def evaluate_diagram_answer(conn, answer_block_id: str, reference_asset_id: str,
                             stub_extraction: bool = False, stub: bool = False,
                             topic_id: str | None = None, explain: bool = False,
                             stub_llm: bool = False, dry_run: bool = False) -> dict:
    """Score one diagram answer_block against one reference diagram asset.

    Takes a CONNECTION, not a cursor (it used to take a cursor): the ledger
    write is delegated to core/plugins/persistence.write_evaluation_result(),
    which owns the commit/rollback including the dry_run rollback — the same
    shape scripts/evaluate_table_answer.py has. The connection's transaction
    must already have RLS bypassed (platform admin) or the correct
    college_id set; this function doesn't set it.
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
            # reporting "not found" for what is usually a missing tenant context
            # (core/plugins/persistence.py's module docstring, rule 4).
            raise persistence.RLSVisibilityError(
                f"answer_block {answer_block_id!r} returned zero rows. answer_blocks is "
                f"RLS-protected and fails closed SILENTLY, so this is EITHER a "
                f"nonexistent block_id OR a missing tenant context on this transaction."
            )
        answer_id, blob_url, block_type, answer_status, question_id = row

        if block_type != "diagram":
            raise ValueError(
                f"answer_block {answer_block_id} has block_type={block_type!r}, expected 'diagram'"
            )
        if not blob_url:
            raise ValueError(
                f"answer_block {answer_block_id} has no blob_url — cannot extract a diagram from nothing"
            )

        cur.execute("""
            SELECT asset_type, structured_data FROM content_assets WHERE asset_id = %s
        """, (reference_asset_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"reference_asset_id {reference_asset_id} not found")
        asset_type, reference_graph = row
        if asset_type != "diagram":
            raise ValueError(
                f"content_asset {reference_asset_id} has asset_type={asset_type!r}, expected 'diagram'"
            )
        if not reference_graph:
            raise ValueError(
                f"content_asset {reference_asset_id} has no structured_data to compare against"
            )

        cur.execute("SELECT marks_max FROM questions WHERE question_id = %s", (question_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"question_id {question_id} not found")
        marks_max = float(row[0])

        # --stub bypasses the glossary lookup entirely, as it always has:
        # stub_compare() never looks at a glossary, so querying for one
        # would be work whose result is discarded.
        glossary = [] if stub else _load_glossary(cur, topic_id=topic_id)

        plugin = get_plugin("diagram_evaluation")
        extracted = plugin.extract(blob_url, stub=stub or stub_extraction)
        result = plugin.evaluate(
            extracted,
            DiagramReference(graph=reference_graph, marks_max=marks_max,
                             glossary_terms=glossary, asset_id=reference_asset_id),
            stub=stub, explain=explain, stub_llm=stub_llm,
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
        "result": result.metrics,
        "status_changed": status_changed,
    }


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
