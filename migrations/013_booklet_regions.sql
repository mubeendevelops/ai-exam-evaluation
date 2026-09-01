-- =============================================================================
-- Migration 013 — Booklet region provenance on answer_blocks (Task 5: ingestion)
-- =============================================================================
--
-- Why: until now every answer_blocks row was created one at a time from a
-- single pre-cropped image (scripts/upload_diagram_scan.py, or the ad-hoc
-- INSERT behind run_table_eval_demo.sh). Booklet ingestion instead produces
-- MANY blocks per answer, each one a region cut out of a specific page of a
-- scanned PDF by a layout model. The table could not record where a block came
-- from — no page number, no bounding box — nor how confident the classifier
-- was that the region is text vs. table vs. diagram. Without that, a
-- mis-routed region is indistinguishable from a correct one after the fact,
-- and a human reviewer has no way to find the region on the original page.
--
-- Design decision — no new answer_block_type enum value. The obvious
-- alternative was an 'unknown' block_type for regions the classifier is unsure
-- about. Rejected: block_type is what plugins dispatch on
-- (core/plugins/registry.py::plugins_for), so an 'unknown' value would be a
-- type no plugin supports, silently dropping regions instead of surfacing
-- them. Uncertainty is a separate axis from kind, so it gets its own column —
-- needs_review — and the block keeps the classifier's best guess. This also
-- sidesteps ALTER TYPE ... ADD VALUE, which cannot have its new value used in
-- the same transaction that adds it, and so cannot be made cleanly idempotent
-- inside the BEGIN/COMMIT this file is wrapped in (PROJECT_CONTEXT.md rule 5).
--
-- Every column added here is nullable (or defaulted), so all six pre-existing
-- writers — upload_diagram_scan.py, seed_minimal.sql, the table demo insert,
-- and the three plugins — keep working with no change at all.
--
-- Run AFTER: 001 through 012.
-- Idempotent: ADD COLUMN IF NOT EXISTS, guarded ADD CONSTRAINT, CREATE INDEX
--             IF NOT EXISTS.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Region provenance columns
-- -----------------------------------------------------------------------------

ALTER TABLE answer_blocks
    ADD COLUMN IF NOT EXISTS page_number               INT,
    ADD COLUMN IF NOT EXISTS region_bbox               JSONB,
    ADD COLUMN IF NOT EXISTS page_image_url            TEXT,
    ADD COLUMN IF NOT EXISTS classification_label      TEXT,
    ADD COLUMN IF NOT EXISTS classification_confidence REAL,
    ADD COLUMN IF NOT EXISTS needs_review              BOOLEAN NOT NULL DEFAULT false;

-- Guarded: ADD CONSTRAINT has no IF NOT EXISTS in Postgres.
DO $$ BEGIN
    ALTER TABLE answer_blocks
        ADD CONSTRAINT answer_blocks_page_number_positive
        CHECK (page_number IS NULL OR page_number >= 1);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    ALTER TABLE answer_blocks
        ADD CONSTRAINT answer_blocks_classification_confidence_range
        CHECK (classification_confidence IS NULL
               OR (classification_confidence >= 0 AND classification_confidence <= 1));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- -----------------------------------------------------------------------------
-- 2. Indexes
-- -----------------------------------------------------------------------------

-- Page-ordered retrieval of one answer's blocks (the review UI's natural read).
CREATE INDEX IF NOT EXISTS idx_answer_blocks_page
    ON answer_blocks (answer_id, page_number);

-- Partial: the review queue only ever asks for the flagged minority.
CREATE INDEX IF NOT EXISTS idx_answer_blocks_needs_review
    ON answer_blocks (needs_review) WHERE needs_review;

-- -----------------------------------------------------------------------------
-- 3. Documentation
-- -----------------------------------------------------------------------------

COMMENT ON COLUMN answer_blocks.page_number IS
    '1-based page of the source booklet PDF this region was cut from. NULL for '
    'blocks attached individually from a single image (every row predating '
    'booklet ingestion). Written by core/booklet_persist.py::persist_regions.';
COMMENT ON COLUMN answer_blocks.region_bbox IS
    'Region rectangle as a JSON array [x, y, w, h], in pixel coordinates of the '
    'DESKEWED page image referenced by page_image_url — not of the original '
    'PDF page, which is why the deskewed page is stored rather than recomputed. '
    'Same [x, y, w, h] shape core/table_extractor.py already emits per cell. '
    'Produced by core/booklet_segmenter.py::classify_page.';
COMMENT ON COLUMN answer_blocks.page_image_url IS
    'Stable "bucket/key" ref to the full deskewed/denoised page image, so a '
    'reviewer can see a flagged region in its page context. blob_url remains '
    'the cropped region itself. NEVER a presigned URL (PROJECT_CONTEXT.md rule '
    '1) — generate those on demand via core/storage.py::presigned_get_url.';
COMMENT ON COLUMN answer_blocks.classification_label IS
    'Raw label emitted by the layout model before it was mapped onto '
    'block_type, e.g. ''paragraph_title'' -> ''text'', ''chart'' -> ''diagram''. '
    'Kept for provenance: block_type is lossy by design (20 layout labels '
    'collapse into 4 block types), and debugging a mis-route needs the label '
    'that was actually predicted. See core/booklet_segmenter.py::'
    'LAYOUT_TO_BLOCK_TYPE.';
COMMENT ON COLUMN answer_blocks.classification_confidence IS
    'Layout model''s confidence that this region is of classification_label, '
    'in [0, 1]. DISTINCT from confidence_score, which is the OCR confidence of '
    'the text read out of the block — a region can be confidently a table and '
    'still be read badly, and the two failures need different fixes. Regions '
    'from the heuristic fallback path carry a deliberately capped value.';
COMMENT ON COLUMN answer_blocks.needs_review IS
    'True when this region should not be trusted to route to a plugin '
    'unattended: classification confidence below threshold, a block_type no '
    'plugin supports (formula), or segmentation done by the heuristic fallback '
    'rather than the layout model. Uncertainty is tracked here rather than as '
    'an ''unknown'' block_type so the block still carries its best guess and '
    'stays dispatchable once a human confirms it.';

COMMIT;
