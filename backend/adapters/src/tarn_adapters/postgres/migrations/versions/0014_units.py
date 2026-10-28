"""P26: units of work (D162) and exam codes.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-28

- ``exam_blueprints.exam_code`` (blueprint document 1.1): names the exam; unique per college
  among the papers that carry one (checked by the service, like question codes).
- ``units``: one exam + one student year + one subject, one paper, one SME. College RLS,
  composite foreign keys to the paper (its identity row, version 1), the subject, the owner and
  the creator, all of the same college. Unique code and (paper, lower(year)) per college. The
  app role may SELECT, INSERT and UPDATE (no DELETE: units stay, P28 freezes them).
- ``booklets.unit_id`` (NOT NULL, composite foreign key): every booklet belongs to a unit.
  Booklets uploaded before units are back-filled: one unit per (college, paper) in use, the
  year of the earliest upload, due today, owned by the first active SME (by name) entitled to
  the paper's subject, ``created_by`` NULL. With no such SME the upgrade refuses: the data is
  fixed by hand first, never guessed (as D170).

The downgrade is for development: it refuses while a unit exists.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"
# The back-fill reads and writes these; FORCE RLS would hide rows from the owner.
_TOUCHED = ("booklets", "users", "sme_subjects", "exam_blueprints")

_PAPERS_IN_USE = """
    SELECT b.college_id, b.blueprint_id AS paper_id, min(b.uploaded_at) AS first_at,
           p.subject_id, p.title,
           (SELECT u.id
              FROM users u
              JOIN sme_subjects g
                ON g.college_id = u.college_id AND g.user_id = u.id
               AND g.subject_id = p.subject_id
             WHERE u.college_id = b.college_id AND u.role = 'sme' AND u.active
             ORDER BY lower(u.display_name), u.id
             LIMIT 1) AS owner_id
      FROM booklets b
      JOIN LATERAL (
            SELECT e.subject_id, e.title
              FROM exam_blueprints e
             WHERE e.college_id = b.college_id AND e.id = b.blueprint_id
             ORDER BY e.version DESC
             LIMIT 1) p ON true
     GROUP BY b.college_id, b.blueprint_id, p.subject_id, p.title
"""


def _backfill() -> None:
    bind = op.get_bind()
    orphans = bind.execute(
        text(f"SELECT count(*) FROM ({_PAPERS_IN_USE}) x WHERE owner_id IS NULL")
    ).scalar_one()
    if orphans:
        raise RuntimeError(
            f"{orphans} paper(s) with booklets have no active SME entitled to their subject: "
            "grant the subject to an SME (or remove the booklets) before upgrading"
        )
    op.execute(
        f"""
        INSERT INTO units (college_id, id, code, paper_id, subject_id, year, owner_id,
                           due_date, created_by, created_at)
        SELECT college_id, gen_random_uuid(),
               'U-' || lpad((row_number() OVER (PARTITION BY college_id
                                                ORDER BY lower(title), paper_id))::text, 4, '0'),
               paper_id, subject_id, to_char(first_at AT TIME ZONE 'UTC', 'YYYY'), owner_id,
               current_date, NULL, now()
          FROM ({_PAPERS_IN_USE}) x
        """
    )
    op.execute(
        "UPDATE booklets b SET unit_id = u.id FROM units u "
        "WHERE u.college_id = b.college_id AND u.paper_id = b.blueprint_id"
    )


def upgrade() -> None:
    for table in _TOUCHED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE exam_blueprints ADD COLUMN exam_code text NOT NULL DEFAULT '' "
        "CHECK (exam_code = btrim(exam_code) AND length(exam_code) <= 50)"
    )
    op.execute(
        """
        CREATE TABLE units (
            college_id uuid NOT NULL REFERENCES colleges (id),
            id uuid NOT NULL,
            code text NOT NULL CHECK (code ~ '^U-[0-9]{4,}$'),
            paper_id uuid NOT NULL,
            paper_version integer NOT NULL DEFAULT 1 CHECK (paper_version = 1),
            subject_id uuid NOT NULL,
            subject_version integer NOT NULL DEFAULT 1 CHECK (subject_version = 1),
            year text NOT NULL
                CHECK (year = btrim(year) AND length(year) BETWEEN 1 AND 20),
            owner_id uuid NOT NULL,
            due_date date NOT NULL,
            state text NOT NULL DEFAULT 'open' CHECK (state IN ('open')),
            created_by uuid,
            created_at timestamptz NOT NULL,
            version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, id),
            UNIQUE (id),
            UNIQUE (college_id, code),
            FOREIGN KEY (college_id, paper_id, paper_version)
                REFERENCES exam_blueprints (college_id, id, version),
            FOREIGN KEY (college_id, subject_id, subject_version)
                REFERENCES subjects (college_id, id, version),
            FOREIGN KEY (college_id, owner_id) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, created_by) REFERENCES users (college_id, id)
        )
        """
    )
    for statement in (
        "CREATE UNIQUE INDEX units_paper_year ON units (college_id, paper_id, lower(year))",
        "CREATE INDEX ON units (college_id, owner_id)",
        "ALTER TABLE units ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE units FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY college_isolation ON units "
        f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        f"GRANT SELECT, INSERT, UPDATE ON units TO {APP_ROLE}",
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
        "ALTER TABLE booklets ADD COLUMN unit_id uuid",
    ):
        op.execute(statement)
    _backfill()
    for statement in (
        "ALTER TABLE booklets ALTER COLUMN unit_id SET NOT NULL",
        "ALTER TABLE booklets ADD CONSTRAINT booklets_unit_fk "
        "FOREIGN KEY (college_id, unit_id) REFERENCES units (college_id, id)",
        "CREATE INDEX ON booklets (college_id, unit_id)",
        *(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" for table in _TOUCHED),
    ):
        op.execute(statement)


def downgrade() -> None:
    op.execute("ALTER TABLE units NO FORCE ROW LEVEL SECURITY")
    if op.get_bind().execute(text("SELECT EXISTS (SELECT 1 FROM units)")).scalar():
        op.execute("ALTER TABLE units FORCE ROW LEVEL SECURITY")
        raise RuntimeError("units exist: 0014 can only be downgraded without them")
    for statement in (
        "ALTER TABLE booklets DROP CONSTRAINT booklets_unit_fk",
        "ALTER TABLE booklets DROP COLUMN unit_id",
        "DROP TABLE units",
        "ALTER TABLE exam_blueprints DROP COLUMN exam_code",
    ):
        op.execute(statement)
