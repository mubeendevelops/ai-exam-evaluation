"""
core/plugins/persistence.py — the one write path from an EvaluationResult to
the evaluation_results ledger.

Plugins produce scores; this module is the only thing that persists them, so
the repo's non-negotiable DB rules (CLAUDE_CONTEXT.md §5, §6) are enforced in
exactly one place instead of being re-derived by every new evaluation module:

  1. APPEND-ONLY. A score is never UPDATEd. Re-scoring an answer flips the
     previous row's is_current to FALSE and INSERTs a new row, so the whole
     scoring history stays queryable (CLAUDE_CONTEXT.md §5 rule 2). The
     actual SQL lives in core/answer_evaluation.record_evaluation(), shared
     with scripts/evaluate_answer.py and scripts/evaluate_diagram_answer.py —
     this module validates and delegates rather than writing a second INSERT
     that could drift from the first.

  2. POLYMORPHIC REFERENCE. Since migration 012, exactly one of
     reference_answer_variant_id (text answers) / reference_asset_id
     (diagram answers) is set per row — never both, never neither. The DB
     enforces this too (chk_evaluation_results_one_reference), but it's
     checked here FIRST so a caller gets a clear Python error naming the
     mistake instead of an IntegrityError from a constraint, and gets it
     before any statement runs.

  3. evaluator_model (migration 009) and metrics JSONB (migration 010) are
     always populated, from the result's own metrics dict — an unattributed
     score in an append-only ledger can't be compared against later ones.

  4. RLS FAILS CLOSED AND SILENTLY. answer_blocks/answers/evaluation_results
     are RLS-protected (migration 003). A transaction that forgot
     `SET LOCAL app.current_college_id` or `app.is_platform_admin` doesn't
     error — every SELECT just returns zero rows, which reads exactly like
     "that answer doesn't exist" (CLAUDE_CONTEXT.md §6, §10). So the lookup
     below treats "zero rows" as LOUD and explicitly reports whether the RLS
     GUCs were set, instead of letting a misconfigured tenant context look
     like missing data — and, crucially, instead of writing an orphaned row.
"""
from __future__ import annotations

from core import answer_evaluation, db
from core.plugins.base import REQUIRED_METRIC_KEYS, EvaluationResult


class RLSVisibilityError(RuntimeError):
    """A query that must have matched a row returned zero.

    Its own exception type because the fix differs from a plain "bad id":
    almost always the caller forgot to set the RLS GUC for the transaction,
    and RLS gives no other signal (CLAUDE_CONTEXT.md §6 — fails closed,
    silently). Never swallow this into a "no data" code path.
    """


def _rls_context(cur) -> str:
    """Human-readable description of the transaction's RLS settings, for
    error messages. Uses current_setting(..., true) — the missing_ok form —
    so an unset GUC comes back NULL instead of raising."""
    cur.execute("""
        SELECT current_setting('app.current_college_id', true),
               current_setting('app.is_platform_admin', true)
    """)
    college_id, is_admin = cur.fetchone()
    if is_admin and is_admin.lower() == "true":
        return "app.is_platform_admin='true' (platform admin)"
    if college_id:
        return f"app.current_college_id={college_id!r}"
    return "NEITHER app.current_college_id NOR app.is_platform_admin is set"


def _validate_reference(reference_answer_variant_id, reference_asset_id) -> None:
    """Enforces migration 012's XOR in Python, before touching the DB."""
    has_variant = reference_answer_variant_id is not None
    has_asset = reference_asset_id is not None

    if has_variant and has_asset:
        raise ValueError(
            "evaluation_results is polymorphic over its reference (migration 012): "
            "pass EITHER reference_answer_variant_id (text answers) OR "
            "reference_asset_id (diagram answers), not both. Got both "
            f"reference_answer_variant_id={reference_answer_variant_id!r} and "
            f"reference_asset_id={reference_asset_id!r}."
        )
    if not has_variant and not has_asset:
        raise ValueError(
            "evaluation_results requires exactly one reference (migration 012): "
            "pass reference_answer_variant_id (text answers) or reference_asset_id "
            "(diagram answers). Got neither."
        )


def _validate_metrics(result: EvaluationResult) -> None:
    """Every persisted score must be attributable to the plugin and version
    that produced it — see core/plugins/base.py's REQUIRED_METRIC_KEYS."""
    missing = [key for key in REQUIRED_METRIC_KEYS if key not in result.metrics]
    if missing:
        raise ValueError(
            f"EvaluationResult.metrics is missing required key(s) {missing}. "
            f"evaluation_results.metrics must always carry {list(REQUIRED_METRIC_KEYS)} "
            f"so a row in this append-only ledger can be traced back to the exact "
            f"plugin version that scored it. Got keys: {sorted(result.metrics)}."
        )


def write_evaluation_result(
    conn,
    *,
    answer_block_id: str,
    result: EvaluationResult,
    reference_answer_variant_id: str | None = None,
    reference_asset_id: str | None = None,
    dry_run: bool = False,
) -> str:
    """Appends one EvaluationResult to the evaluation_results ledger for the
    answer that owns `answer_block_id`. Returns the new evaluation_id.

    The caller owns the connection and MUST have already set the RLS context
    on this transaction (`SET LOCAL app.current_college_id = '<uuid>'`, or
    `SET LOCAL app.is_platform_admin = 'true'` for system-level jobs like the
    evaluation runners) — this function doesn't set it, only detects and
    reports its absence (see the module docstring, rule 4).

    dry_run=True does all the same validation and SQL, then ROLLS BACK
    instead of committing, matching every writer script's --dry-run flag
    (CLAUDE_CONTEXT.md §5 rule 6). It is the only way to exercise the trigger
    and constraint checks without persisting.

    Raises:
        ValueError          — both or neither reference FK, or metrics missing
                              a required key. Raised before any SQL runs.
        RLSVisibilityError  — the answer_block lookup returned zero rows,
                              which usually means the RLS GUC wasn't set.
    """
    _validate_reference(reference_answer_variant_id, reference_asset_id)
    _validate_metrics(result)

    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT ab.answer_id, ab.block_type
            FROM answer_blocks ab
            JOIN answers a ON a.answer_id = ab.answer_id
            WHERE ab.block_id = %s
        """, (answer_block_id,))
        row = cur.fetchone()

        if row is None:
            # Do NOT treat this as "no data" — under RLS that's exactly what a
            # missing tenant context looks like (CLAUDE_CONTEXT.md §6, §10).
            raise RLSVisibilityError(
                f"answer_block {answer_block_id!r} returned zero rows. answer_blocks is "
                f"RLS-protected and fails closed SILENTLY, so this is EITHER a "
                f"nonexistent/invalid block_id OR a missing tenant context on this "
                f"transaction. RLS context here: {_rls_context(cur)}. If that reads "
                f"'NEITHER ... is set', that is the bug: run "
                f"\"SET LOCAL app.current_college_id = '<college-uuid>'\" (tenant "
                f"request) or \"SET LOCAL app.is_platform_admin = 'true'\" "
                f"(system-level job) before calling this."
            )

        answer_id, _block_type = row

        evaluation_id = answer_evaluation.record_evaluation(
            cur,
            answer_id=answer_id,
            reference_answer_variant_id=reference_answer_variant_id,
            reference_asset_id=reference_asset_id,
            evaluator_model=result.metrics["evaluator_model"],
            score=result.score,
            explanation=result.explanation,
            metrics=result.metrics,
            evaluator_type="ai",
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    db.end_transaction(conn, dry_run=dry_run)
    return evaluation_id
