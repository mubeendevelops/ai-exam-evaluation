-- =============================================================================
-- Migration 020 — indexes for the content-upload picker (core/paragraphs.py)
-- =============================================================================
--
-- WHY
--
-- Before this pass, nothing in the codebase ever wrote a `paragraphs` row —
-- not core/, not api/, not a script, not even seed_minimal.sql (see that
-- file's own header, and tests/test_api/conftest.py::make_paragraph, which is
-- the only INSERT that existed). core/paragraphs.py is that missing write
-- path, plus the read path (list/search/filter) a "content picker" UI needs.
-- This migration adds the indexes that read path leans on; it changes no
-- column and no constraint.
--
-- WHAT IT DOES NOT DO, AND WHY
--
-- No `college_id` column and no RLS policy pair. Migration 003 §0 excludes
-- the entire question schema from tenancy — "shared question bank, no tenant
-- column, readable/usable by every college" — and `paragraphs`/`sentences`
-- are two of those twelve tables (003 line 341 names them explicitly). An
-- uploaded paragraph is visible to every college, on purpose, the same way a
-- question generated from it is once it exists. This migration keeps that
-- decision rather than quietly reopening it: tenanting uploaded content, if
-- ever wanted, is a deliberate future migration that also has to update
-- CLAUDE_CONTEXT.md §5/§6 and the tests that pin the bank as shared
-- (tests/test_api/test_questions.py::test_the_bank_is_shared_across_colleges
-- and its counterpart in test_content.py), not a column added here in
-- passing.
--
-- WHAT EACH INDEX IS FOR
--
--   idx_paragraphs_source_document — GET /api/v1/content/documents groups by
--     this column, and the picker's document filter (GET
--     /api/v1/content/paragraphs?source_document=...) equality-matches it.
--     `idx_paragraphs_status` already exists (002 line 263); this is the
--     column that index does not cover.
--   idx_paragraphs_uploaded_at — the picker's list ordering
--     (uploaded_at DESC, paragraph_id DESC — the same timestamp-plus-PK
--     tiebreak every other list query in this codebase uses, since bulk
--     paragraph creation from one upload can share a now() timestamp).
--     Composite and pre-ordered so LIMIT/OFFSET paging does not sort the
--     whole table on every page.
--   idx_topic_links_entity — backs the topic carry-forward this schema
--     already runs on every question, unindexed until now:
--     scripts/generate_questions.py's
--       INSERT INTO topic_links (...) SELECT ... FROM topic_links
--       WHERE entity_type = 'paragraph' AND entity_id = %s
--     seq-scans topic_links on every single generation call without this.
--
-- Run AFTER: 001 through 019 (needs `paragraphs` from 002 and `topic_links`
-- from 002).
-- Idempotent: CREATE INDEX IF NOT EXISTS only — no table, column, or
-- constraint changes, so there is nothing to guard with a DO block.
-- =============================================================================

BEGIN;

CREATE INDEX IF NOT EXISTS idx_paragraphs_source_document
    ON paragraphs (source_document);

CREATE INDEX IF NOT EXISTS idx_paragraphs_uploaded_at
    ON paragraphs (uploaded_at DESC, paragraph_id DESC);

CREATE INDEX IF NOT EXISTS idx_topic_links_entity
    ON topic_links (entity_type, entity_id);

COMMIT;
