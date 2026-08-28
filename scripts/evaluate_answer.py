#!/usr/bin/env python3
"""
scripts/evaluate_answer.py — Task 3: score a single student answer against
a reference answer variant using AI (embeddings or LLM).

BEHAVIOUR:
  1. Fetches answers.text_extracted + reference_answer_variants.content.
  2. Calls core/evaluator (--method embeddings|llm).
  3. Flips is_current=False on any previous evaluation_results for this answer.
  4. Inserts new evaluation_results row: evaluator_type='ai',
     evaluator_model=<model name>, is_current=True.
  5. Updates answers.status: pending_evaluation → ai_scored.
  6. Writes answer_status_history (changed_by=NULL — system-driven).

  The trigger trg_evaluation_variant_matches_answer_question (migration 001)
  enforces that the variant belongs to the same question as the answer — no
  application-level check needed.

  RLS: answer-schema tables have row-level security scoped by college_id.
  This script is a system-level operation, so it sets
  app.is_platform_admin = 'true' per transaction to bypass tenant isolation.

Written as a plain function so a future API endpoint can call it directly.

Usage:
    export PGHOST=... PGDATABASE=... PGUSER=... PGPASSWORD=...
    export GROQ_API_KEY=...  # only for --method llm

    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method embeddings
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method llm
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method llm --dry-run
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method embeddings --stub
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod              # noqa: E402
from core import evaluator                 # noqa: E402
from core import answer_evaluation         # noqa: E402

METHODS = {"embeddings": evaluator.score_with_embeddings,
           "llm": evaluator.score_with_llm}


def evaluate_answer(cur, answer_id: str, variant_id: str,
                    method: str = "embeddings", stub: bool = False) -> dict:
    """Score one answer against one reference variant. Caller owns the
    transaction — commit/rollback is the caller's responsibility.

    The cursor's connection must already have RLS bypassed (platform admin)
    or the correct college_id set."""

    if method not in METHODS:
        raise ValueError(f"method must be one of {list(METHODS)}, got {method!r}")

    # Fetch student answer
    cur.execute("""
        SELECT text_extracted, status, question_id
        FROM answers WHERE answer_id = %s
    """, (answer_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"answer_id {answer_id} not found")

    student_text, answer_status, question_id = row
    if not student_text:
        raise ValueError(
            f"answer {answer_id} has no text_extracted — "
            f"cannot score without extracted text (OCR not yet run?)"
        )

    # Fetch reference answer
    cur.execute("""
        SELECT content, question_id FROM reference_answer_variants
        WHERE variant_id = %s
    """, (variant_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"variant_id {variant_id} not found")
    reference_text, variant_question_id = row

    # Fetch marks_max for this question
    cur.execute("SELECT marks_max FROM questions WHERE question_id = %s",
                (question_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"question_id {question_id} not found")
    marks_max = float(row[0])

    # Score
    if stub:
        result = evaluator.stub_score(student_text, reference_text, marks_max)
    else:
        result = METHODS[method](student_text, reference_text, marks_max)

    evaluation_id = answer_evaluation.record_evaluation(
        cur,
        answer_id=answer_id,
        reference_answer_variant_id=variant_id,
        evaluator_model=result["model"],
        score=result["score"],
        explanation=result["explanation"],
        metrics=result.get("metrics"),
    )

    status_changed = answer_evaluation.transition_to_ai_scored(cur, answer_id, answer_status)

    return {
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
        "variant_id": variant_id,
        "method": method if not stub else "stub",
        "model": result["model"],
        "score": result["score"],
        "marks_max": marks_max,
        "explanation": result["explanation"],
        "metrics": result.get("metrics", {}),
        "status_changed": status_changed,
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("answer_id")
    ap.add_argument("variant_id")
    ap.add_argument("--method", required=True, choices=list(METHODS),
                    help="scoring method: embeddings (sentence-transformers) "
                         "or llm (Groq API)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print result, roll back instead of committing")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake score (no model/API needed)")
    args = ap.parse_args()

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")
            result = evaluate_answer(
                cur, args.answer_id, args.variant_id,
                method=args.method, stub=args.stub,
            )

        print(f"Answer {result['answer_id']}: "
              f"{result['score']}/{result['marks_max']} "
              f"(method={result['method']}, model={result['model']})")
        print(f"  {result['explanation']}")

        metrics = result.get('metrics', {})
        metrics_str = ", ".join(f"{k}={v}" for k, v in metrics.items() if k != "usage")
        if "usage" in metrics:
            metrics_str += f", usage={metrics['usage']}"
        if metrics_str:
            print(f"  Metrics: {metrics_str}")

        if result["status_changed"]:
            print("  Status: pending_evaluation → ai_scored")

        if args.dry_run:
            print("\n[dry-run] rolled back, no changes persisted.")
        else:
            print("\nCommitted.")
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
