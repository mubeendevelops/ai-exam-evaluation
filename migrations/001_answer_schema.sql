-- ============================================================================
-- Migration: 001_answer_schema.sql
-- Scope:     Answer Evaluation side of the AI Exam Evaluation Platform
--            (answer-schema-design.md / PROJECT_CONTEXT.md §5)
--
-- This script is idempotent: it can be re-run safely (CREATE TABLE IF NOT
-- EXISTS, guarded CREATE TYPE, CREATE OR REPLACE FUNCTION, DROP TRIGGER IF
-- EXISTS + CREATE TRIGGER, CREATE INDEX IF NOT EXISTS).
--
-- PLACEHOLDER TABLES:
--   `questions` and `reference_answer_variants` are minimal stand-ins for
--   the real Question schema (PROJECT_CONTEXT.md §4), which is owned by a
--   separate migration. They exist here ONLY so the answer-schema FKs and
--   the two cross-schema integrity triggers below can resolve. They should
--   be dropped/replaced wholesale when the real Question schema migration
--   lands — see README.md.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Extensions
-- ----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

-- ----------------------------------------------------------------------------
-- Enums (guarded CREATE TYPE — Postgres has no CREATE TYPE IF NOT EXISTS)
-- ----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE question_status AS ENUM ('draft', 'confirmed', 'rejected', 'live', 'superseded');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE exam_status AS ENUM ('draft', 'live', 'closed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE answer_status AS ENUM ('pending_evaluation', 'ai_scored', 'sme_reviewed', 'finalized', 'flagged');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE answer_block_type AS ENUM ('text', 'diagram', 'table', 'formula');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE reviewer_role AS ENUM ('teacher', 'sme', 'admin');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE evaluator_type AS ENUM ('ai', 'sme');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE answer_review_action AS ENUM ('confirmed', 'overridden', 'flagged');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- PLACEHOLDER TABLES (stand-ins for the future Question schema migration)
-- ============================================================================

-- PLACEHOLDER for questions(question_id PK, ...) — full shape defined in
-- PROJECT_CONTEXT.md §4. Only the columns needed to resolve answer-schema
-- FKs and the "must be live" integrity trigger are included here.
CREATE TABLE IF NOT EXISTS questions (
    question_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status      question_status NOT NULL DEFAULT 'draft'
);

-- PLACEHOLDER for reference_answer_variants(variant_id PK, question_id FK, ...)
-- Only question_id is included, since it's required to validate the
-- "variant must belong to the same question as the answer" trigger.
CREATE TABLE IF NOT EXISTS reference_answer_variants (
    variant_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id UUID NOT NULL REFERENCES questions(question_id)
);

CREATE INDEX IF NOT EXISTS idx_reference_answer_variants_question_id
    ON reference_answer_variants (question_id);

-- ============================================================================
-- ANSWER SCHEMA — 8 tables, in dependency order
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. students
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS students (
    student_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    roll_number  TEXT NOT NULL UNIQUE,
    email        TEXT NOT NULL,
    enrolled_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 2. exams
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exams (
    exam_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    conducted_at  TIMESTAMPTZ,
    status        exam_status NOT NULL DEFAULT 'draft'
);

-- ----------------------------------------------------------------------------
-- 3. reviewers
--    Shared table: also referenced by question_reviews / question_status_history
--    in the (not-yet-created) Question schema.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reviewers (
    reviewer_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    role         reviewer_role NOT NULL,
    email        TEXT NOT NULL
);

-- ----------------------------------------------------------------------------
-- 4. answers
--    question_id is a cross-schema FK into the placeholder `questions` table.
--    ON DELETE RESTRICT reflects the §7 recommendation: a question with
--    existing answers should not be hard-deletable (not yet a confirmed
--    decision — see README "Open items").
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answers (
    answer_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id      UUID NOT NULL REFERENCES questions(question_id) ON DELETE RESTRICT,
    student_id       UUID NOT NULL REFERENCES students(student_id),
    exam_id          UUID NOT NULL REFERENCES exams(exam_id),
    source_scan_url  TEXT NOT NULL,
    text_extracted   TEXT,
    status           answer_status NOT NULL DEFAULT 'pending_evaluation',
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 5. answer_blocks
--    blob_url nullable (null for text blocks), content nullable (null for
--    diagram/table/formula blocks) — exactly as specified, not mutually
--    exclusive-enforced since the doc doesn't ask for that constraint.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answer_blocks (
    block_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id         UUID NOT NULL REFERENCES answers(answer_id),
    block_type        answer_block_type NOT NULL,
    blob_url          TEXT,
    content           TEXT,
    sequence_order    INT NOT NULL,
    confidence_score  REAL
);

-- ----------------------------------------------------------------------------
-- 6. evaluation_results
--    answer_id is deliberately NOT unique (append-only ledger; `is_current`
--    flags the active row). reference_answer_variant_id is a cross-schema FK.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evaluation_results (
    evaluation_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id                      UUID NOT NULL REFERENCES answers(answer_id),
    reference_answer_variant_id    UUID NOT NULL REFERENCES reference_answer_variants(variant_id),
    evaluator_type                 evaluator_type NOT NULL,
    score                          REAL NOT NULL,
    explanation                    TEXT,
    is_current                     BOOLEAN NOT NULL DEFAULT true,
    evaluated_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 7. answer_reviews
--    final_marks nullable (null when action = flagged, per doc).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answer_reviews (
    review_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id     UUID NOT NULL REFERENCES answers(answer_id),
    reviewer_id   UUID NOT NULL REFERENCES reviewers(reviewer_id),
    action        answer_review_action NOT NULL,
    final_marks   REAL,
    comment       TEXT,
    reviewed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ----------------------------------------------------------------------------
-- 8. answer_status_history
--    changed_by nullable (null when system-driven, e.g. AI scoring).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS answer_status_history (
    history_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id    UUID NOT NULL REFERENCES answers(answer_id),
    old_status   TEXT NOT NULL,
    new_status   TEXT NOT NULL,
    changed_by   UUID REFERENCES reviewers(reviewer_id),
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Indexes — every FK column, plus evaluation_results.is_current and
-- answers.status as explicitly requested.
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_answers_question_id            ON answers (question_id);
CREATE INDEX IF NOT EXISTS idx_answers_student_id              ON answers (student_id);
CREATE INDEX IF NOT EXISTS idx_answers_exam_id                 ON answers (exam_id);
CREATE INDEX IF NOT EXISTS idx_answers_status                  ON answers (status);

CREATE INDEX IF NOT EXISTS idx_answer_blocks_answer_id         ON answer_blocks (answer_id);

CREATE INDEX IF NOT EXISTS idx_evaluation_results_answer_id    ON evaluation_results (answer_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_results_variant_id   ON evaluation_results (reference_answer_variant_id);
CREATE INDEX IF NOT EXISTS idx_evaluation_results_is_current   ON evaluation_results (is_current);

CREATE INDEX IF NOT EXISTS idx_answer_reviews_answer_id        ON answer_reviews (answer_id);
CREATE INDEX IF NOT EXISTS idx_answer_reviews_reviewer_id      ON answer_reviews (reviewer_id);

CREATE INDEX IF NOT EXISTS idx_answer_status_history_answer_id ON answer_status_history (answer_id);
CREATE INDEX IF NOT EXISTS idx_answer_status_history_changed_by ON answer_status_history (changed_by);

-- ============================================================================
-- Cross-schema integrity triggers (PROJECT_CONTEXT.md §6)
--
-- STOPGAP NOTICE: both triggers reference the PLACEHOLDER `questions` and
-- `reference_answer_variants` tables defined above. When the real Question
-- schema migration replaces those placeholders, these triggers should keep
-- working unchanged as long as `question_id` / `status` / `variant_id` keep
-- the same names and types — but re-verify at that time.
-- ============================================================================

-- Rule (a): an `answers` row may only be created/repointed against a
-- `questions` row whose status = 'live'. Plain FKs can't express this.
CREATE OR REPLACE FUNCTION fn_check_answer_question_is_live()
RETURNS TRIGGER AS $$
DECLARE
    q_status question_status;
BEGIN
    SELECT status INTO q_status FROM questions WHERE question_id = NEW.question_id;

    IF q_status IS NULL THEN
        RAISE EXCEPTION 'answers.question_id % does not exist in questions', NEW.question_id;
    END IF;

    IF q_status <> 'live' THEN
        RAISE EXCEPTION
            'Cannot create/update answer against question % with status %; question must be live',
            NEW.question_id, q_status;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_answers_question_must_be_live ON answers;
CREATE TRIGGER trg_answers_question_must_be_live
    BEFORE INSERT OR UPDATE OF question_id ON answers
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_answer_question_is_live();

-- Rule (b): evaluation_results.reference_answer_variant_id must belong to
-- the same question_id as the answers row it evaluates. Two independently
-- valid FKs (answer_id -> answers.question_id, variant_id ->
-- reference_answer_variants.question_id) can otherwise point at mismatched
-- questions.
CREATE OR REPLACE FUNCTION fn_check_evaluation_variant_matches_answer_question()
RETURNS TRIGGER AS $$
DECLARE
    answer_question_id  UUID;
    variant_question_id UUID;
BEGIN
    SELECT question_id INTO answer_question_id
    FROM answers WHERE answer_id = NEW.answer_id;

    IF answer_question_id IS NULL THEN
        RAISE EXCEPTION 'evaluation_results.answer_id % does not exist in answers', NEW.answer_id;
    END IF;

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

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_evaluation_variant_matches_answer_question ON evaluation_results;
CREATE TRIGGER trg_evaluation_variant_matches_answer_question
    BEFORE INSERT OR UPDATE OF answer_id, reference_answer_variant_id ON evaluation_results
    FOR EACH ROW
    EXECUTE FUNCTION fn_check_evaluation_variant_matches_answer_question();

COMMIT;