#!/usr/bin/env python3
"""
scripts/evaluate_diagram_answer.py — Task 4: score one student's diagram
answer_block against a reference diagram (a content_assets row).

BEHAVIOUR (mirrors scripts/evaluate_answer.py, adapted for diagrams):
  1. Fetches the answer_block's blob_url (block_type must be 'diagram').
  2. Extracts its structure via core/diagram_extractor — real extraction is
     NOT implemented yet (no engine selected, plan.md §4.3), so
     --stub-extraction is required until that decision is made.
  3. Fetches the reference asset's structured_data (content_assets,
     asset_type='diagram').
  4. Loads glossary_terms (optionally scoped by --topic-id, plus global
     terms), and calls core/diagram_evaluator.compare_diagrams() — or
     stub_compare() with --stub, bypassing extraction and glossary lookup
     entirely.
  5. Flips is_current=False on any previous evaluation_results for this
     answer, inserts a new row with reference_asset_id set (NOT
     reference_answer_variant_id — see migration 012 / plan.md §4.1),
     score=similarity_score, evaluator_type='ai', metrics=<full comparison
     JSON, covering the task's (a)-(g)>.
  6. Updates answers.status: pending_evaluation -> ai_scored (same
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
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod          # noqa: E402
from core import diagram_extractor     # noqa: E402
from core import diagram_evaluator     # noqa: E402


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


def evaluate_diagram_answer(cur, answer_block_id: str, reference_asset_id: str,
                             stub_extraction: bool = False, stub: bool = False,
                             topic_id: str | None = None) -> dict:
    """Score one diagram answer_block against one reference diagram asset.
    Caller owns the transaction — commit/rollback is the caller's
    responsibility.

    The cursor's connection must already have RLS bypassed (platform admin)
    or the correct college_id set."""

    cur.execute("""
        SELECT ab.answer_id, ab.blob_url, ab.block_type, a.status, a.question_id
        FROM answer_blocks ab
        JOIN answers a ON a.answer_id = ab.answer_id
        WHERE ab.block_id = %s
    """, (answer_block_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"answer_block_id {answer_block_id} not found")
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

    if stub:
        extracted = diagram_extractor.stub_extract(blob_url)
        result = diagram_evaluator.stub_compare(extracted, reference_graph, marks_max)
    else:
        if stub_extraction:
            extracted = diagram_extractor.stub_extract(blob_url)
        else:
            extracted = diagram_extractor.extract_diagram_structure(blob_url)
        glossary = _load_glossary(cur, topic_id=topic_id)
        result = diagram_evaluator.compare_diagrams(extracted, reference_graph, glossary, marks_max)

    # Flip previous evaluation_results for this answer to is_current=False
    cur.execute("""
        UPDATE evaluation_results SET is_current = FALSE
        WHERE answer_id = %s AND is_current = TRUE
    """, (answer_id,))

    evaluation_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO evaluation_results (
            evaluation_id, answer_id, reference_answer_variant_id, reference_asset_id,
            evaluator_type, evaluator_model, score, explanation,
            is_current, evaluated_at, metrics
        )
        VALUES (%s, %s, NULL, %s, 'ai', %s, %s, %s, TRUE, now(), %s)
    """, (
        evaluation_id, answer_id, reference_asset_id,
        result["model"]["matching_method"], result["similarity_score"],
        f"Diagram comparison: {len(result.get('missing_information', []))} missing item(s), "
        f"{len(result.get('anomalies', []))} anomaly/anomalies.",
        json.dumps(result),
    ))

    if answer_status == "pending_evaluation":
        cur.execute("UPDATE answers SET status = 'ai_scored' WHERE answer_id = %s", (answer_id,))
        cur.execute("""
            INSERT INTO answer_status_history
                (history_id, answer_id, old_status, new_status, changed_by, changed_at)
            VALUES (%s, %s, 'pending_evaluation', 'ai_scored', NULL, now())
        """, (str(uuid.uuid4()), answer_id))

    return {
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
        "reference_asset_id": reference_asset_id,
        "score": result["similarity_score"],
        "marks_max": marks_max,
        "result": result,
        "status_changed": answer_status == "pending_evaluation",
    }


def _print_summary(result: dict) -> None:
    """Compact, human-readable rendering of the comparison — the full
    structured object (plan.md §5) is always stored in
    evaluation_results.metrics regardless of what's printed here; this is
    just a terminal-friendly view of it. Use --verbose for the raw JSON."""
    r = result["result"]

    nodes = r.get("node_validation", [])
    matched = [n for n in nodes if n["status"] == "matched"]
    missing = [n for n in nodes if n["status"] != "matched"]
    print(f"\nLabels: {len(matched)}/{len(nodes)} matched")
    for n in matched:
        print(f"  ✓ {n['reference_label']!r}  ← read as {n['matched_label']!r} "
              f"(similarity {n['similarity']})")
    for n in missing:
        print(f"  ✗ {n['reference_label']!r}  not found")

    edges = r.get("edge_comparison", [])
    matched_edges = [e for e in edges if e["status"] in ("matched", "direction_reversed")]
    if edges:
        note = ("  (edge/arrow detection is not implemented yet — this is "
                 "not a real signal, see CLAUDE_CONTEXT.md §11)" if not matched_edges else "")
        print(f"\nConnections: {len(matched_edges)}/{len(edges)} matched{note}")

    anomalies = r.get("anomalies", [])
    if anomalies:
        print(f"\nUnexpected content ({len(anomalies)}):")
        for a in anomalies:
            print(f"  - {a}")

    glossary = r.get("glossary_matches", [])
    if glossary:
        print(f"\nGlossary corrections applied ({len(glossary)}):")
        for g in glossary:
            print(f"  {g['extracted_label']!r} → {g['canonical_term']} ({g['match_type']})")


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
                         "(no real extraction service exists yet — see plan.md §4.3)")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake comparison entirely (implies stub extraction, "
                         "no glossary/embedding-model load)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print result, roll back instead of committing")
    ap.add_argument("--verbose", action="store_true",
                    help="print the full stored JSON instead of the compact summary")
    args = ap.parse_args()

    conn = db_mod.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")
            result = evaluate_diagram_answer(
                cur, args.answer_block_id, args.reference_asset_id,
                stub_extraction=args.stub_extraction, stub=args.stub,
                topic_id=args.topic_id,
            )

        print(f"Answer {result['answer_id']}: {result['score']}/{result['marks_max']}")
        if args.verbose:
            print(json.dumps(result["result"], indent=2))
        else:
            _print_summary(result)

        if result["status_changed"]:
            print("\nStatus: pending_evaluation -> ai_scored")

        if args.dry_run:
            conn.rollback()
            print("\n[dry-run] rolled back, no changes persisted.")
        else:
            conn.commit()
            print("\nCommitted.")
    except (ValueError, NotImplementedError) as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
