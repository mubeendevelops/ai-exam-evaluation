"""P34: an index for the paper structure lock (D222).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-09

Saving a paper asks whether any booklet is pinned to one of its versions
(``BookletRepository.pins_blueprint``); ``booklets (college_id, blueprint_id)`` answers it
without a scan. No column, policy or grant changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX booklets_blueprint_idx ON booklets (college_id, blueprint_id)")


def downgrade() -> None:
    op.execute("DROP INDEX booklets_blueprint_idx")
