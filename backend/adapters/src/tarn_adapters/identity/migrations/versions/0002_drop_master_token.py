"""Drop the master recovery token (O9: its purpose is undefined; removed until the recovery
design is decided). Its CHECK with recovery_version goes with the column.

Revision ID: i0002
Revises: i0001
Create Date: 2026-10-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "i0002"
down_revision: str | None = "i0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE identities DROP COLUMN encrypted_master_recovery_token")


def downgrade() -> None:
    # Restored empty; the old CHECK is not restored (no row could satisfy it again).
    op.execute("ALTER TABLE identities ADD COLUMN encrypted_master_recovery_token text")
