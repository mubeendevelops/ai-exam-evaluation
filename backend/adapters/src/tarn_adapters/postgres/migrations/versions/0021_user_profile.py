"""P36: an SME's employee ID and department (D225).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-10

``users.employee_id`` (1-40 characters) and ``users.department`` (1-80), both optional and set
by the admin. Staff data: the identity database is untouched. The table's row-level security,
policy and grants already cover the new columns; the checks keep a blank or padded value out
(the core trims and turns blank into NULL).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN employee_id text, ADD COLUMN department text")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_employee_id_check "
        "CHECK (employee_id IS NULL OR "
        "(employee_id = btrim(employee_id) AND char_length(employee_id) BETWEEN 1 AND 40))"
    )
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT users_department_check "
        "CHECK (department IS NULL OR "
        "(department = btrim(department) AND char_length(department) BETWEEN 1 AND 80))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN employee_id, DROP COLUMN department")
