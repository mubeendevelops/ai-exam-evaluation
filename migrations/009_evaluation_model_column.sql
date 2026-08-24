-- =============================================================================
-- Migration 009 — Add evaluator_model to evaluation_results
-- =============================================================================
--
-- Why: evaluator_type only distinguishes 'ai' from 'sme'. Task 3 introduces
-- two distinct AI scoring methods (sentence-transformer embeddings and LLM
-- prompt-based), both stored as evaluator_type='ai'. evaluator_model records
-- which model/method produced the score (e.g. 'all-MiniLM-L6-v2' or
-- 'qwen/qwen3.6-27b'), enabling downstream comparison and auditability.
--
-- Run AFTER: 001 through 008.
-- Idempotent: ADD COLUMN IF NOT EXISTS.
-- =============================================================================

BEGIN;

ALTER TABLE evaluation_results
    ADD COLUMN IF NOT EXISTS evaluator_model TEXT;

COMMENT ON COLUMN evaluation_results.evaluator_model IS
    'Model/method identifier that produced this score. '
    'NULL for legacy rows or SME manual scores. '
    'Examples: ''all-MiniLM-L6-v2'', ''qwen/qwen3.6-27b''.';

COMMIT;
