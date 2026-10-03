"""P7: question bank: code and difficulty, retired keys and criteria, answer-key files, and the
public directory of college names.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-03

- ``question_versions.code`` (unique per owning college, checked by the service) and
  ``difficulty``.
- ``reference_answers.retired`` and ``rubric_criteria.retired``: removing a key or a criterion
  saves a retired version, so global rows stay append-only (D35) and old scores keep theirs.
- ``key_files``: answer-key files attached to a question (blob under ``global/keys/``). The
  uploader's confirmation that the file holds no student data (C9) is a column that must be true.
- ``college_directory``: id and name of every college, readable by all, so a teacher can see
  *which college owns* a piece of global content. ``colleges`` itself stays isolated by RLS.
  (Backfilled from ``colleges`` here; on a database that already has colleges and a
  non-superuser owner, run the INSERT with row security off first.)
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"

# Same owner and version columns as every versioned global table (revision 0001).
_META = """
    owning_college_id uuid NOT NULL REFERENCES colleges (id),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    copied_from_kind text,
    copied_from_id uuid,
    copied_from_version integer,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    CHECK ((copied_from_kind IS NULL) = (copied_from_id IS NULL)
       AND (copied_from_id IS NULL) = (copied_from_version IS NULL))
"""


def upgrade() -> None:
    for statement in (
        "ALTER TABLE question_versions ADD COLUMN code text NOT NULL DEFAULT ''",
        "UPDATE question_versions SET code = 'Q-' || left(question_id::text, 8) WHERE code = ''",
        "ALTER TABLE question_versions ALTER COLUMN code DROP DEFAULT",
        "ALTER TABLE question_versions ADD CONSTRAINT question_code_shape "
        "CHECK (code <> '' AND code = btrim(code) AND length(code) <= 40)",
        "ALTER TABLE question_versions ADD COLUMN difficulty text NOT NULL DEFAULT 'medium' "
        "CHECK (difficulty IN ('easy', 'medium', 'hard'))",
        "ALTER TABLE reference_answers ADD COLUMN retired boolean NOT NULL DEFAULT false",
        "ALTER TABLE rubric_criteria ADD COLUMN retired boolean NOT NULL DEFAULT false",
        f"""
        CREATE TABLE key_files (
            id uuid NOT NULL,
            version integer NOT NULL CHECK (version >= 1),
            question_id uuid NOT NULL REFERENCES questions (id),
            name text NOT NULL CHECK (btrim(name) <> ''),
            media_type text NOT NULL
                CHECK (media_type IN ('application/pdf', 'image/png', 'image/jpeg')),
            size_bytes bigint NOT NULL CHECK (size_bytes >= 1),
            sha256 text NOT NULL CHECK (length(sha256) = 64),
            blob_key text NOT NULL CHECK (blob_key LIKE 'global/%'),
            keywords text[] NOT NULL DEFAULT '{{}}',
            no_student_data_confirmed boolean NOT NULL CHECK (no_student_data_confirmed),
            {_META},
            PRIMARY KEY (id, version)
        )
        """,
        "CREATE INDEX ON key_files (question_id)",
        "ALTER TABLE key_files ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE key_files FORCE ROW LEVEL SECURITY",
        "CREATE POLICY global_read ON key_files FOR SELECT USING (true)",
        f"CREATE POLICY owner_insert ON key_files FOR INSERT "
        f"WITH CHECK (owning_college_id = {_CURRENT})",
        f"GRANT SELECT, INSERT ON key_files TO {APP_ROLE}",
        "CREATE TRIGGER content_version BEFORE INSERT ON key_files FOR EACH ROW "
        "EXECUTE FUNCTION tarn_check_content_version('id', 'true')",
        """
        CREATE TABLE college_directory (
            id uuid PRIMARY KEY REFERENCES colleges (id),
            name text NOT NULL CHECK (btrim(name) <> '')
        )
        """,
        "INSERT INTO college_directory (id, name) SELECT id, name FROM colleges",
        "ALTER TABLE college_directory ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE college_directory FORCE ROW LEVEL SECURITY",
        "CREATE POLICY directory_read ON college_directory FOR SELECT USING (true)",
        f"CREATE POLICY directory_own ON college_directory FOR INSERT WITH CHECK (id = {_CURRENT})",
        f"CREATE POLICY directory_rename ON college_directory FOR UPDATE "
        f"USING (id = {_CURRENT}) WITH CHECK (id = {_CURRENT})",
        f"GRANT SELECT, INSERT, UPDATE ON college_directory TO {APP_ROLE}",
    ):
        op.execute(statement)


def downgrade() -> None:
    for statement in (
        "DROP TABLE college_directory",
        "DROP TABLE key_files",
        "ALTER TABLE rubric_criteria DROP COLUMN retired",
        "ALTER TABLE reference_answers DROP COLUMN retired",
        "ALTER TABLE question_versions DROP COLUMN difficulty",
        "ALTER TABLE question_versions DROP CONSTRAINT question_code_shape",
        "ALTER TABLE question_versions DROP COLUMN code",
    ):
        op.execute(statement)
