"""P32: the SME's suggested replacement on a delegation request (D218).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-09

``delegation_requests.suggested_to`` (nullable): the SME the requester suggests, a user of the
same college (composite foreign key) and never the requester. The table's row-level security,
policy and grants already cover the new column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE delegation_requests ADD COLUMN suggested_to uuid")
    op.execute(
        "ALTER TABLE delegation_requests ADD CONSTRAINT delegation_requests_suggested_fk "
        "FOREIGN KEY (college_id, suggested_to) REFERENCES users (college_id, id)"
    )
    op.execute(
        "ALTER TABLE delegation_requests ADD CONSTRAINT delegation_requests_suggested_other "
        "CHECK (suggested_to IS NULL OR suggested_to <> requested_by)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE delegation_requests DROP COLUMN suggested_to")
