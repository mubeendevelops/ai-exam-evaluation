#!/usr/bin/env python3
"""
scripts/evaluate_answer.py — Task 3: score a single student answer against
a reference answer variant using AI (embeddings, LLM, keyword coverage, or
a weighted blend of all four signals plus rubric coverage).

BEHAVIOUR:
  1. Fetches answers.text_extracted + reference_answer_variants.content,
     and — for --method blended|keyword — the question's weighted keywords
     (question_keywords joined to keywords).
  2. Routes through the plugin registry: core/plugins/registry.get_plugin
     ("text_extraction"), whose extract()/evaluate() wrap
     core/evaluator.py's score_with_embeddings/score_with_llm (no
     reimplementation) and add keyword/rubric coverage. See
     core/plugins/text_extraction.py's module docstring for the four
     signals and how they blend.
  3. Flips is_current=False on any previous evaluation_results for this
     answer.
  4. Inserts new evaluation_results row: evaluator_type='ai',
     evaluator_model=<model/blend descriptor>, is_current=True, with the
     full per-signal breakdown in metrics (migration 010).
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
    export GROQ_API_KEY=...  # only needed for the llm signal (blended/llm)

    python3 scripts/evaluate_answer.py <answer_id> <variant_id>                       # --method blended (default)
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method embeddings
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method llm
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method keyword
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method blended --weights "llm=0.5,semantic=0.25,keyword=0.15,rubric=0.1"
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method blended --dry-run
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method blended --stub-llm   # real semantic/keyword/rubric, fake LLM call
    python3 scripts/evaluate_answer.py <answer_id> <variant_id> --method embeddings --stub    # everything fake, no model/API needed
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db as db_mod              # noqa: E402
from core import answer_evaluation         # noqa: E402
from core.plugins.registry import get_plugin       # noqa: E402
from core.plugins.text_extraction import (         # noqa: E402
    PlainText, TextReference, parse_weights,
)

METHODS = ("blended", "embeddings", "llm", "keyword")


def _load_keywords(cur, question_id: str) -> list[dict]:
    cur.execute("""
        SELECT k.term, qk.weight
        FROM question_keywords qk
        JOIN keywords k ON k.keyword_id = qk.keyword_id
        WHERE qk.question_id = %s
    """, (question_id,))
    return [{"term": term, "weight": float(weight)} for term, weight in cur.fetchall()]


def evaluate_answer(cur, answer_id: str, variant_id: str,
                    method: str = "blended", stub: bool = False,
                    stub_llm: bool = False, weights: dict | None = None) -> dict:
    """Score one answer against one reference variant. Caller owns the
    transaction — commit/rollback is the caller's responsibility.

    The cursor's connection must already have RLS bypassed (platform admin)
    or the correct college_id set."""

    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}, got {method!r}")

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

    keywords = _load_keywords(cur, question_id) if method in ("blended", "keyword") else []

    reference = TextReference(
        reference_text=reference_text,
        marks_max=marks_max,
        keywords=keywords,
        question_id=str(question_id),
        variant_id=str(variant_id),
    )

    plugin = get_plugin("text_extraction")
    extracted = plugin.extract(PlainText(student_text), stub=stub)
    result = plugin.evaluate(
        extracted, reference,
        stub=stub, stub_llm=stub_llm, method=method, weights=weights,
    )

    evaluation_id = answer_evaluation.record_evaluation(
        cur,
        answer_id=answer_id,
        reference_answer_variant_id=variant_id,
        evaluator_model=result.metrics["evaluator_model"],
        score=result.score,
        explanation=result.explanation,
        metrics=result.metrics,
    )

    status_changed = answer_evaluation.transition_to_ai_scored(cur, answer_id, answer_status)

    return {
        "answer_id": answer_id,
        "evaluation_id": evaluation_id,
        "variant_id": variant_id,
        "method": method if not stub else f"{method}(stub)",
        "model": result.metrics["evaluator_model"],
        "score": result.score,
        "marks_max": marks_max,
        "confidence": result.confidence,
        "explanation": result.explanation,
        "metrics": result.metrics,
        "status_changed": status_changed,
    }


def _print_signals(result: dict) -> None:
    signals = result["metrics"].get("signals", {})
    weights = result["metrics"].get("weights", {})
    for name in ("semantic", "llm", "keyword", "rubric"):
        sig = signals.get(name)
        if sig is None:
            continue
        if sig.get("score") is None:
            print(f"    [{name:<8}] unavailable — {sig['explanation']}")
            continue
        weight_str = f" (weight {weights[name]})" if name in weights else ""
        print(f"    [{name:<8}] {sig['score']}/{result['marks_max']}{weight_str} — {sig['model']}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("answer_id")
    ap.add_argument("variant_id")
    ap.add_argument("--method", default="blended", choices=list(METHODS),
                    help="scoring method (default: blended). 'blended' combines "
                         "semantic + llm + keyword + rubric; the others isolate "
                         "one signal.")
    ap.add_argument("--weights", default=None,
                    help="override blend weights for --method blended, e.g. "
                         "'llm=0.5,semantic=0.25,keyword=0.15,rubric=0.1' — "
                         "unspecified signals keep their default weight")
    ap.add_argument("--dry-run", action="store_true",
                    help="print result, roll back instead of committing")
    ap.add_argument("--stub", action="store_true",
                    help="deterministic fake score for every signal touched "
                         "(no model/API needed)")
    ap.add_argument("--stub-llm", action="store_true",
                    help="deterministic fake for the LLM signal only — semantic/"
                         "keyword/rubric still run for real (saves Groq quota "
                         "while exercising the rest of blended scoring)")
    args = ap.parse_args()

    weights = None
    if args.weights:
        try:
            weights = parse_weights(args.weights)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        with db_mod.transaction(dry_run=args.dry_run) as cur:
            cur.execute("SET LOCAL app.is_platform_admin = 'true'")
            result = evaluate_answer(
                cur, args.answer_id, args.variant_id,
                method=args.method, stub=args.stub, stub_llm=args.stub_llm,
                weights=weights,
            )

        print(f"Answer {result['answer_id']}: "
              f"{result['score']}/{result['marks_max']} "
              f"(method={result['method']}, confidence={result['confidence']})")
        print(f"  {result['explanation']}")
        _print_signals(result)

        if result["metrics"].get("low_confidence"):
            print("  ⚠ LOW CONFIDENCE — semantic/LLM signals diverge, flagged for human review.")

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
