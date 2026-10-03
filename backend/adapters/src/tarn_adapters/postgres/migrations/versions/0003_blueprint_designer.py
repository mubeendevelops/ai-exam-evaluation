"""P6: blueprint course code and duration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

- ``exam_blueprints.course_code`` and ``duration_minutes`` (the exam header of the designer).
  The evaluation method of a section and unlinked question slots live in the ``sections`` jsonb
  (storage form), so they need no column.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE exam_blueprints ADD COLUMN course_code text NOT NULL DEFAULT '' "
        "CHECK (course_code = btrim(course_code) AND length(course_code) <= 200)"
    )
    op.execute(
        "ALTER TABLE exam_blueprints ADD COLUMN duration_minutes integer "
        "CHECK (duration_minutes IS NULL OR duration_minutes >= 1)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE exam_blueprints DROP COLUMN duration_minutes")
    op.execute("ALTER TABLE exam_blueprints DROP COLUMN course_code")
