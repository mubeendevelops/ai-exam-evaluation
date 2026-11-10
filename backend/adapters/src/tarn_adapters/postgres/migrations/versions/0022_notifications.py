"""P37: SME notifications (D227, D241, D242).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-10

``notifications``: one in-app notice for one user about one unit. College data: composite
foreign keys to the user and the unit, college RLS forced. ``kind`` from a fixed list;
``payload`` a small JSON object of codes, dates and ids (the one text is the admin's return
note, checked by the core). The sweep's notices (``due_soon``, ``overdue``) are unique per
(unit, kind, day), so a second sweep on the same day inserts nothing. The app role may SELECT
and INSERT, and UPDATE ``read_at`` only (column grant); no DELETE.

The downgrade drops the table (notices are derived from units and events; development only).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"
KINDS = (
    "assigned",
    "transferred_away",
    "returned",
    "request_done",
    "request_declined",
    "due_soon",
    "overdue",
)


def upgrade() -> None:
    kinds = ", ".join(f"'{k}'" for k in KINDS)
    for statement in (
        f"""
        CREATE TABLE notifications (
            college_id uuid NOT NULL REFERENCES colleges (id),
            id uuid NOT NULL,
            user_id uuid NOT NULL,
            kind text NOT NULL CHECK (kind IN ({kinds})),
            unit_id uuid NOT NULL,
            day date NOT NULL,
            payload jsonb NOT NULL DEFAULT '{{}}'::jsonb
                CHECK (jsonb_typeof(payload) = 'object' AND length(payload::text) <= 2000),
            created_at timestamptz NOT NULL,
            read_at timestamptz,
            PRIMARY KEY (college_id, id),
            UNIQUE (id),
            CHECK (read_at IS NULL OR read_at >= created_at),
            FOREIGN KEY (college_id, user_id) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, unit_id) REFERENCES units (college_id, id)
        )
        """,
        "CREATE UNIQUE INDEX notifications_due_once ON notifications "
        "(college_id, unit_id, kind, day) WHERE kind IN ('due_soon', 'overdue')",
        "CREATE INDEX notifications_inbox ON notifications "
        "(college_id, user_id, created_at DESC, id DESC)",
        "CREATE INDEX notifications_unread ON notifications (college_id, user_id) "
        "WHERE read_at IS NULL",
        "ALTER TABLE notifications ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE notifications FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY college_isolation ON notifications "
        f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        f"GRANT SELECT, INSERT ON notifications TO {APP_ROLE}",
        f"GRANT UPDATE (read_at) ON notifications TO {APP_ROLE}",
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE notifications")
