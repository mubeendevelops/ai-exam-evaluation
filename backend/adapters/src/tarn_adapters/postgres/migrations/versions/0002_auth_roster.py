"""P4: roster class/section, audit events without an acting user, database CONNECT limits.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03

- ``students.class_section`` (roster CSV, O8), plus an index for the picker search.
- ``audit_events.actor_id`` may be NULL: a failed sign-in for an unknown email and a tenant
  approval by a Tarn operator have no acting user. The core allows NULL only for those.
- Only the owner and ``tarn_app`` may connect to this database (the identity role
  ``tarn_auth`` cannot, and ``tarn_app`` cannot connect to the identity database).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"


def upgrade() -> None:
    for statement in (
        "ALTER TABLE students ADD COLUMN class_section text NOT NULL DEFAULT '' "
        "CHECK (class_section = btrim(class_section) AND length(class_section) <= 40)",
        "CREATE INDEX students_college_name ON students (college_id, lower(name))",
        "ALTER TABLE audit_events ALTER COLUMN actor_id DROP NOT NULL",
        f"""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC',
                           current_database());
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {APP_ROLE}', current_database());
        END $$
        """,
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        """
        DO $$
        BEGIN
            EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC',
                           current_database());
        END $$
        """,
        # Events without an actor cannot be kept under the old rule; the trigger forbids
        # deleting audit rows, so the downgrade refuses if any exist.
        "ALTER TABLE audit_events ALTER COLUMN actor_id SET NOT NULL",
        "DROP INDEX students_college_name",
        "ALTER TABLE students DROP COLUMN class_section",
    ):
        op.execute(statement)
