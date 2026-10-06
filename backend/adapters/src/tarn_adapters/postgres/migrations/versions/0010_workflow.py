"""P15: the teacher's review.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06

- ``booklets.status`` and ``answers.status`` are checked against the state machines' states
  (``approved_amended`` is new); ``answers.rescore_pending`` marks an answer waiting for a new
  suggestion (it cannot be approved meanwhile).
- ``result_sheets.note``: what an amended version changed (questions and reasons).
- ``booklet_locks``: one row per open booklet (holder, expiry). Locks are taken and released,
  so the application role may update and delete them.
- ``amendments``: an approved answer reopened after booklet approval; insert, then closed once
  (approved or withdrawn). Deleted only with the booklet.
- ``region_edits``: a teacher's OCR corrections, text before and after. Insert-only: the
  history of a booklet's text, kept out of the audit log (student text, D112). Deleted only
  with the booklet.
All three are college data: row-level security on ``college_id`` like every college table.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"
_BOOKLET_STATES = (
    "'uploaded', 'processing', 'needs_retake', 'pages_ready', 'reading', 'text_ready', "
    "'segmented', 'failed', 'scored', 'in_review', 'approved', 'amendment_in_progress', "
    "'approved_amended'"
)
_ANSWER_STATES = "'suggested', 'skipped', 'approved'"

_TABLES = {
    "booklet_locks": """
        college_id uuid NOT NULL,
        booklet_id uuid PRIMARY KEY,
        holder uuid NOT NULL,
        acquired_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        CHECK (expires_at > acquired_at),
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, holder) REFERENCES users (college_id, id)
    """,
    "amendments": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        answer_id uuid NOT NULL,
        base_review_id uuid NOT NULL,
        opened_by uuid NOT NULL,
        opened_at timestamptz NOT NULL,
        reason text NOT NULL DEFAULT '' CHECK (length(reason) <= 500),
        closed_by uuid,
        closed_at timestamptz,
        outcome text CHECK (outcome IN ('approved', 'withdrawn')),
        sheet_version integer CHECK (sheet_version >= 1),
        seq bigint GENERATED ALWAYS AS IDENTITY,
        CHECK ((closed_by IS NULL) = (closed_at IS NULL)
               AND (closed_at IS NULL) = (outcome IS NULL)),
        CHECK (sheet_version IS NULL OR outcome = 'approved'),
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, answer_id) REFERENCES answers (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, opened_by) REFERENCES users (college_id, id),
        FOREIGN KEY (college_id, closed_by) REFERENCES users (college_id, id)
    """,
    # region_id has no foreign key: a re-read of the page replaces its regions, and the
    # history of corrections stays with the booklet.
    "region_edits": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        region_id uuid NOT NULL,
        actor_id uuid NOT NULL,
        at timestamptz NOT NULL,
        before_text text,
        after_text text,
        before_struck_out boolean NOT NULL,
        after_struck_out boolean NOT NULL,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, actor_id) REFERENCES users (college_id, id)
    """,
}
_GRANTS = {
    "booklet_locks": "SELECT, INSERT, UPDATE, DELETE",
    "amendments": "SELECT, INSERT, UPDATE, DELETE",
    "region_edits": "SELECT, INSERT, DELETE",
}


def upgrade() -> None:
    statements = [
        f"ALTER TABLE booklets ADD CONSTRAINT booklet_status CHECK (status IN ({_BOOKLET_STATES}))",
        f"ALTER TABLE answers ADD CONSTRAINT answer_status CHECK (status IN ({_ANSWER_STATES}))",
        "ALTER TABLE answers ADD COLUMN rescore_pending boolean NOT NULL DEFAULT false",
        "ALTER TABLE answers ADD CONSTRAINT approved_not_pending "
        "CHECK (NOT (rescore_pending AND status = 'approved'))",
        "ALTER TABLE result_sheets ADD COLUMN note text NOT NULL DEFAULT ''",
    ]
    for table, columns in _TABLES.items():
        statements += [
            f"CREATE TABLE {table} ({columns})",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY college_isolation ON {table} "
            f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
            f"GRANT {_GRANTS[table]} ON {table} TO {APP_ROLE}",
        ]
    statements += [
        "CREATE INDEX ON amendments (booklet_id)",
        "CREATE INDEX ON region_edits (booklet_id)",
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP TABLE region_edits",
        "DROP TABLE amendments",
        "DROP TABLE booklet_locks",
        "ALTER TABLE result_sheets DROP COLUMN note",
        "ALTER TABLE answers DROP CONSTRAINT approved_not_pending",
        "ALTER TABLE answers DROP COLUMN rescore_pending",
        "ALTER TABLE answers DROP CONSTRAINT answer_status",
        "ALTER TABLE booklets DROP CONSTRAINT booklet_status",
    ):
        op.execute(statement)
