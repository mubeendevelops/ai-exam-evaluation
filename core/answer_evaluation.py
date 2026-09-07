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


def record_extraction(
    cur,
    *,
    block_id: str,
    text: str | None,
    ocr_confidence: float | None,
    plugin: str,
    plugin_version: str | None = None,
    mode: str | None = None,
    engines: list | None = None,
) -> str:
    """Flips any previous is_current answer_block_extractions row for this
    block to is_current=False, then inserts a new is_current=True row.

    Same append-only-ledger shape as record_evaluation() above, applied to
    migrations/019_answer_block_extractions.sql's table instead of
    evaluation_results: one row per extraction, is_current flags the active
    one, and a re-OCR is an INSERT plus a flip — never an UPDATE of `text`
    (that migration's header explains why: settling CLAUDE_CONTEXT.md §10
    decision 2 by accident is exactly what this shape avoids). `college_id`
    is NOT passed — migration 019's trigger derives it from the parent
    answer_blocks row, the same way answer_blocks itself derives its own
    college_id, so a caller cannot get it wrong or stale.

    `text=None` is a legitimate value (an extraction that produced nothing is
    still a fact worth recording — see that migration's COMMENT ON COLUMN
    text), so this never substitutes a placeholder for it. Returns the new
    extraction_id.
    """
    cur.execute("""
        UPDATE answer_block_extractions SET is_current = FALSE
        WHERE block_id = %s AND is_current = TRUE
    """, (block_id,))

    extraction_id = str(uuid.uuid4())
    cur.execute("""
        INSERT INTO answer_block_extractions (
            extraction_id, block_id, text, ocr_confidence,
            plugin, plugin_version, mode, engines,
            is_current, extracted_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, now())
    """, (
        extraction_id, block_id, text, ocr_confidence,
        plugin, plugin_version, mode,
        json.dumps(engines) if engines is not None else None,
    ))
    return extraction_id


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


def transition_to_sme_reviewed(cur, answer_id: str, current_status: str,
                               reviewer_id: str) -> bool:
    """If a human review can move this answer forward, transitions it to
    'sme_reviewed' and records the change in answer_status_history
    (changed_by = the reviewer, since this one is NOT system-driven — the
    counterpart to transition_to_ai_scored's changed_by=NULL). Returns
    whether the transition happened.

    Allowed from 'pending_evaluation', 'ai_scored' and 'flagged'. A teacher
    may override a score that was never produced (the AI failed, or the
    question was flagged for review) — refusing that would make an unscorable
    answer permanently unreviewable, which is the opposite of what the review
    queue is for.

    NOT allowed from 'finalized': there is no path back out of finalized in
    this schema (PROJECT_CONTEXT.md §7 lists re-opening a finalized answer as
    an open product decision), so quietly reversing it here would settle that
    decision by accident. Returns False and leaves the status alone; the
    caller reports it.

    'sme_reviewed' is re-entrant — a second override by another teacher stays
    in the same state and writes another answer_reviews row rather than
    inventing a transition, because answer_reviews is the log of who said
    what and the status is only a summary of where the answer has got to.
    """
    reviewable = ("pending_evaluation", "ai_scored", "flagged")
    if current_status not in reviewable:
        return False

    cur.execute("UPDATE answers SET status = 'sme_reviewed' WHERE answer_id = %s",
                (answer_id,))
    cur.execute("""
        INSERT INTO answer_status_history
            (history_id, answer_id, old_status, new_status, changed_by, changed_at)
        VALUES (%s, %s, %s, 'sme_reviewed', %s, now())
    """, (str(uuid.uuid4()), answer_id, current_status, reviewer_id))
    return True
