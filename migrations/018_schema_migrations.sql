-- =============================================================================
-- Migration 018 — schema_migrations tracking table
-- =============================================================================
--
-- Every migration before this one was applied by hand and tracked only by
-- convention (§8's psql loop, run in numeric order) — "is this DB current?"
-- was answered by reading files and guessing (CLAUDE_CONTEXT.md §10). This
-- adds ONE table recording which migration files have actually run against
-- THIS database, and nothing else: no model-diffing, no up/down pairs, no
-- ownership of the schema. scripts/migrate.py is the runner; it reads this
-- table plus the files on disk and answers --status, or applies --up.
--
-- WHY NOT A MIGRATION FRAMEWORK (alembic, etc.)
--
-- These migrations are hand-written SQL with triggers, RLS policies and
-- partial unique indexes (see migrations/README.md throughout) — exactly the
-- shapes a model-diffing tool infers badly and then "fixes" by dropping and
-- recreating. scripts/migrate.py owns only sequencing and bookkeeping; the
-- SQL stays hand-written, one file per change, same as every migration
-- before this one.
--
-- COLUMNS
--
-- version   TEXT PRIMARY KEY. The migration's zero-padded numeric filename
--           prefix ("001", "016", "018") — not an integer, so a future
--           non-numeric identifier is not a type change, and it sorts and
--           compares as text exactly the way `ls migrations/` already does.
-- applied_at defaults to now() — when THIS database ran the file, not when
--           the file was authored.
-- checksum  sha256 of the file's bytes at the moment it was applied.
--           scripts/migrate.py refuses to apply anything further (and
--           refuses to touch this row) if a file's current checksum no
--           longer matches what's recorded here — a changed already-applied
--           file is corruption or rewritten history, and silently "fixing"
--           the row by re-hashing would erase the only evidence that
--           happened.
--
-- Idempotent (IF NOT EXISTS) and wrapped in BEGIN/COMMIT, per §5 rule 5.
--
-- Running this file is a formality, not the only way this table comes to
-- exist: scripts/migrate.py creates it (identically, IF NOT EXISTS) before
-- it does anything else, because the tool has to be able to answer --status
-- — including "is 018 itself applied?" — on a database that has never run
-- this file. Applying 018 through the normal --up path simply records that
-- fact in the ledger the tool was already maintaining.
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT NOT NULL
);

COMMENT ON TABLE schema_migrations IS
    'Tracks which migrations/NNN_*.sql files have been applied to this database. '
    'Written by scripts/migrate.py, not by application code.';

COMMIT;
