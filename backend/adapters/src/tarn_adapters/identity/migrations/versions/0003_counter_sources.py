"""Lockout counters per client address for the shared admin login (P24, D161, D179).

Revision ID: i0003
Revises: i0002
Create Date: 2026-10-27

``login_counters.source``: "" is the account-wide counter (SMEs, the Governor, and every row
before this revision); the admin's failures are counted per ``addr:<sha256 prefix>`` of the
client address, so one device guessing wrong does not lock the other admin staff out. The key
becomes ``(user_id, source)``. Nothing is deleted by the app role: clearing sets the rows back
to zero.

Identity records carry no role, so the teacher → SME rename (0013) changes nothing here.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "i0003"
down_revision: str | None = "i0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in (
        "ALTER TABLE login_counters ADD COLUMN source text NOT NULL DEFAULT '' "
        "CHECK (source = '' OR source ~ '^addr:[0-9a-f]{32}$')",
        "ALTER TABLE login_counters DROP CONSTRAINT login_counters_pkey",
        "ALTER TABLE login_counters ADD PRIMARY KEY (user_id, source)",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "ALTER TABLE login_counters NO FORCE ROW LEVEL SECURITY",
        "DELETE FROM login_counters WHERE source <> ''",
        "ALTER TABLE login_counters FORCE ROW LEVEL SECURITY",
        "ALTER TABLE login_counters DROP CONSTRAINT login_counters_pkey",
        "ALTER TABLE login_counters ADD PRIMARY KEY (user_id)",
        "ALTER TABLE login_counters DROP COLUMN source",
    ):
        op.execute(statement)
