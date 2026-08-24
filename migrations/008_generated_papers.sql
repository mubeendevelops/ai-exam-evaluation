-- =============================================================================
-- Migration 008 — Generated Papers Schema
-- =============================================================================
--
-- What this adds
-- ──────────────
-- Three tables linking a paper pattern to actual questions from the bank:
--
--   generated_papers   – a concrete exam paper created from a pattern
--   paper_sections     – per-section metadata (choose_count) for this paper
--   paper_questions    – the actual question→slot assignments
--
-- Design decisions
-- ─────────────────
-- ① Papers are SHARED across all colleges → NO college_id, NO RLS.
--    Same scope as paper_patterns and questions.
--
-- ② choose_count lives HERE (on paper_sections), NOT on pattern_sections.
--    The pattern only records whether a section is mandatory/optional;
--    the generated paper records the actual choose_count used.
--    (See migration 006 design note ②.)
--
-- ③ Only questions with status='live' may be assigned to a paper slot.
--    Enforced by trigger (trg_paper_question_must_be_live), mirroring
--    trg_answers_question_must_be_live from 001_answer_schema.sql.
--
-- ④ A question may not appear twice in the same paper.
--    UNIQUE(paper_id, question_id) on paper_questions enforces this.
--
-- ⑤ Each slot may be filled at most once per paper.
--    UNIQUE(paper_section_id, slot_id) on paper_questions enforces this.
--
-- ⑥ Paper status is 'draft' or 'finalized'. Draft papers can be edited
--    (slots reassigned); finalized papers are locked. Finalization is a
--    separate step (not implemented in this migration — handled by the
--    script layer, same pattern as question status transitions).
--
-- Run AFTER: 001 through 007.
-- =============================================================================

BEGIN;

-- =============================================================================
-- 1. generated_papers
-- =============================================================================

DO $$ BEGIN
    CREATE TYPE paper_status AS ENUM ('draft', 'finalized');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS generated_papers (
    paper_id        UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    pattern_id      UUID            NOT NULL
                                    REFERENCES paper_patterns(pattern_id)
                                    ON DELETE RESTRICT,

    name            TEXT            NOT NULL,
    status          paper_status    NOT NULL DEFAULT 'draft',

    generated_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- NULL = system-generated; non-NULL = teacher who created this paper.
    generated_by    UUID            REFERENCES reviewers(reviewer_id)
                                    ON DELETE SET NULL
);

COMMENT ON TABLE  generated_papers IS
    'A concrete exam paper created by filling a paper_pattern with live questions.';
COMMENT ON COLUMN generated_papers.pattern_id IS
    'The pattern template this paper was generated from.';
COMMENT ON COLUMN generated_papers.generated_by IS
    'NULL = system-generated; non-NULL = teacher/coordinator who created this paper.';
COMMENT ON COLUMN generated_papers.status IS
    'draft = editable (slots can be reassigned); finalized = locked.';


-- =============================================================================
-- 2. paper_sections
-- =============================================================================

CREATE TABLE IF NOT EXISTS paper_sections (
    paper_section_id    UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_id            UUID    NOT NULL
                                REFERENCES generated_papers(paper_id)
                                ON DELETE CASCADE,
    section_id          UUID    NOT NULL
                                REFERENCES pattern_sections(section_id)
                                ON DELETE RESTRICT,

    -- How many of this section's slots a student must answer.
    -- For mandatory sections: equals the total number of top-level slots.
    -- For optional sections: typically 1 ("answer Q3 or Q4"), but can be
    -- higher ("answer 2 of 3") — set at generation time.
    choose_count        INT     NOT NULL CHECK (choose_count >= 1),

    UNIQUE (paper_id, section_id)
);

COMMENT ON TABLE  paper_sections IS
    'Per-section metadata for a generated paper. Records the choose_count '
    'that was decided at generation time (not stored on the pattern).';
COMMENT ON COLUMN paper_sections.choose_count IS
    'Number of slots a student must answer in this section. '
    'For mandatory sections = total top-level slot count. '
    'For optional sections = typically 1 (either/or).';


-- =============================================================================
-- 3. paper_questions
-- =============================================================================

CREATE TABLE IF NOT EXISTS paper_questions (
    paper_question_id   UUID    PRIMARY KEY DEFAULT gen_random_uuid(),
    paper_section_id    UUID    NOT NULL
                                REFERENCES paper_sections(paper_section_id)
                                ON DELETE CASCADE,
    slot_id             UUID    NOT NULL
                                REFERENCES pattern_slots(slot_id)
                                ON DELETE RESTRICT,
    question_id         UUID    NOT NULL
                                REFERENCES questions(question_id)
                                ON DELETE RESTRICT,

    -- Each slot filled at most once per paper section.
    UNIQUE (paper_section_id, slot_id)
);

COMMENT ON TABLE  paper_questions IS
    'Maps pattern slots to actual questions for a generated paper.';


-- No-duplicate: a question can't appear twice in the same paper.
-- This is a cross-section constraint — paper_id lives on paper_sections,
-- not paper_questions, so a simple column-level UNIQUE can't express it.
-- Enforced by trg_paper_question_must_be_live trigger below instead.


-- =============================================================================
-- 4. Trigger: question must be 'live'
-- =============================================================================

CREATE OR REPLACE FUNCTION trg_fn_paper_question_must_be_live()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    q_status question_status;
    v_paper_id UUID;
    dup_count INT;
BEGIN
    -- Check question is live
    SELECT status INTO q_status
    FROM   questions
    WHERE  question_id = NEW.question_id;

    IF q_status IS NULL THEN
        RAISE EXCEPTION
            'paper_questions: question_id (%) does not exist',
            NEW.question_id;
    END IF;

    IF q_status != 'live' THEN
        RAISE EXCEPTION
            'paper_questions: question (%) has status=''%'', must be ''live''',
            NEW.question_id, q_status;
    END IF;

    -- Check no duplicate question in the same paper (cross-section)
    SELECT ps.paper_id INTO v_paper_id
    FROM   paper_sections ps
    WHERE  ps.paper_section_id = NEW.paper_section_id;

    SELECT COUNT(*) INTO dup_count
    FROM   paper_questions pq
    JOIN   paper_sections ps ON ps.paper_section_id = pq.paper_section_id
    WHERE  ps.paper_id = v_paper_id
      AND  pq.question_id = NEW.question_id
      AND  pq.paper_question_id IS DISTINCT FROM NEW.paper_question_id;

    IF dup_count > 0 THEN
        RAISE EXCEPTION
            'paper_questions: question (%) is already assigned to another '
            'slot in paper (%)',
            NEW.question_id, v_paper_id;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_paper_question_must_be_live
    BEFORE INSERT OR UPDATE ON paper_questions
    FOR EACH ROW EXECUTE FUNCTION trg_fn_paper_question_must_be_live();


-- =============================================================================
-- 5. Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_generated_papers_pattern
    ON generated_papers (pattern_id);

CREATE INDEX IF NOT EXISTS idx_generated_papers_status
    ON generated_papers (status)
    WHERE status = 'draft';

CREATE INDEX IF NOT EXISTS idx_paper_sections_paper
    ON paper_sections (paper_id);

CREATE INDEX IF NOT EXISTS idx_paper_questions_section
    ON paper_questions (paper_section_id);

CREATE INDEX IF NOT EXISTS idx_paper_questions_question
    ON paper_questions (question_id);


COMMIT;
