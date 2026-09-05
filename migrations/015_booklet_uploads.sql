-- =============================================================================
-- Migration 015 — booklet_uploads, and job progress reporting
-- =============================================================================
--
-- TWO CHANGES, both driven by the same thing: uploading a booklet and
-- evaluating it are now separate API calls.
--
-- ── 1. booklet_uploads ──────────────────────────────────────────────────────
--
-- Yesterday POST /api/v1/upload enqueued an evaluation_jobs row and returned
-- its id as `upload_id`, because one upload meant exactly one job.
-- api/schemas/upload.py's docstring recorded that this would stop being true
-- "the moment re-evaluating an existing upload is supported". That moment is
-- now: POST /api/v1/evaluate takes {upload_id, exam_id, student_id}, so a
-- single uploaded PDF can be evaluated more than once — different exam/student
-- bindings, a re-run after a reference answer is fixed, a re-run after
-- ingestion is corrected.
--
-- With the old shape that leaves an evaluation_jobs row that is not a job: it
-- would sit 'queued' forever with no worker that handles it, and
-- GET /api/v1/jobs/{id} would tell the client "waiting for a worker to pick
-- this up" about something no worker will ever pick up. An API that lies about
-- its own state is worse than one that needs an extra table.
--
-- So the upload gets its own row. ONE booklet_uploads row : MANY
-- evaluation_jobs rows.
--
-- No FK from evaluation_jobs to here, deliberately: migration 014 made
-- `payload` generic on purpose so a new job kind needs no migration, and a
-- typed upload_id column would apply to exactly one job_type. The cost is
-- real and stated rather than hidden — an upload_id inside a payload can
-- dangle if its upload is deleted. Mitigation is at the edge:
-- POST /api/v1/evaluate resolves the upload (tenant-scoped) BEFORE enqueueing,
-- so a job is never created against an upload that does not exist.
--
-- ── 2. evaluation_jobs.progress ─────────────────────────────────────────────
--
-- 014 gave a job `payload` (input) and `result` (output, terminal only). A
-- running job had nothing to say between the two, so GET /jobs/{id} could
-- only report the four statuses. Booklet evaluation takes MINUTES (§7D), and
-- "running" for four minutes with no further signal is indistinguishable from
-- "hung" — the single most common support question a queue like this
-- generates.
--
-- A separate column rather than mutating `payload`, because payload is the
-- job's INPUT and a worker overwriting its own input destroys the record of
-- what it was asked to do (and with it any chance of a faithful retry).
--
-- Run AFTER: 001 through 014.
-- Idempotent: CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, guarded
--             ADD CONSTRAINT, CREATE INDEX IF NOT EXISTS, DROP POLICY IF
--             EXISTS before CREATE POLICY.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. booklet_uploads
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS booklet_uploads (
    upload_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- FK nullability + cardinality (PROJECT_CONTEXT.md rule 4):
    --   MANY booklet_uploads -> ONE college. NOT NULL — a scanned answer
    --   booklet is a student's exam paper and is never tenant-less; a row with
    --   no college could not be isolated by RLS at all. ON DELETE RESTRICT
    --   (schema-wide default posture): deleting a college that still has
    --   uploaded booklets should fail loudly, not silently orphan the scans.
    college_id    UUID NOT NULL REFERENCES colleges(college_id),

    -- Stable "bucket/key" reference, NEVER a presigned URL (rule 1 / §10).
    -- The same convention as answers.source_scan_url,
    -- answer_blocks.blob_url and content_assets.blob_url — and the value that
    -- is later matched against answers.source_scan_url to find the regions a
    -- particular ingestion of this upload produced.
    blob_url      TEXT NOT NULL,

    filename      TEXT NOT NULL,          -- as the client sent it
    content_type  TEXT,                   -- client-declared; NOT trusted for validation
    size_bytes    BIGINT NOT NULL,        -- bytes actually received
    storage_mode  TEXT NOT NULL,          -- 'dummy' | 'minio' (which core/storage.py path)
    uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Guarded: ADD CONSTRAINT has no IF NOT EXISTS in Postgres.
DO $$ BEGIN
    ALTER TABLE booklet_uploads
        ADD CONSTRAINT booklet_uploads_size_non_negative CHECK (size_bytes >= 0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- TEXT + CHECK rather than an enum: core/storage.py's mode set is a property
-- of that module, not a domain concept, and 014's argument against an enum
-- for job_type applies unchanged (ALTER TYPE ... ADD VALUE cannot be used in
-- the transaction that adds it, so it cannot be made idempotent here).
DO $$ BEGIN
    ALTER TABLE booklet_uploads
        ADD CONSTRAINT booklet_uploads_storage_mode
        CHECK (storage_mode IN ('dummy', 'minio'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- RLS: identical policy pair to migrations 003 and 014. A scanned booklet is
-- as tenant-private as the answers cut out of it.
--
-- NOTE, unchanged from 014: Postgres exempts SUPERUSER and BYPASSRLS roles
-- from row-level security and FORCE does not change that, so with
-- PGUSER=postgres these policies are INERT and the explicit college_id
-- predicates in core/uploads.py are what actually isolate tenants. See
-- api/README.md.
ALTER TABLE booklet_uploads ENABLE ROW LEVEL SECURITY;
ALTER TABLE booklet_uploads FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation ON booklet_uploads;
CREATE POLICY tenant_isolation ON booklet_uploads
    FOR ALL
    USING (college_id = current_setting('app.current_college_id', true)::uuid)
    WITH CHECK (college_id = current_setting('app.current_college_id', true)::uuid);

DROP POLICY IF EXISTS platform_admin_bypass ON booklet_uploads;
CREATE POLICY platform_admin_bypass ON booklet_uploads
    FOR ALL
    USING (current_setting('app.is_platform_admin', true) = 'true')
    WITH CHECK (current_setting('app.is_platform_admin', true) = 'true');

-- The tenant's "my uploads" list, newest first.
CREATE INDEX IF NOT EXISTS idx_booklet_uploads_college_uploaded
    ON booklet_uploads (college_id, uploaded_at DESC);

-- blob_url is how an upload is matched to the answers.source_scan_url rows a
-- given ingestion produced, so it is looked up by value.
CREATE INDEX IF NOT EXISTS idx_booklet_uploads_blob_url
    ON booklet_uploads (blob_url);

COMMENT ON TABLE booklet_uploads IS
    'One uploaded answer-booklet PDF. ONE upload : MANY evaluation_jobs — the '
    'same scan can be evaluated more than once. Written by '
    'api/routers/upload.py via core/uploads.py; read by '
    'api/routers/evaluation.py to resolve POST /api/v1/evaluate''s upload_id.';
COMMENT ON COLUMN booklet_uploads.blob_url IS
    'Stable "bucket/key" storage ref, NEVER a presigned URL (rule 1 / §10). '
    'Matched against answers.source_scan_url to find the answer_blocks rows a '
    'particular ingestion of this booklet produced.';
COMMENT ON COLUMN booklet_uploads.content_type IS
    'Client-declared Content-Type, recorded for provenance only. Upload '
    'validation uses the %PDF- signature instead — filename and Content-Type '
    'are both client-controlled.';

-- -----------------------------------------------------------------------------
-- 2. evaluation_jobs.progress
-- -----------------------------------------------------------------------------
ALTER TABLE evaluation_jobs
    ADD COLUMN IF NOT EXISTS progress JSONB;

COMMENT ON COLUMN evaluation_jobs.progress IS
    'What a RUNNING job is currently doing: {stage, percent, message, counts}. '
    'Distinct from payload (the job''s immutable input — a worker must never '
    'overwrite the record of what it was asked to do) and from result (the '
    'final report, terminal states only). NULL until a worker first reports. '
    'percent is a STAGE MARKER, not a measured fraction: core/booklet_evaluator '
    'runs extraction and evaluation as bounded thread pools with no progress '
    'callback, so within-stage completion is genuinely unknown and is not '
    'invented — see api/schemas/jobs.py::JobProgress.';

COMMIT;
