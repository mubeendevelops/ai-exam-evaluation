-- =============================================================================
-- Migration 014 — evaluation_jobs: a Postgres-backed async job queue
-- =============================================================================
--
-- Why this exists: booklet evaluation takes MINUTES (core/booklet_evaluator.py
-- runs OCR, layout detection and LLM calls per region), so the API cannot do
-- it inside a request. Something has to hold "this work was accepted, it is
-- not done yet, here is how to ask about it".
--
-- Why NOT Redis: the design doc names Redis for job queueing, but
-- CLAUDE_CONTEXT.md §2 and §10 both record that it is implemented NOWHERE —
-- no client, no connection helper, no code. Adding it here would mean a new
-- service to run, a second store to back up, and a second place tenant
-- isolation has to be re-implemented by hand, because Redis has no RLS. This
-- table gets tenant isolation from the same policy pair every other
-- answer-schema table already uses (section 4 below), the same backup as the
-- rest of the data, and transactional consistency with the rows a job reads
-- and writes — a job's state change and the evaluation_results row it
-- produces can commit together or not at all, which two separate stores
-- cannot offer at any price.
--
-- SELECT ... FOR UPDATE SKIP LOCKED (Postgres 9.5+) is what makes this a real
-- queue rather than a table people poll and race on: it hands each concurrent
-- worker a different row instead of blocking them all on the first one. The
-- claim query lives in core/jobs.py::claim_next_job.
--
-- SCALE HONESTY: this is the right tool at this project's actual scale (one
-- Postgres, a handful of workers, minute-long jobs — the queue depth will be
-- tens, not millions). It is NOT a general replacement for a broker: there is
-- no fan-out, no pub/sub, no delayed retry backoff, and long-held row locks
-- would matter if jobs were milliseconds instead of minutes. Revisit if any
-- of those stops being true; do not revisit merely because Redis is
-- conventional.
--
-- Run AFTER: 001 through 013.
-- Idempotent: CREATE TABLE IF NOT EXISTS, guarded CREATE TYPE, guarded ADD
--             CONSTRAINT, CREATE INDEX IF NOT EXISTS, DROP POLICY IF EXISTS
--             before CREATE POLICY.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Status enum
-- -----------------------------------------------------------------------------
-- An enum rather than a TEXT + CHECK, matching answer_status / exam_status /
-- question_status in 001. Guarded because Postgres has no
-- CREATE TYPE IF NOT EXISTS.
--
-- Four states, one direction:
--
--     queued ──claim──> running ──┬──> succeeded   (terminal)
--        ^                        └──> failed      (terminal)
--        └────────── retry ───────────────┘
--
-- 'failed' is deliberately NOT terminal-forever: a retry moves the row back to
-- 'queued' and increments attempts, which is why attempts lives on the row
-- rather than being inferred from history.
DO $$ BEGIN
    CREATE TYPE evaluation_job_status AS ENUM ('queued', 'running', 'succeeded', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -----------------------------------------------------------------------------
-- 2. Table
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evaluation_jobs (
    job_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK nullability + cardinality (PROJECT_CONTEXT.md rule 4):
    --   MANY evaluation_jobs -> ONE college. NOT NULL: a job with no tenant
    --   could not be isolated by RLS at all, and there is no such thing as a
    --   tenant-less evaluation — even a platform-admin job is run ON BEHALF OF
    --   exactly one college's data. ON DELETE RESTRICT (the schema-wide
    --   default posture): deleting a college with jobs on file should fail
    --   loudly rather than silently discard its audit trail.
    college_id  UUID NOT NULL REFERENCES colleges(college_id),

    -- Free TEXT, not an enum, on purpose. Job KINDS are expected to be added
    -- often (booklet evaluation today; pending-text batches, pending-diagram
    -- batches, table batches, re-scoring runs next), and every new one would
    -- otherwise need a migration and an ALTER TYPE ... ADD VALUE — which
    -- cannot have its new value used in the same transaction that adds it, so
    -- it cannot be made cleanly idempotent inside this file's BEGIN/COMMIT
    -- (the same argument 013 records for answer_block_type). Job STATUS is an
    -- enum because those four states are the state machine itself and adding
    -- one is a design change, not a routine addition.
    job_type    TEXT NOT NULL,

    status      evaluation_job_status NOT NULL DEFAULT 'queued',

    -- Everything the worker needs to do the job, and nothing it can look up.
    -- For job_type='booklet_evaluation' today: the stable "bucket/key" storage
    -- ref of the uploaded PDF, the original filename, size, content type and
    -- storage mode. NEVER a presigned URL (CLAUDE_CONTEXT.md §10) — those
    -- expire, and a queued job may not run for minutes.
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- The worker's output. NULL until the job reaches a terminal state.
    -- Deliberately NOT the score itself: scores belong in the append-only
    -- evaluation_results ledger. This holds the run report (counts, per-question
    -- summary, failures) — the thing that has no other home.
    result      JSONB,

    -- Why it failed, in a form a human can act on. NULL unless it did.
    error       TEXT,

    -- Claim count, incremented on each claim (not on each enqueue), so a job
    -- that keeps dying mid-run is distinguishable from one that was never
    -- picked up. A retry cap is enforced by the worker, not by a constraint,
    -- because the right cap is operational policy and will change.
    attempts    INT NOT NULL DEFAULT 0,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at  TIMESTAMPTZ,   -- NULL while queued; set on the claim that runs it
    finished_at TIMESTAMPTZ    -- NULL until terminal; set with succeeded/failed
);

-- -----------------------------------------------------------------------------
-- 3. State-machine invariants
-- -----------------------------------------------------------------------------
-- These are in the DB, not only in core/jobs.py, because a job row is written
-- by a worker process that can be killed at any instant. An invariant that
-- lives only in Python is an invariant that holds only when Python got to
-- finish.
-- Guarded: ADD CONSTRAINT has no IF NOT EXISTS in Postgres.

DO $$ BEGIN
    ALTER TABLE evaluation_jobs
        ADD CONSTRAINT evaluation_jobs_attempts_non_negative
        CHECK (attempts >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Terminal states have a finish time; non-terminal states do not. This is the
-- constraint that stops the "succeeded but finished_at IS NULL" row that makes
-- every duration metric silently wrong.
DO $$ BEGIN
    ALTER TABLE evaluation_jobs
        ADD CONSTRAINT evaluation_jobs_finished_at_matches_status
        CHECK ((status IN ('succeeded', 'failed')) = (finished_at IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Nothing finishes without having started.
DO $$ BEGIN
    ALTER TABLE evaluation_jobs
        ADD CONSTRAINT evaluation_jobs_finished_implies_started
        CHECK (finished_at IS NULL OR started_at IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- A failed job MUST say why. Same posture as §7C/§7D's "flagged, never
-- guessed": a failure with a NULL error is indistinguishable from a bug in the
-- error reporting, and this is the row an operator reads at 2am.
DO $$ BEGIN
    ALTER TABLE evaluation_jobs
        ADD CONSTRAINT evaluation_jobs_failed_requires_error
        CHECK (status <> 'failed' OR error IS NOT NULL);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- A queued job has not run yet, so it carries no result. (It MAY carry an
-- error and a non-zero attempts count — that is a requeued failure, and
-- keeping the last error visible while it waits is the point.)
DO $$ BEGIN
    ALTER TABLE evaluation_jobs
        ADD CONSTRAINT evaluation_jobs_queued_has_no_result
        CHECK (status <> 'queued' OR result IS NULL);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -----------------------------------------------------------------------------
-- 4. Row-Level Security — identical policy pair to migration 003's seven
--    answer-schema tables. evaluation_jobs joins that set: a job names a
--    college's uploaded booklet and holds its evaluation report, which is
--    exactly as tenant-private as the answers it scores.
--
--    FORCE is required because the app connects as the table owner in the
--    common setup (003 records the same reasoning).
--
--    NOTE — and this is the reason api/routers/jobs.py ALSO filters on
--    college_id explicitly in its WHERE clause: Postgres exempts SUPERUSER and
--    BYPASSRLS roles from row-level security entirely, and FORCE does not
--    change that. With PGUSER=postgres (the .env.example default) these
--    policies are inert. See api/README.md; the explicit predicate is what
--    actually isolates tenants until a non-superuser application role exists.
-- -----------------------------------------------------------------------------
ALTER TABLE evaluation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE evaluation_jobs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON evaluation_jobs;
CREATE POLICY tenant_isolation ON evaluation_jobs
    FOR ALL
    USING (college_id = current_setting('app.current_college_id', true)::uuid)
    WITH CHECK (college_id = current_setting('app.current_college_id', true)::uuid);

DROP POLICY IF EXISTS platform_admin_bypass ON evaluation_jobs;
CREATE POLICY platform_admin_bypass ON evaluation_jobs
    FOR ALL
    USING (current_setting('app.is_platform_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_platform_admin', true) = 'true');

-- -----------------------------------------------------------------------------
-- 5. Indexes
-- -----------------------------------------------------------------------------

-- THE claim index. The worker's query is
--   SELECT ... WHERE status = 'queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1
-- and it runs on every poll of every worker. Partial, because the queued rows
-- are the small minority of a table that otherwise only grows: succeeded jobs
-- accumulate forever and must not be paid for on every claim.
CREATE INDEX IF NOT EXISTS idx_evaluation_jobs_claim
    ON evaluation_jobs (created_at)
    WHERE status = 'queued';

-- Tenant job list, newest first — the "my uploads" screen.
CREATE INDEX IF NOT EXISTS idx_evaluation_jobs_college_created
    ON evaluation_jobs (college_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- 6. Documentation
-- -----------------------------------------------------------------------------
COMMENT ON TABLE evaluation_jobs IS
    'Postgres-backed async job queue. Claimed with SELECT ... FOR UPDATE SKIP '
    'LOCKED by scripts/run_job_worker.py via core/jobs.py. Chosen over Redis, '
    'which CLAUDE_CONTEXT.md §2/§10 record as named in the design doc but '
    'implemented nowhere — see this file''s header for the full argument.';
COMMENT ON COLUMN evaluation_jobs.college_id IS
    'Owning tenant. NOT NULL; many jobs to one college; ON DELETE RESTRICT. '
    'This is the column both RLS policies above and api/routers/jobs.py''s '
    'explicit WHERE predicate filter on.';
COMMENT ON COLUMN evaluation_jobs.job_type IS
    'What the worker should do, e.g. ''booklet_evaluation''. TEXT rather than '
    'an enum so a new job kind needs no migration — see section 2.';
COMMENT ON COLUMN evaluation_jobs.payload IS
    'Worker input. For booklet_evaluation: {blob_url, filename, content_type, '
    'size_bytes, storage_mode}. blob_url is a stable "bucket/key" ref, NEVER a '
    'presigned URL (CLAUDE_CONTEXT.md §10) — a queued job may not run for '
    'minutes and a signed URL would have expired.';
COMMENT ON COLUMN evaluation_jobs.result IS
    'Worker output: the run report (counts, per-question summary, failures). '
    'NOT the scores themselves — those are appended to evaluation_results, '
    'which is the ledger of record (PROJECT_CONTEXT.md rule 2).';
COMMENT ON COLUMN evaluation_jobs.attempts IS
    'Incremented on each CLAIM, not each enqueue, so a job that repeatedly '
    'dies mid-run is distinguishable from one never picked up. The retry cap '
    'is worker policy, not a DB constraint.';

COMMIT;
