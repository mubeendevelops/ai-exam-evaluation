"""
core/answer_evaluation.py — shared write-path helpers for recording an AI
evaluation result against an answer, used identically by
scripts/evaluate_answer.py (text) and scripts/evaluate_diagram_answer.py
(diagram). Both scripts previously duplicated this sequence in full.

Both callers must run inside a transaction with RLS already bypassed
(platform admin) or the correct college_id set — these helpers don't touch
RLS themselves.
"""
from __future__ import annotations

import json
import uuid
from typing import Any


def record_evaluation(
    cur,
    *,
    answer_id: str,
    evaluator_model: str,
    score: float,
    explanation: str,
    metrics: dict[str, Any] | None = None,
    reference_answer_variant_id: str | None = None,
    reference_asset_id: str | None = None,
    evaluator_type: str = "ai",
) -> str:
    """Flips any previous is_current evaluation_results row for this answer
    to is_current=False, then inserts a new is_current=True row.

    Exactly one of reference_answer_variant_id / reference_asset_id should
    be set (per the chk_evaluation_results_one_reference CHECK constraint,
    migration 012) — text evaluations set the former, diagram evaluations
    the latter. Returns the new evaluation_id.
    """
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
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, now(), %s)
    """, (
        evaluation_id, answer_id, reference_answer_variant_id, reference_asset_id,
        evaluator_type, evaluator_model, score, explanation,
        json.dumps(metrics) if metrics is not None else None,
    ))
    return evaluation_id


def transition_to_ai_scored(cur, answer_id: str, current_status: str) -> bool:
    """If current_status is 'pending_evaluation', transitions the answer to
    'ai_scored' and records the change in answer_status_history
    (changed_by=NULL — system-driven). Returns whether the transition
    happened, so callers can report it."""
    if current_status != "pending_evaluation":
        return False

    cur.execute("UPDATE answers SET status = 'ai_scored' WHERE answer_id = %s", (answer_id,))
    cur.execute("""
        INSERT INTO answer_status_history
            (history_id, answer_id, old_status, new_status, changed_by, changed_at)
        VALUES (%s, %s, 'pending_evaluation', 'ai_scored', NULL, now())
    """, (str(uuid.uuid4()), answer_id))
    return True
