-- ============================================================================
-- Migration: 005_question_tree_and_ai_flag.sql
-- Two additions to the questions table:
--
-- 1. is_ai_generated (boolean, nullable)
--    Distinguishes HOW a question was created (human vs AI), which
--    source_type alone cannot express. source_type = "paragraph" means
--    "derived from a paragraph" — it says nothing about whether a teacher
--    wrote it or an LLM generated it. Nullable: NULL = unknown/legacy
--    (questions loaded before this migration have no creation context).
--
-- 2. parent_question_id (FK → questions, nullable, self-referential)
--    Tracks the DERIVATION TREE — which question a reworded/variant
--    question was branched from. Distinct from supersedes_question_id:
--      supersedes_question_id = strict replacement (original retires,
--        status flips to 'superseded' — used when correcting an error
--        in a live question)
--      parent_question_id = creative derivation (original stays active
--        and usable — used for rewording, difficulty variants, style
--        changes)
--    Multiple children can share the same parent, forming a real tree.
--    Traversable with a recursive CTE (see view below).
--
-- PREREQUISITE: 001–004 already applied. Idempotent, safe to re-run.
-- ============================================================================

BEGIN;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS is_ai_generated BOOLEAN,
    ADD COLUMN IF NOT EXISTS parent_question_id UUID REFERENCES questions(question_id);

CREATE INDEX IF NOT EXISTS idx_questions_parent_question_id ON questions (parent_question_id);
CREATE INDEX IF NOT EXISTS idx_questions_is_ai_generated    ON questions (is_ai_generated);

-- ============================================================================
-- Convenience view: question_tree
-- Returns every question with its full ancestry path and depth in the
-- derivation tree. Use this in reporting/UI queries to render the tree
-- without re-writing the recursive CTE each time.
--
-- Example query — get full tree rooted at a specific question:
--   SELECT * FROM question_tree WHERE root_question_id = '<uuid>';
--
-- Example query — get all children of a question (direct + indirect):
--   SELECT * FROM question_tree WHERE root_question_id = '<uuid>' AND depth > 0;
-- ============================================================================
CREATE OR REPLACE VIEW question_tree AS
WITH RECURSIVE tree AS (
    -- Base: root questions (no parent)
    SELECT
        question_id,
        parent_question_id,
        content,
        style,
        marks_max,
        status,
        is_ai_generated,
        question_group_id,
        question_id         AS root_question_id,
        ARRAY[question_id]  AS path,        -- ancestry chain as an array of UUIDs
        0                   AS depth
    FROM questions
    WHERE parent_question_id IS NULL

    UNION ALL

    -- Recursive: children
    SELECT
        q.question_id,
        q.parent_question_id,
        q.content,
        q.style,
        q.marks_max,
        q.status,
        q.is_ai_generated,
        q.question_group_id,
        t.root_question_id,
        t.path || q.question_id,
        t.depth + 1
    FROM questions q
    JOIN tree t ON q.parent_question_id = t.question_id
)
SELECT * FROM tree;

COMMIT;
