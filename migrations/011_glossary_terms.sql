-- =============================================================================
-- Migration 011 — Glossary Terms (Task 4: diagram answer evaluation)
-- =============================================================================
--
-- Adds a canonical-vocabulary table used to normalize noisy OCR'd diagram
-- labels before matching them against a reference diagram's node labels
-- (see plan.md §4.2, §5 step 2).
--
-- Kept separate from `keywords` (002_question_schema.sql) on purpose:
-- keywords tag a QUESTION with relevance-weighted terms
-- (question_keywords.weight = how relevant this term is to that question).
-- A glossary term is a different concept entirely — "the correct name for
-- this concept" plus known synonyms/misreadings, with no per-question
-- weighting and no question_id at all. Forcing both into one table would
-- replicate, in the other direction, the wrong-generalization mistake this
-- project already corrected once (see PROJECT_CONTEXT.md §8's topic_links /
-- content_assets precedent — one table per genuinely-shared shape, not one
-- table for two different shapes that happen to both be about "terms").
--
-- Run AFTER: 001 through 010.
-- Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS glossary_terms (
    term_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_term   TEXT NOT NULL,
    aliases          TEXT[] NOT NULL DEFAULT '{}',
    topic_id         UUID REFERENCES topics(topic_id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_glossary_terms_canonical_term
    ON glossary_terms (canonical_term);

CREATE INDEX IF NOT EXISTS idx_glossary_terms_topic_id
    ON glossary_terms (topic_id);

COMMENT ON TABLE  glossary_terms IS
    'Canonical vocabulary for diagram-label matching (Task 4). Distinct from '
    'keywords (question-relevance tagging) — this is "the correct name for '
    'this concept" plus known aliases/misreadings, used to normalize noisy '
    'OCR output before comparing an extracted diagram against a reference.';
COMMENT ON COLUMN glossary_terms.aliases IS
    'Known synonyms/misreadings, e.g. {''Central Processing Unit'', ''processor''} '
    'for canonical_term = ''CPU''. Matched case-insensitively by '
    'core/diagram_evaluator.py::match_glossary.';
COMMENT ON COLUMN glossary_terms.topic_id IS
    'Optional scoping — NULL = applies globally to all diagrams, non-NULL = '
    'only loaded for diagrams under that topic. Nullable self-contained FK, '
    'no ON DELETE behavior specified since topic deletion isn''t implemented '
    'anywhere in this schema yet (see PROJECT_CONTEXT.md §7 topic hierarchy '
    'open item).';

COMMIT;
