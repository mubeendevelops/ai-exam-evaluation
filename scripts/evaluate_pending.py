#!/usr/bin/env python3
"""
scripts/evaluate_pending.py — Task 3 batch runner: scores all answers
with status='pending_evaluation'.

For each pending answer, finds the current reference variant for its
question, then delegates to evaluate_answer.evaluate_answer(). Each
answer is committed independently so one failure doesn't roll back the
entire batch.

Answers whose question has no current reference variant are skipped with
a warning (the SME hasn't created one yet — nothing to score against).

This script becomes a scheduled job later; standalone CLI first.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...

    python3 scripts/evaluate_pending.py --method embeddings
    python3 scripts/evaluate_pending.py --method llm
    python3 scripts/evaluate_pending.py --method embeddings --stub --dry-run
    python3 scripts/evaluate_pending.py --method embeddings --limit 10
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod                          # noqa: E402
from scripts.evaluate_answer import evaluate_answer, METHODS  # noqa: E402


def _find_pending(cur, limit: int | None = None) -> list[dict]:
    """Find pending answers paired with the current reference variant
    for their question. Skips answers whose question has no current
    variant (returns them separately for logging)."""
    sql = """
        SELECT a.answer_id, a.question_id,
               rav.variant_id
        FROM   answers a
        LEFT JOIN reference_answer_variants rav
            ON  rav.question_id = a.question_id
            AND rav.is_current = TRUE
        WHERE  a.status = 'pending_evaluation'
        ORDER  BY a.submitted_at ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    cur.execute(sql)
    return [
        {"answer_id": str(r[0]), "question_id": str(r[1]),
         "variant_id": str(r[2]) if r[2] else None}
        for r in cur.fetchall()
    ]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--method", required=True, choices=list(METHODS),
                    help="scoring method: embeddings or llm")
    ap.add_argument("--limit", type=int, default=None,
                    help="max answers to process (default: all)")
    ap.add_argument("--dry-run", action="store_true",
                    help="roll back each answer instead of committing")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake scores (no model/API needed)")
    args = ap.parse_args()

    conn = db_mod.get_connection()
    conn.autocommit = False

    # Initial query uses platform admin to see all tenants
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")
            pending = _find_pending(cur, limit=args.limit)
        conn.rollback()  # release the read-only transaction
    except Exception as e:
        conn.rollback()
        print(f"ERROR fetching pending answers: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)

    if not pending:
        print("No answers with status='pending_evaluation' found.")
        conn.close()
        return

    scored, skipped, failed = 0, 0, 0

    for item in pending:
        if item["variant_id"] is None:
            print(f"  SKIP {item['answer_id']}: question {item['question_id']} "
                  f"has no current reference variant")
            skipped += 1
            continue

        try:
            with conn.cursor() as cur:
                cur.execute("SET LOCAL app.is_platform_admin = 'true'")
                result = evaluate_answer(
                    cur, item["answer_id"], item["variant_id"],
                    method=args.method, stub=args.stub,
                )
            if args.dry_run:
                conn.rollback()
            else:
                conn.commit()
            scored += 1
            metrics = result.get('metrics', {})
            latency_str = f"{metrics.get('latency_ms', 0)}ms"
            usage_str = f", tokens={metrics['usage']['total_tokens']}" if "usage" in metrics else ""
            print(f"  OK   {result['answer_id']}: "
                  f"{result['score']}/{result['marks_max']} "
                  f"({result['model']}, {latency_str}{usage_str})")
        except Exception as e:
            conn.rollback()
            failed += 1
            print(f"  FAIL {item['answer_id']}: {e}", file=sys.stderr)

    print(f"\nDone. scored={scored}, skipped={skipped}, failed={failed}"
          + (" [dry-run]" if args.dry_run else ""))
    conn.close()


if __name__ == "__main__":
    sys.exit(main())
