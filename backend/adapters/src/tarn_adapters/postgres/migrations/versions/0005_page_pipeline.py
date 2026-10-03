"""P9: upload and page cleaning: uploaded files and failures on booklets, page quality, and the
job queue.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-03

- ``booklets.sources`` (the uploaded files as stored, in upload order) and ``failure_reason``.
- ``pages``: the untouched ``original_key``, ``cleaned`` (the stage marker that makes page
  cleaning resumable), the cleaner's ``metrics``, the gate's ``retake_reasons`` and the teacher's
  ``use_anyway``. One row per page index of a booklet.
- ``jobs``: the PostgreSQL job queue (D61). College tables carry ``college_id`` and the usual
  policy: a college enqueues and sees only its own jobs. The worker serves *all* colleges, so a
  second policy lets a transaction that has set ``app.scheduler = 'on'`` read and update any job.
  Like ``app.college_id`` the setting is the application's to set (O7); a job row holds only
  ids, a kind and attempt bookkeeping, never booklet content. Jobs are never deleted by the
  application (they are the history of what ran).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"


def upgrade() -> None:
    for statement in (
        "ALTER TABLE booklets ADD COLUMN sources jsonb NOT NULL DEFAULT '[]'",
        "ALTER TABLE booklets ADD COLUMN failure_reason text",
        "ALTER TABLE booklets ADD CONSTRAINT booklet_failure_reason "
        "CHECK ((status = 'failed') = (failure_reason IS NOT NULL))",
        "CREATE INDEX ON booklets (college_id, uploaded_by, status)",
        "ALTER TABLE pages ADD COLUMN original_key text",
        "ALTER TABLE pages ADD COLUMN cleaned boolean NOT NULL DEFAULT true",
        "ALTER TABLE pages ADD COLUMN use_anyway boolean NOT NULL DEFAULT false",
        "ALTER TABLE pages ADD COLUMN metrics jsonb",
        "ALTER TABLE pages ADD COLUMN retake_reasons text[] NOT NULL DEFAULT '{}'",
        "ALTER TABLE pages ADD CONSTRAINT page_use_anyway_needs_flag "
        "CHECK (NOT use_anyway OR cardinality(retake_reasons) > 0)",
        "ALTER TABLE pages ADD CONSTRAINT page_index_once UNIQUE (booklet_id, page_index)",
        """
        CREATE FUNCTION tarn_scheduler_mode() RETURNS boolean
        LANGUAGE sql STABLE AS
        $$ SELECT coalesce(current_setting('app.scheduler', true), '') = 'on' $$
        """,
        """
        CREATE TABLE jobs (
            id uuid PRIMARY KEY,
            college_id uuid NOT NULL REFERENCES colleges (id),
            kind text NOT NULL CHECK (btrim(kind) <> ''),
            payload jsonb NOT NULL DEFAULT '{}',
            dedupe_key text,
            status text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            max_attempts integer NOT NULL CHECK (max_attempts >= 1),
            run_after timestamptz NOT NULL DEFAULT now(),
            locked_by text,
            locked_until timestamptz,
            started_at timestamptz,
            finished_at timestamptz,
            last_error text,
            created_at timestamptz NOT NULL DEFAULT now(),
            seq bigint GENERATED ALWAYS AS IDENTITY
        )
        """,
        # One live job per (college, key): enqueueing twice returns the first.
        "CREATE UNIQUE INDEX jobs_live_key ON jobs (college_id, dedupe_key) "
        "WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')",
        "CREATE INDEX ON jobs (status, run_after)",
        "CREATE INDEX ON jobs (college_id, started_at)",
        "ALTER TABLE jobs ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE jobs FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY college_isolation ON jobs "
        f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        "CREATE POLICY scheduler_read ON jobs FOR SELECT USING (tarn_scheduler_mode())",
        "CREATE POLICY scheduler_update ON jobs FOR UPDATE "
        "USING (tarn_scheduler_mode()) WITH CHECK (tarn_scheduler_mode())",
        f"GRANT SELECT, INSERT, UPDATE ON jobs TO {APP_ROLE}",
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP TABLE jobs",
        "DROP FUNCTION tarn_scheduler_mode()",
        "ALTER TABLE pages DROP CONSTRAINT page_index_once",
        "ALTER TABLE pages DROP CONSTRAINT page_use_anyway_needs_flag",
        "ALTER TABLE pages DROP COLUMN retake_reasons",
        "ALTER TABLE pages DROP COLUMN metrics",
        "ALTER TABLE pages DROP COLUMN use_anyway",
        "ALTER TABLE pages DROP COLUMN cleaned",
        "ALTER TABLE pages DROP COLUMN original_key",
        "ALTER TABLE booklets DROP CONSTRAINT booklet_failure_reason",
        "ALTER TABLE booklets DROP COLUMN failure_reason",
        "ALTER TABLE booklets DROP COLUMN sources",
    ):
        op.execute(statement)
