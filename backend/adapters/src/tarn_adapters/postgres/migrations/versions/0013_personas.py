"""P24: personas, subject entitlements, retired subjects.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-27

- ``users.role``: ``admin``, ``sme``, ``governor`` (D159, D176). Every ``teacher`` becomes an
  ``sme``. One admin and one governor per college (partial unique indexes; D161, D180). The
  upgrade refuses to run while a college has more than one admin: such accounts are split by
  hand, never guessed.
- ``subjects.retired`` (D181): a retired subject cannot be granted or used for a new paper.
- ``sme_subjects`` (D160, D178): the subjects of each SME. Composite foreign keys tie a grant
  to a user and to version 1 of a subject of the same college (``subject_version`` is always
  1: it only makes the key point at the subject's identity row). College RLS. The app role may
  SELECT, INSERT and DELETE (a revoked grant is gone; the audit log keeps who and when).
  Every existing SME is granted every subject of their college, so today's work stays
  visible (``granted_by`` NULL = this migration).

The downgrade is for development: it refuses while a governor or a retired subject exists,
drops the grants and turns SMEs back into teachers.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"
_FORCED = ("users", "subjects")


def _refuse_two_admins() -> None:
    n = op.get_bind().execute(
        text(
            "SELECT count(*) FROM (SELECT college_id FROM users WHERE role = 'admin' "
            "GROUP BY college_id HAVING count(*) > 1) c"
        )
    )
    colleges = int(n.scalar_one())
    if colleges:
        raise RuntimeError(
            f"{colleges} college(s) have more than one admin account; the admin login is "
            "shared and single (D161): disable or remove the extra accounts by hand first"
        )


def upgrade() -> None:
    for table in _FORCED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    _refuse_two_admins()
    for statement in (
        "ALTER TABLE users DROP CONSTRAINT users_role_check",
        "UPDATE users SET role = 'sme' WHERE role = 'teacher'",
        "ALTER TABLE users ADD CONSTRAINT users_role_check "
        "CHECK (role IN ('admin', 'sme', 'governor'))",
        "CREATE UNIQUE INDEX users_one_admin ON users (college_id) WHERE role = 'admin'",
        "CREATE UNIQUE INDEX users_one_governor ON users (college_id) WHERE role = 'governor'",
        "ALTER TABLE subjects ADD COLUMN retired boolean NOT NULL DEFAULT false",
        """
        CREATE TABLE sme_subjects (
            college_id uuid NOT NULL REFERENCES colleges (id),
            user_id uuid NOT NULL,
            subject_id uuid NOT NULL,
            subject_version integer NOT NULL DEFAULT 1 CHECK (subject_version = 1),
            granted_by uuid,
            granted_at timestamptz NOT NULL,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, user_id, subject_id),
            FOREIGN KEY (college_id, user_id) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, subject_id, subject_version)
                REFERENCES subjects (college_id, id, version),
            FOREIGN KEY (college_id, granted_by) REFERENCES users (college_id, id)
        )
        """,
        "CREATE INDEX ON sme_subjects (college_id, subject_id)",
        """
        INSERT INTO sme_subjects (college_id, user_id, subject_id, granted_at)
        SELECT u.college_id, u.id, s.id, now()
          FROM users u JOIN subjects s ON s.college_id = u.college_id AND s.version = 1
         WHERE u.role = 'sme'
        """,
        "ALTER TABLE sme_subjects ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE sme_subjects FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY college_isolation ON sme_subjects "
        f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        f"GRANT SELECT, INSERT, DELETE ON sme_subjects TO {APP_ROLE}",
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        *(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" for table in _FORCED),
    ):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    for table in _FORCED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    if bind.execute(text("SELECT EXISTS (SELECT 1 FROM users WHERE role = 'governor')")).scalar():
        raise RuntimeError("governor accounts exist: 0013 can only be downgraded without them")
    if bind.execute(text("SELECT EXISTS (SELECT 1 FROM subjects WHERE retired)")).scalar():
        raise RuntimeError("retired subjects exist: 0013 can only be downgraded without them")
    for statement in (
        "DROP TABLE sme_subjects",
        "ALTER TABLE subjects DROP COLUMN retired",
        "DROP INDEX users_one_governor",
        "DROP INDEX users_one_admin",
        "ALTER TABLE users DROP CONSTRAINT users_role_check",
        "UPDATE users SET role = 'teacher' WHERE role = 'sme'",
        "ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('admin', 'teacher'))",
        *(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" for table in _FORCED),
    ):
        op.execute(statement)
