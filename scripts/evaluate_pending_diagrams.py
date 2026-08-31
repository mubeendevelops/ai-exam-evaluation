#!/usr/bin/env python3
"""
scripts/evaluate_pending_diagrams.py — Task 4 batch runner (plan.md §7
phase 2): scores every DIAGRAM answer_block belonging to an answer with
status='pending_evaluation'.

Mirrors scripts/evaluate_pending.py, with the differences the diagram side
forces:

  - It batches over ANSWER_BLOCKS, not answers. Text answers are scored
    whole (evaluate_answer.py takes an answer_id); a diagram is scored per
    block, because the image lives on answer_blocks.blob_url.
  - The reference is a content_assets row, not a reference_answer_variants
    row, and it is found through question_asset_links(role='question_source')
    filtered to asset_type='diagram' — the same link the migration-012
    trigger trg_evaluation_reference_matches_answer_question insists on. An
    answer whose question has no such linked diagram asset is SKIPPED with a
    reason, exactly as evaluate_pending.py skips a question with no current
    reference variant.
  - Each block is committed independently, so one failure doesn't roll back
    the batch.

TWO AMBIGUITIES ARE SKIPPED, NOT GUESSED AT. Both would otherwise produce a
score that is quietly wrong rather than obviously missing:

  1. An answer with MORE THAN ONE diagram block. evaluation_results is keyed
     by answer_id, not block_id, and core/answer_evaluation.record_evaluation()
     flips every previous is_current row for that ANSWER — so scoring two
     blocks of one answer leaves the second silently overwriting the first as
     "the current score for this answer". That is a real, pre-existing
     structural limitation of the ledger (it applies just as much to the two
     table blocks the seed data already has on one answer), not something a
     batch runner should paper over by picking a block. Score those by hand
     with scripts/evaluate_diagram_answer.py, deliberately.
  2. A question linked to MORE THAN ONE diagram asset. Which one is "the"
     reference is a content decision; the runner names the candidates and
     leaves it to a human.

RLS: system-level operation, so every transaction sets
app.is_platform_admin = 'true' — same convention as evaluate_pending.py and
evaluate_diagram_answer.py, and required here because a batch job is
deliberately cross-tenant (CLAUDE_CONTEXT.md §6).

This becomes a scheduled job later; standalone CLI first.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/evaluate_pending_diagrams.py                    # real extraction + scoring
    python3 scripts/evaluate_pending_diagrams.py --dry-run
    python3 scripts/evaluate_pending_diagrams.py --stub --dry-run   # no OCR, no embeddings, no DB writes
    python3 scripts/evaluate_pending_diagrams.py --stub-extraction --limit 10
    python3 scripts/evaluate_pending_diagrams.py --explain --stub-llm
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod                                        # noqa: E402
from core.plugins import persistence                                  # noqa: E402
from scripts.evaluate_diagram_answer import evaluate_diagram_answer   # noqa: E402

#: How much of a UUID to show in the summary table. Full UUIDs make every
#: row 36 characters of mostly-identical text; the first segment is enough
#: to tell this batch's rows apart, and the full id is printed on the
#: per-block line above the table for anything that needs copying.
_ID_WIDTH = 8


def _find_pending(cur, limit: int | None = None) -> list[dict]:
    """Every diagram answer_block on a pending answer, paired with the one
    diagram asset linked to its question as question_source.

    The two ambiguity counts (`diagram_block_count`, `asset_count`) are
    computed in SQL rather than by post-processing in Python so that --limit
    can't change a block's verdict: a row that is ambiguous is ambiguous
    whether or not the rest of its answer fell outside the limit.
    """
    cur.execute("""
        SELECT ab.block_id, ab.answer_id, a.question_id, ab.blob_url,
               COUNT(*) OVER (PARTITION BY ab.answer_id) AS diagram_block_count,
               ref.asset_id, ref.asset_count
        FROM   answers a
        JOIN   answer_blocks ab
            ON ab.answer_id = a.answer_id AND ab.block_type = 'diagram'
        LEFT   JOIN LATERAL (
            SELECT MIN(ca.asset_id::text)::uuid AS asset_id,
                   COUNT(*)                     AS asset_count
            FROM   question_asset_links qal
            JOIN   content_assets ca ON ca.asset_id = qal.asset_id
            WHERE  qal.question_id = a.question_id
              AND  qal.role = 'question_source'
              AND  ca.asset_type = 'diagram'
              AND  ca.structured_data IS NOT NULL
        ) ref ON TRUE
        WHERE  a.status = 'pending_evaluation'
        ORDER  BY a.submitted_at ASC, ab.sequence_order ASC
        LIMIT  %s
    """, (limit,))
    return [
        {"block_id": str(r[0]), "answer_id": str(r[1]), "question_id": str(r[2]),
         "blob_url": r[3], "diagram_block_count": r[4],
         "asset_id": str(r[5]) if r[5] else None, "asset_count": r[6] or 0}
        for r in cur.fetchall()
    ]


def _skip_reason(item: dict) -> str | None:
    """Why this block can't be scored unattended, or None if it can. See the
    module docstring for why each of these is a skip rather than a guess."""
    if item["diagram_block_count"] > 1:
        return (f"answer has {item['diagram_block_count']} diagram blocks and "
                f"evaluation_results is keyed by answer, not block — score these "
                f"individually with evaluate_diagram_answer.py")
    if item["asset_count"] == 0:
        return (f"question {item['question_id']} has no diagram asset linked via "
                f"question_asset_links(role='question_source') — nothing to score against")
    if item["asset_count"] > 1:
        return (f"question {item['question_id']} has {item['asset_count']} linked "
                f"diagram assets — which one is the reference is a content decision")
    if not item["blob_url"]:
        return "answer_block has no blob_url — no image to extract a diagram from"
    return None


def _counts(result: dict) -> tuple[str, str]:
    """(labels, connections) as "matched/total" strings for the summary
    table, read out of the stored comparison. Both are "-" under --stub,
    whose comparison deliberately carries empty detail lists."""
    r = result["result"]
    nodes = r.get("node_validation", [])
    edges = r.get("edge_comparison", [])
    matched_nodes = sum(1 for n in nodes if n["status"] == "matched")
    matched_edges = sum(1 for e in edges if e["status"] in ("matched", "direction_reversed"))
    return (f"{matched_nodes}/{len(nodes)}" if nodes else "-",
            f"{matched_edges}/{len(edges)}" if edges else "-")


def _print_summary_table(rows: list[dict], dry_run: bool) -> None:
    """One line per block attempted, then the totals. Deliberately a table
    rather than evaluate_pending.py's running log alone: a diagram batch's
    interesting signal is the SPREAD (which diagrams scored badly, which lost
    their labels versus their connectors), and that is unreadable as prose."""
    header = (f"{'answer':<{_ID_WIDTH}}  {'block':<{_ID_WIDTH}}  {'score':>11}  "
              f"{'labels':>8}  {'conns':>8}  outcome")
    print("\n" + header)
    print("-" * len(header))

    for row in rows:
        score = (f"{row['score']}/{row['marks_max']}" if row["score"] is not None else "-")
        print(f"{row['answer_id'][:_ID_WIDTH]:<{_ID_WIDTH}}  "
              f"{row['block_id'][:_ID_WIDTH]:<{_ID_WIDTH}}  {score:>11}  "
              f"{row['labels']:>8}  {row['connections']:>8}  {row['outcome']}")

    print("-" * len(header))
    scored = [r for r in rows if r["outcome"] == "ok"]
    skipped = sum(1 for r in rows if r["outcome"].startswith("skip"))
    failed = sum(1 for r in rows if r["outcome"].startswith("FAIL"))
    fractions = [r["score"] / r["marks_max"] for r in scored if r["marks_max"]]
    mean_note = (f", mean {sum(fractions) / len(fractions) * 100:.1f}% of marks"
                 if fractions else "")
    print(f"scored={len(scored)}, skipped={skipped}, failed={failed}{mean_note}"
          + ("  [dry-run — nothing persisted]" if dry_run else ""))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--limit", type=int, default=None,
                    help="max diagram blocks to process (default: all)")
    ap.add_argument("--topic-id", default=None,
                    help="scope glossary lookup to this topic (plus global terms)")
    ap.add_argument("--dry-run", action="store_true",
                    help="roll back each block instead of committing")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake extraction AND comparison (no OCR "
                         "model, no embedding model, no image fetch)")
    ap.add_argument("--stub-extraction", action="store_true",
                    help="fake only the image->graph extraction; the comparison "
                         "runs for real. Needed for dummy-storage test rows with "
                         "no real object behind their blob_url")
    ap.add_argument("--explain", action="store_true",
                    help="run the LLM explanation pass on each result. Does NOT "
                         "change any score. Costs one Groq call per block")
    ap.add_argument("--stub-llm", action="store_true",
                    help="deterministic fake for the --explain pass only")
    args = ap.parse_args()

    if args.stub_llm and not args.explain:
        print("NOTE: --stub-llm only affects the --explain pass, which wasn't requested.",
              file=sys.stderr)

    conn = db_mod.get_connection()
    conn.autocommit = False

    # Initial query runs as platform admin so the batch sees every tenant.
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")
            pending = _find_pending(cur, limit=args.limit)
        conn.rollback()  # release the read-only transaction
    except Exception as e:
        conn.rollback()
        print(f"ERROR fetching pending diagram blocks: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    if not pending:
        print("No diagram answer_blocks on answers with status='pending_evaluation'.")
        conn.close()
        return

    print(f"Found {len(pending)} diagram block(s) to consider.\n")
    rows = []

    for item in pending:
        base = {"answer_id": item["answer_id"], "block_id": item["block_id"],
                "score": None, "marks_max": None, "labels": "-", "connections": "-"}

        reason = _skip_reason(item)
        if reason:
            print(f"  SKIP {item['block_id']}: {reason}")
            rows.append({**base, "outcome": "skip"})
            continue

        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.is_platform_admin = 'true'")
            result = evaluate_diagram_answer(
                conn, item["block_id"], item["asset_id"],
                stub_extraction=args.stub_extraction, stub=args.stub,
                topic_id=args.topic_id, explain=args.explain,
                stub_llm=args.stub_llm, dry_run=args.dry_run,
            )
        except Exception as e:
            conn.rollback()
            print(f"  FAIL {item['block_id']}: {e}", file=sys.stderr)
            rows.append({**base, "outcome": f"FAIL ({type(e).__name__})"})
            continue

        labels, connections = _counts(result)
        extraction = result["result"].get("extraction", {})
        latency = extraction.get("latency_ms")
        latency_note = f", extract {latency:.0f}ms" if latency else ""
        print(f"  OK   {item['block_id']}: {result['score']}/{result['marks_max']} "
              f"(labels {labels}, connections {connections}{latency_note})")
        rows.append({**base, "score": result["score"], "marks_max": result["marks_max"],
                     "labels": labels, "connections": connections, "outcome": "ok"})

    conn.close()
    _print_summary_table(rows, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
