-- ============================================================================
-- Migration: 002_question_schema.sql
-- Scope:     Question Knowledge Repository / Generation side of the AI Exam
--            Evaluation Platform (question-schema-design.md /
--            PROJECT_CONTEXT.md §4)
--
-- PREREQUISITE: 001_answer_schema.sql must already be applied. This script
-- extends the placeholder `questions` and `reference_answer_variants`
-- tables it created, and reuses its `reviewers` table as-is.
--
-- This script is idempotent: CREATE TABLE IF NOT EXISTS, guarded CREATE
-- TYPE, ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS. Re-running it
-- is safe.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- Enums (guarded CREATE TYPE — Postgres has no CREATE TYPE IF NOT EXISTS).
-- `question_status` already exists from 001_answer_schema.sql (placeholder
-- `questions` table) and is reused as-is — not redefined here.
-- ----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE paragraph_status AS ENUM ('active', 'superseded');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE question_source_type AS ENUM ('sentence', 'paragraph', 'diagram', 'table', 'formula', 'manual');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE question_style AS ENUM ('long', 'short', 'one_word', 'mcq');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE topic_entity_type AS ENUM ('paragraph', 'question');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE content_asset_type AS ENUM ('diagram', 'table', 'formula');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE asset_link_role AS ENUM ('question_source', 'answer_component');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE reference_variant_type AS ENUM ('short', 'long');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE question_review_action AS ENUM ('confirmed', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================================
-- 1. paragraphs
-- ============================================================================
CREATE TABLE IF NOT EXISTS paragraphs (
    paragraph_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content          TEXT NOT NULL,
    source_document  TEXT NOT NULL,
    version          INT NOT NULL DEFAULT 1,
    status           paragraph_status NOT NULL DEFAULT 'active',
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 2. sentences
-- ============================================================================
CREATE TABLE IF NOT EXISTS sentences (
    sentence_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    paragraph_id         UUID NOT NULL REFERENCES paragraphs(paragraph_id),
    content              TEXT NOT NULL,
    sequence_order       INT NOT NULL,
    is_question_worthy   BOOLEAN
);

-- ============================================================================
-- 3. topics
-- ============================================================================
CREATE TABLE IF NOT EXISTS topics (
    topic_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    parent_topic_id  UUID REFERENCES topics(topic_id)
);

-- ============================================================================
-- 4. topic_links
-- Generalized tagging table (paragraph_id / question_id are NOT separate
-- tables here on purpose — see PROJECT_CONTEXT.md §8). `entity_id` is
-- polymorphic (points into paragraphs or questions depending on
-- entity_type) and therefore cannot carry a real FK constraint.
-- ============================================================================
CREATE TABLE IF NOT EXISTS topic_links (
    link_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type  topic_entity_type NOT NULL,
    entity_id    UUID NOT NULL,
    topic_id     UUID NOT NULL REFERENCES topics(topic_id)
);

-- ============================================================================
-- 5. keywords
-- ============================================================================
CREATE TABLE IF NOT EXISTS keywords (
    keyword_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    term        TEXT NOT NULL UNIQUE
);

-- ============================================================================
-- 6. content_assets
-- Generalized diagram/table/formula store (one table, not three — see
-- PROJECT_CONTEXT.md §8).
-- ============================================================================
CREATE TABLE IF NOT EXISTS content_assets (
    asset_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_type       content_asset_type NOT NULL,
    blob_url         TEXT,
    structured_data  JSON,
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 7. questions — EXTENDING THE PLACEHOLDER FROM 001_answer_schema.sql
--
-- The placeholder already has `question_id` (PK) and `status`
-- (question_status enum), which `answers.question_id` and both integrity
-- triggers from 001 depend on. Rather than DROP + CREATE (which would
-- either cascade-drop those dependent FKs/triggers or fail outright without
-- CASCADE), this ALTERs the existing table in place. The table identity,
-- PK type, and `status` column are untouched, so `answers.question_id` and
-- the `trg_answers_question_must_be_live` trigger keep working unchanged.
-- ============================================================================
ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS source_type question_source_type,
    ADD COLUMN IF NOT EXISTS source_id UUID,
    ADD COLUMN IF NOT EXISTS style question_style,
    ADD COLUMN IF NOT EXISTS marks_max REAL,
    ADD COLUMN IF NOT EXISTS supersedes_question_id UUID REFERENCES questions(question_id),
    ADD COLUMN IF NOT EXISTS question_group_id UUID,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

-- Enforce NOT NULL on the columns the doc marks as required (source_id and
-- supersedes_question_id stay nullable, exactly as specified). Guarded:
-- only applied if the table currently has no rows, since ALTER COLUMN ...
-- SET NOT NULL fails on pre-existing NULLs. If placeholder rows already
-- exist in your environment, backfill first, then run the ALTERs in the
-- ELSE branch manually.
DO $$
DECLARE
    row_count INT;
BEGIN
    SELECT COUNT(*) INTO row_count FROM questions;
    IF row_count = 0 THEN
        ALTER TABLE questions ALTER COLUMN source_type SET NOT NULL;
        ALTER TABLE questions ALTER COLUMN style SET NOT NULL;
        ALTER TABLE questions ALTER COLUMN marks_max SET NOT NULL;
        ALTER TABLE questions ALTER COLUMN question_group_id SET NOT NULL;
        ALTER TABLE questions ALTER COLUMN created_at SET NOT NULL;
    ELSE
        RAISE NOTICE
            'questions has % existing row(s); skipped NOT NULL enforcement on source_type/style/marks_max/question_group_id/created_at. Backfill those columns, then run: ALTER TABLE questions ALTER COLUMN <col> SET NOT NULL;',
            row_count;
    END IF;
END $$;

-- ============================================================================
-- 8. question_keywords
-- Join table. The doc lists question_id/keyword_id/weight without stating a
-- PK explicitly — assumed composite PK (question_id, keyword_id), i.e. one
-- weight per keyword per question. Flagged as an assumption in README.
-- ============================================================================
CREATE TABLE IF NOT EXISTS question_keywords (
    question_id  UUID NOT NULL REFERENCES questions(question_id),
    keyword_id   UUID NOT NULL REFERENCES keywords(keyword_id),
    weight       REAL NOT NULL,
    PRIMARY KEY (question_id, keyword_id)
);

-- ============================================================================
-- 9. reference_answer_variants — EXTENDING THE PLACEHOLDER FROM
-- 001_answer_schema.sql
--
-- Placeholder already has `variant_id` (PK) and `question_id` (FK ->
-- questions), which `evaluation_results.reference_answer_variant_id` and
-- `trg_evaluation_variant_matches_answer_question` depend on. Extended in
-- place for the same reason as `questions` above.
-- ============================================================================
ALTER TABLE reference_answer_variants
    ADD COLUMN IF NOT EXISTS variant_type reference_variant_type,
    ADD COLUMN IF NOT EXISTS content TEXT,
    ADD COLUMN IF NOT EXISTS is_current BOOLEAN DEFAULT true,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

DO $$
DECLARE
    row_count INT;
BEGIN
    SELECT COUNT(*) INTO row_count FROM reference_answer_variants;
    IF row_count = 0 THEN
        ALTER TABLE reference_answer_variants ALTER COLUMN variant_type SET NOT NULL;
        ALTER TABLE reference_answer_variants ALTER COLUMN content SET NOT NULL;
        ALTER TABLE reference_answer_variants ALTER COLUMN is_current SET NOT NULL;
        ALTER TABLE reference_answer_variants ALTER COLUMN created_at SET NOT NULL;
    ELSE
        RAISE NOTICE
            'reference_answer_variants has % existing row(s); skipped NOT NULL enforcement on variant_type/content/is_current/created_at. Backfill those columns, then run the corresponding ALTER TABLE ... SET NOT NULL statements manually.',
            row_count;
    END IF;
END $$;

-- ============================================================================
-- 10. question_asset_links
-- Generalized join replacing what would otherwise be six separate tables
-- (diagram/table/formula x source-role/answer-role) — see
-- PROJECT_CONTEXT.md §8. question_id populated only when role =
-- question_source; reference_answer_variant_id populated only when role =
-- answer_component — both nullable exactly as specified, not
-- CHECK-enforced (same minimal-constraint pattern used for
-- answer_reviews.final_marks in 001_answer_schema.sql).
-- ============================================================================
CREATE TABLE IF NOT EXISTS question_asset_links (
    link_id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id                      UUID NOT NULL REFERENCES content_assets(asset_id),
    role                          asset_link_role NOT NULL,
    question_id                   UUID REFERENCES questions(question_id),
    reference_answer_variant_id   UUID REFERENCES reference_answer_variants(variant_id)
);

-- ============================================================================
-- 11. question_reviews
-- Mirrors answer_reviews from the Answer schema. Reuses the existing
-- `reviewers` table from 001_answer_schema.sql — not recreated here.
-- ============================================================================
CREATE TABLE IF NOT EXISTS question_reviews (
    review_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id   UUID NOT NULL REFERENCES questions(question_id),
    reviewer_id   UUID NOT NULL REFERENCES reviewers(reviewer_id),
    action        question_review_action NOT NULL,
    comment       TEXT,
    reviewed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 12. question_status_history
-- Mirrors answer_status_history. changed_by nullable — null when
-- system-driven, same pattern as answer_status_history.changed_by.
-- ============================================================================
CREATE TABLE IF NOT EXISTS question_status_history (
    history_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id  UUID NOT NULL REFERENCES questions(question_id),
    old_status   TEXT NOT NULL,
    new_status   TEXT NOT NULL,
    changed_by   UUID REFERENCES reviewers(reviewer_id),
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- Indexes — every FK column, plus question_group_id, status (paragraphs and
-- questions), and is_current (reference_answer_variants) as requested.
-- Note: idx_reference_answer_variants_question_id already exists from
-- 001_answer_schema.sql — not recreated here.
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_paragraphs_status                   ON paragraphs (status);

CREATE INDEX IF NOT EXISTS idx_sentences_paragraph_id               ON sentences (paragraph_id);

CREATE INDEX IF NOT EXISTS idx_topics_parent_topic_id               ON topics (parent_topic_id);

CREATE INDEX IF NOT EXISTS idx_topic_links_topic_id                 ON topic_links (topic_id);

CREATE INDEX IF NOT EXISTS idx_questions_status                     ON questions (status);
CREATE INDEX IF NOT EXISTS idx_questions_question_group_id          ON questions (question_group_id);
CREATE INDEX IF NOT EXISTS idx_questions_supersedes_question_id     ON questions (supersedes_question_id);

CREATE INDEX IF NOT EXISTS idx_question_keywords_question_id        ON question_keywords (question_id);
CREATE INDEX IF NOT EXISTS idx_question_keywords_keyword_id         ON question_keywords (keyword_id);

CREATE INDEX IF NOT EXISTS idx_reference_answer_variants_is_current ON reference_answer_variants (is_current);

CREATE INDEX IF NOT EXISTS idx_question_asset_links_asset_id        ON question_asset_links (asset_id);
CREATE INDEX IF NOT EXISTS idx_question_asset_links_question_id     ON question_asset_links (question_id);
CREATE INDEX IF NOT EXISTS idx_question_asset_links_variant_id      ON question_asset_links (reference_answer_variant_id);

CREATE INDEX IF NOT EXISTS idx_question_reviews_question_id         ON question_reviews (question_id);
CREATE INDEX IF NOT EXISTS idx_question_reviews_reviewer_id         ON question_reviews (reviewer_id);

CREATE INDEX IF NOT EXISTS idx_question_status_history_question_id  ON question_status_history (question_id);
CREATE INDEX IF NOT EXISTS idx_question_status_history_changed_by   ON question_status_history (changed_by);

-- ============================================================================
-- No changes to the two integrity triggers from 001_answer_schema.sql
-- (trg_answers_question_must_be_live, trg_evaluation_variant_matches_answer_question).
-- Both keep working unmodified: `questions` and `reference_answer_variants`
-- were extended in place, never dropped, so their PK columns and the FKs
-- from `answers` / `evaluation_results` were never invalidated.
-- ============================================================================

COMMIT;