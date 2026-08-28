-- =============================================================================
-- Migration 012 — Diagram Evaluation support on evaluation_results (Task 4)
-- =============================================================================
--
-- Why: evaluation_results (001_answer_schema.sql) assumes every evaluation
-- scores an answer's TEXT against a reference_answer_variants row. Task 4
-- (handwritten vs. digital diagram comparison) evaluates an answer_blocks
-- diagram against a content_assets row instead — there is no
-- reference_answer_variant on the diagram side.
--
-- Design decision (plan.md §4.1, Option A — approved): rather than creating
-- a parallel diagram_evaluation_results table (which would duplicate
-- evaluation_id/answer_id/evaluator_type/score/explanation/is_current/
-- evaluator_model/metrics wholesale), evaluation_results is made polymorphic
-- over its reference: exactly one of reference_answer_variant_id /
-- reference_asset_id is set per row. This keeps "what is the current score
-- for this answer" a single-table query regardless of answer type, and
-- follows PROJECT_CONTEXT.md §8 rule #3 (one generalized table over
-- duplicating the same shape per type) rather than fighting it.
--
-- The full structured diagram comparison (node validation, edge comparison,
-- missing information, anomalies, glossary matches — see plan.md §5) is not
-- given new columns here: it lands in the existing `metrics JSONB` column
-- (added in 010_evaluation_metrics_column.sql), which was already the
-- "structured extra detail" column by convention.
--
-- Run AFTER: 001 through 011.
-- Idempotent: ADD COLUMN IF NOT EXISTS, guarded ADD CONSTRAINT,
-- DROP TRIGGER/FUNCTION IF EXISTS before recreating.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Schema changes
-- -----------------------------------------------------------------------------

-- reference_answer_variant_id must become nullable — a diagram evaluation
-- row will have this NULL and reference_asset_id set instead.
ALTER TABLE evaluation_results
    ALTER COLUMN reference_answer_variant_id DROP NOT NULL;

ALTER TABLE evaluation_results
    ADD COLUMN IF NOT EXISTS reference_asset_id UUID REFERENCES content_assets(asset_id);

CREATE INDEX IF NOT EXISTS idx_evaluation_results_reference_asset_id
    ON evaluation_results (reference_asset_id);

-- Exactly one of the two reference columns must be set — never both, never
-- neither. Guarded: ADD CONSTRAINT has no IF NOT EXISTS in Postgres.
DO $$ BEGIN
    ALTER TABLE evaluation_results
        ADD CONSTRAINT chk_evaluation_results_one_reference
        CHECK (
            (reference_answer_variant_id IS NOT NULL AND reference_asset_id IS NULL)
            OR
            (reference_answer_variant_id IS NULL AND reference_asset_id IS NOT NULL)
        );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMENT ON COLUMN evaluation_results.reference_answer_variant_id IS
    'Set for TEXT-answer evaluations (Task 3). Mutually exclusive with '
    'reference_asset_id — see chk_evaluation_results_one_reference.';
COMMENT ON COLUMN evaluation_results.reference_asset_id IS
    'Set for DIAGRAM-answer evaluations (Task 4) — references the reference '
    '(digital) diagram in content_assets. Mutually exclusive with '
    'reference_answer_variant_id — see chk_evaluation_results_one_reference.';

-- -----------------------------------------------------------------------------
-- 2. Replace the cross-schema integrity trigger
--
-- The original trg_evaluation_variant_matches_answer_question /
-- fn_check_evaluation_variant_matches_answer_question (001_answer_schema.sql)
-- only knew about reference_answer_variant_id and would reject every row
-- with it NULL. Replaced with a version that validates whichever reference
-- column is populated:
--   - reference_answer_variant_id set: unchanged behaviour from 001 — the
--     variant's question_id must match the answer's question_id.
--   - reference_asset_id set: the asset must be linked to the SAME question
--     as the answer via question_asset_links(role='question_source') — the
--     diagram equivalent of the same rule.
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_check_evaluation_reference_matches_answer_question()
RETURNS TRIGGER AS $$
DECLARE
    answer_question_id  UUID;
    variant_question_id UUID;
    asset_question_id   UUID;
BEGIN
    SELECT question_id INTO answer_question_id
    FROM answers WHERE answer_id = NEW.answer_id;

    IF answer_question_id IS NULL THEN
        RAISE EXCEPTION 'evaluation_results.answer_id % does not exist in answers', NEW.answer_id;
    END IF;

    IF NEW.reference_answer_variant_id IS NOT NULL THEN
        SELECT question_id INTO variant_question_id
        FROM reference_answer_variants WHERE variant_id = NEW.reference_answer_variant_id;

        IF variant_question_id IS NULL THEN
            RAISE EXCEPTION
                'evaluation_results.reference_answer_variant_id % does not exist in reference_answer_variants',
                NEW.reference_answer_variant_id;
        END IF;

        IF answer_question_id <> variant_question_id THEN
            RAISE EXCEPTION
                'reference_answer_variant % belongs to question %, but answer % belongs to question %',
                NEW.reference_answer_variant_id, variant_question_id, NEW.answer_id, answer_question_id;
        END IF;

    ELSIF NEW.reference_asset_id IS NOT NULL THEN
        SELECT question_id INTO asset_question_id
        FROM question_asset_links
        WHERE asset_id = NEW.reference_asset_id
          AND role = 'question_source'
          AND question_id IS NOT NULL
        LIMIT 1;

        IF asset_question_id IS NULL THEN
            RAISE EXCEPTION
                'evaluation_results.reference_asset_id % is not linked to any question via question_asset_links(role=question_source)',
                NEW.reference_asset_id;
        END IF;

        IF answer_question_id <> asset_question_id THEN
            RAISE EXCEPTION
                'reference_asset % belongs to question %, but answer % belongs to question %',
                NEW.reference_asset_id, asset_question_id, NEW.answer_id, answer_question_id;
        END IF;

    ELSE
        -- Defense in depth: chk_evaluation_results_one_reference should
        -- already reject this, but a trigger fires before CHECK constraints
        -- are (re-)validated in some update orderings, so check explicitly.
        RAISE EXCEPTION
            'evaluation_results row for answer % must have exactly one of '
            'reference_answer_variant_id or reference_asset_id set',
            NEW.answer_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_evaluation_variant_matches_answer_question ON evaluation_results;
DROP FUNCTION IF EXISTS fn_check_evaluation_variant_matches_answer_question();

DROP TRIGGER IF EXISTS trg_evaluation_reference_matches_answer_question ON evaluation_results;
CREATE TRIGGER trg_evaluation_reference_matches_answer_question
    BEFORE INSERT OR UPDATE OF answer_id, reference_answer_variant_id, reference_asset_id
    ON evaluation_results
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_evaluation_reference_matches_answer_question();

COMMIT;
