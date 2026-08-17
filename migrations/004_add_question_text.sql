-- ============================================================================
-- Migration: 004_add_question_text.sql
-- Fixes a real gap in question-schema-design.md: the `questions` table as
-- specified has no column for the question's actual text. source_type /
-- source_id point at where a question was generated FROM, but nothing
-- stores what the question actually says — a silent problem for
-- sentence/paragraph/diagram/table/formula-sourced questions (arguably
-- regenerable from source + a prompt template) and a hard blocker for
-- source_type = 'manual' questions, which have no source to regenerate
-- from at all.
--
-- PREREQUISITE: 001, 002, 003 already applied.
-- Idempotent, safe to re-run.
-- ============================================================================

BEGIN;

-- Named `content` to match the naming convention already used elsewhere in
-- this schema for "the text of the thing" (paragraphs.content,
-- sentences.content, reference_answer_variants.content) rather than
-- introducing a new naming style.
ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS content TEXT;

-- Guarded NOT NULL enforcement, same pattern as 002_question_schema.sql:
-- a question row without any text isn't meaningfully a question, so this
-- should end up NOT NULL — but existing rows (already loaded without this
-- column) would violate that immediately. Backfill first (re-run your
-- loader script — it's idempotent and will UPDATE existing rows with the
-- real text), THEN either re-run this migration or run the ALTER manually.
DO $$
DECLARE
    null_count INT;
BEGIN
    SELECT COUNT(*) INTO null_count FROM questions WHERE content IS NULL;
    IF null_count = 0 THEN
        ALTER TABLE questions ALTER COLUMN content SET NOT NULL;
    ELSE
        RAISE NOTICE
            'questions has % row(s) with content still NULL; skipped NOT NULL enforcement. Backfill (e.g. re-run load_exam_bank.py, which now sends content on every insert/update), then run: ALTER TABLE questions ALTER COLUMN content SET NOT NULL;',
            null_count;
    END IF;
END $$;

COMMIT;
