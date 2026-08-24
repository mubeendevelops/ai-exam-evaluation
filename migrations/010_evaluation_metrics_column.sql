-- =============================================================================
-- Migration 010 — Add metrics to evaluation_results
-- =============================================================================
--
-- Why: To compare models (especially embeddings vs LLM) for latency, token
-- usage, and cost, we need to capture these performance metrics.
--
-- Run AFTER: 001 through 009.
-- Idempotent: ADD COLUMN IF NOT EXISTS.
-- =============================================================================

BEGIN;

ALTER TABLE evaluation_results
    ADD COLUMN IF NOT EXISTS metrics JSONB;

COMMENT ON COLUMN evaluation_results.metrics IS
    'Stores performance metrics for the evaluation. '
    'Example: {"latency_ms": 1500, "prompt_tokens": 300, "completion_tokens": 150}';

COMMIT;
