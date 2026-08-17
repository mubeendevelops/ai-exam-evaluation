-- ============================================================================
-- Migration: 003_multi_tenancy.sql
-- Scope:     Introduces multi-tenancy (multiple colleges on one platform).
--
-- Design: Question schema (12 tables) stays UNCHANGED — shared question
-- bank, no tenant column, readable/usable by every college. Answer schema
-- (8 tables) becomes single-tenant: every table gets `college_id`, enforced
-- both by triggers (data consistency) and Postgres Row-Level Security
-- (defense-in-depth isolation).
--
-- PREREQUISITE: 001_answer_schema.sql and 002_question_schema.sql already
-- applied.
--
-- This script is idempotent and safe to re-run.
-- ============================================================================

BEGIN;

-- ============================================================================
-- 0. colleges — the tenant table (new; not in either original schema doc,
-- required to introduce multi-tenancy at all).
-- ============================================================================
DO $$ BEGIN
    CREATE TYPE college_status AS ENUM ('active', 'suspended');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS colleges (
    college_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    short_code  TEXT NOT NULL UNIQUE,   -- e.g. login/subdomain routing key
    status      college_status NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Bootstrap a "legacy" tenant to own any rows that existed before this
-- migration (single-tenant era). Real colleges get onboarded as separate
-- rows afterward by the application/admin flow.
DO $$
DECLARE
    legacy_college_id UUID;
BEGIN
    SELECT college_id INTO legacy_college_id FROM colleges WHERE short_code = 'legacy';
    IF legacy_college_id IS NULL THEN
        INSERT INTO colleges (name, short_code, status)
        VALUES ('Legacy / Pre-Multi-Tenant Data', 'legacy', 'active');
    END IF;
END $$;

-- ============================================================================
-- 1. reviewers — add NULLABLE college_id.
--    NULL   = platform-level reviewer/SME, eligible to review the shared
--             question bank (question_reviews) regardless of tenant.
--    non-NULL = belongs to one college; used for that college's answer_reviews.
--    Question-schema tables (question_reviews, question_status_history) are
--    NOT tenant-filtered — any reviewer, global or college-scoped, can act
--    on the shared bank, matching "questions stay shared."
-- ============================================================================
ALTER TABLE reviewers
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);

CREATE INDEX IF NOT EXISTS idx_reviewers_college_id ON reviewers (college_id);

-- ============================================================================
-- 2. students — add college_id, backfill, enforce NOT NULL, fix uniqueness.
-- ============================================================================
ALTER TABLE students
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);

DO $$
DECLARE
    legacy_college_id UUID;
BEGIN
    SELECT college_id INTO legacy_college_id FROM colleges WHERE short_code = 'legacy';
    UPDATE students SET college_id = legacy_college_id WHERE college_id IS NULL;
END $$;

ALTER TABLE students ALTER COLUMN college_id SET NOT NULL;

-- roll_number was globally unique; two colleges will legitimately share
-- roll numbers, so uniqueness must be scoped to (college_id, roll_number).
-- Constraint name below is Postgres's default auto-generated name for the
-- original inline UNIQUE on roll_number in 001_answer_schema.sql — verify
-- with \d students if this DROP is a no-op in your environment.
ALTER TABLE students DROP CONSTRAINT IF EXISTS students_roll_number_key;
DO $$ BEGIN
    ALTER TABLE students ADD CONSTRAINT students_college_id_roll_number_key UNIQUE (college_id, roll_number);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_students_college_id ON students (college_id);

-- ============================================================================
-- 3. exams — add college_id, backfill, enforce NOT NULL.
-- ============================================================================
ALTER TABLE exams
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);

DO $$
DECLARE
    legacy_college_id UUID;
BEGIN
    SELECT college_id INTO legacy_college_id FROM colleges WHERE short_code = 'legacy';
    UPDATE exams SET college_id = legacy_college_id WHERE college_id IS NULL;
END $$;

ALTER TABLE exams ALTER COLUMN college_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_exams_college_id ON exams (college_id);

-- ============================================================================
-- 4. answers — add college_id, backfill, enforce NOT NULL. college_id is
-- then DERIVED (not trusted from app input) via trigger below: it's always
-- set to the referenced student's college_id, and rejected if the
-- referenced exam belongs to a different college.
-- ============================================================================
ALTER TABLE answers
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);

DO $$
DECLARE
    legacy_college_id UUID;
BEGIN
    SELECT college_id INTO legacy_college_id FROM colleges WHERE short_code = 'legacy';
    UPDATE answers SET college_id = legacy_college_id WHERE college_id IS NULL;
END $$;

ALTER TABLE answers ALTER COLUMN college_id SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_answers_college_id ON answers (college_id);

CREATE OR REPLACE FUNCTION fn_derive_and_check_answer_college()
RETURNS TRIGGER AS $$
DECLARE
    student_college UUID;
    exam_college    UUID;
BEGIN
    SELECT college_id INTO student_college FROM students WHERE student_id = NEW.student_id;
    SELECT college_id INTO exam_college    FROM exams    WHERE exam_id = NEW.exam_id;

    IF student_college IS NULL THEN
        RAISE EXCEPTION 'answers.student_id % does not exist in students', NEW.student_id;
    END IF;
    IF exam_college IS NULL THEN
        RAISE EXCEPTION 'answers.exam_id % does not exist in exams', NEW.exam_id;
    END IF;
    IF student_college <> exam_college THEN
        RAISE EXCEPTION
            'student % belongs to college %, but exam % belongs to college % — cannot create an answer across colleges',
            NEW.student_id, student_college, NEW.exam_id, exam_college;
    END IF;

    NEW.college_id := student_college; -- always derived, never trusted from input
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_answers_derive_college ON answers;
CREATE TRIGGER trg_answers_derive_college
    BEFORE INSERT OR UPDATE OF student_id, exam_id, college_id ON answers
    FOR EACH ROW
    EXECUTE FUNCTION fn_derive_and_check_answer_college();

-- ============================================================================
-- 5. answer_blocks / evaluation_results — add college_id, backfilled from
-- the parent answer, then always DERIVED via trigger (never trusted from
-- app input). No reviewer involved, so no extra check beyond derivation.
-- ============================================================================
ALTER TABLE answer_blocks
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);
UPDATE answer_blocks ab SET college_id = a.college_id
    FROM answers a WHERE a.answer_id = ab.answer_id AND ab.college_id IS NULL;
ALTER TABLE answer_blocks ALTER COLUMN college_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_answer_blocks_college_id ON answer_blocks (college_id);

ALTER TABLE evaluation_results
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);
UPDATE evaluation_results er SET college_id = a.college_id
    FROM answers a WHERE a.answer_id = er.answer_id AND er.college_id IS NULL;
ALTER TABLE evaluation_results ALTER COLUMN college_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evaluation_results_college_id ON evaluation_results (college_id);

CREATE OR REPLACE FUNCTION fn_derive_college_from_answer()
RETURNS TRIGGER AS $$
DECLARE
    parent_college UUID;
BEGIN
    SELECT college_id INTO parent_college FROM answers WHERE answer_id = NEW.answer_id;
    IF parent_college IS NULL THEN
        RAISE EXCEPTION '% .answer_id % does not exist in answers', TG_TABLE_NAME, NEW.answer_id;
    END IF;
    NEW.college_id := parent_college;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_answer_blocks_derive_college ON answer_blocks;
CREATE TRIGGER trg_answer_blocks_derive_college
    BEFORE INSERT OR UPDATE OF answer_id, college_id ON answer_blocks
    FOR EACH ROW
    EXECUTE FUNCTION fn_derive_college_from_answer();

DROP TRIGGER IF EXISTS trg_evaluation_results_derive_college ON evaluation_results;
CREATE TRIGGER trg_evaluation_results_derive_college
    BEFORE INSERT OR UPDATE OF answer_id, college_id ON evaluation_results
    FOR EACH ROW
    EXECUTE FUNCTION fn_derive_college_from_answer();

-- ============================================================================
-- 6. answer_reviews — college_id derived from the parent answer, PLUS a
-- check that the assigned reviewer is either a global reviewer (NULL
-- college_id) or belongs to the SAME college as the answer. Stops a
-- College A teacher from reviewing College B's student answers.
-- ============================================================================
ALTER TABLE answer_reviews
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);
UPDATE answer_reviews ar SET college_id = a.college_id
    FROM answers a WHERE a.answer_id = ar.answer_id AND ar.college_id IS NULL;
ALTER TABLE answer_reviews ALTER COLUMN college_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_answer_reviews_college_id ON answer_reviews (college_id);

CREATE OR REPLACE FUNCTION fn_derive_and_check_answer_review_college()
RETURNS TRIGGER AS $$
DECLARE
    parent_college    UUID;
    reviewer_college  UUID;
BEGIN
    SELECT college_id INTO parent_college FROM answers WHERE answer_id = NEW.answer_id;
    IF parent_college IS NULL THEN
        RAISE EXCEPTION 'answer_reviews.answer_id % does not exist in answers', NEW.answer_id;
    END IF;

    SELECT college_id INTO reviewer_college FROM reviewers WHERE reviewer_id = NEW.reviewer_id;
    IF reviewer_college IS NOT NULL AND reviewer_college <> parent_college THEN
        RAISE EXCEPTION
            'reviewer % belongs to college %, but answer % belongs to college % — cross-college review is not allowed',
            NEW.reviewer_id, reviewer_college, NEW.answer_id, parent_college;
    END IF;

    NEW.college_id := parent_college;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_answer_reviews_derive_and_check_college ON answer_reviews;
CREATE TRIGGER trg_answer_reviews_derive_and_check_college
    BEFORE INSERT OR UPDATE OF answer_id, reviewer_id, college_id ON answer_reviews
    FOR EACH ROW
    EXECUTE FUNCTION fn_derive_and_check_answer_review_college();

-- ============================================================================
-- 7. answer_status_history — same pattern as answer_reviews, but
-- changed_by is nullable (system-driven transitions have no reviewer), so
-- the reviewer-college check only applies when changed_by is present.
-- ============================================================================
ALTER TABLE answer_status_history
    ADD COLUMN IF NOT EXISTS college_id UUID REFERENCES colleges(college_id);
UPDATE answer_status_history ash SET college_id = a.college_id
    FROM answers a WHERE a.answer_id = ash.answer_id AND ash.college_id IS NULL;
ALTER TABLE answer_status_history ALTER COLUMN college_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_answer_status_history_college_id ON answer_status_history (college_id);

CREATE OR REPLACE FUNCTION fn_derive_and_check_status_history_college()
RETURNS TRIGGER AS $$
DECLARE
    parent_college    UUID;
    reviewer_college  UUID;
BEGIN
    SELECT college_id INTO parent_college FROM answers WHERE answer_id = NEW.answer_id;
    IF parent_college IS NULL THEN
        RAISE EXCEPTION 'answer_status_history.answer_id % does not exist in answers', NEW.answer_id;
    END IF;

    IF NEW.changed_by IS NOT NULL THEN
        SELECT college_id INTO reviewer_college FROM reviewers WHERE reviewer_id = NEW.changed_by;
        IF reviewer_college IS NOT NULL AND reviewer_college <> parent_college THEN
            RAISE EXCEPTION
                'reviewer % belongs to college %, but answer % belongs to college % — cannot change status across colleges',
                NEW.changed_by, reviewer_college, NEW.answer_id, parent_college;
        END IF;
    END IF;

    NEW.college_id := parent_college;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_answer_status_history_derive_and_check_college ON answer_status_history;
CREATE TRIGGER trg_answer_status_history_derive_and_check_college
    BEFORE INSERT OR UPDATE OF answer_id, changed_by, college_id ON answer_status_history
    FOR EACH ROW
    EXECUTE FUNCTION fn_derive_and_check_status_history_college();

-- ============================================================================
-- 8. Row-Level Security — defense-in-depth tenant isolation on every
-- answer-schema table. Relies on the app setting a per-request/transaction
-- session variable, e.g.:
--
--     SET LOCAL app.current_college_id = '<uuid>';
--
-- at the start of each transaction, after authenticating which college the
-- request belongs to. If the variable is unset, current_setting(...,true)
-- returns NULL, and the tenant policy matches nothing — fails closed.
--
-- A second, permissive policy allows a platform-admin session (setting
-- app.is_platform_admin = 'true') to see across all colleges for
-- cross-tenant support/reporting.
--
-- IMPORTANT: RLS only applies to roles that are NOT the table owner and do
-- NOT have BYPASSRLS, unless FORCE ROW LEVEL SECURITY is set. If your app
-- connects as the same role that owns these tables (common in simple
-- setups), FORCE is required for RLS to have any effect — applied below.
-- ============================================================================
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['students', 'exams', 'answers', 'answer_blocks',
                              'evaluation_results', 'answer_reviews', 'answer_status_history']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);

        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
        EXECUTE format($f$
            CREATE POLICY tenant_isolation ON %I
                FOR ALL
                USING (college_id = current_setting('app.current_college_id', true)::uuid)
                WITH CHECK (college_id = current_setting('app.current_college_id', true)::uuid)
        $f$, t);

        EXECUTE format('DROP POLICY IF EXISTS platform_admin_bypass ON %I', t);
        EXECUTE format($f$
            CREATE POLICY platform_admin_bypass ON %I
                FOR ALL
                USING (current_setting('app.is_platform_admin', true) = 'true')
                WITH CHECK (current_setting('app.is_platform_admin', true) = 'true')
        $f$, t);
    END LOOP;
END $$;

-- ============================================================================
-- No changes to Question-schema tables (paragraphs, sentences, topics,
-- topic_links, questions, keywords, question_keywords, content_assets,
-- question_asset_links, reference_answer_variants, question_reviews,
-- question_status_history). They remain shared/global across all colleges,
-- with no college_id and no RLS, per requirement.
-- ============================================================================

COMMIT;
