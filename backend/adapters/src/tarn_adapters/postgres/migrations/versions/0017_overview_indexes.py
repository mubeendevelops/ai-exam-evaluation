"""P29: indexes behind the admin's dashboard and progress report (D167).

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-28

The figures are SQL aggregates over the college's audit events (the SME's last activity, the
recent-activity feed) and jobs (queue depth). ``audit_events`` was indexed only by
(college, booklet): these two let the aggregates read an index instead of the whole log.
No table, column, policy or grant changes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX audit_events_actor_at ON audit_events (college_id, actor_id, at)")
    op.execute("CREATE INDEX audit_events_action_at ON audit_events (college_id, action, at DESC)")
    op.execute("CREATE INDEX jobs_college_status ON jobs (college_id, status)")


def downgrade() -> None:
    op.execute("DROP INDEX jobs_college_status")
    op.execute("DROP INDEX audit_events_action_at")
    op.execute("DROP INDEX audit_events_actor_at")
