"""P23: content is private to the college (D165).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-27

Subjects, questions and their versions, reference answers, rubric criteria, glossaries,
reference diagrams, key files and exam blueprints stop being global content:

- ``college_id NOT NULL`` on every content table, back-filled from ``owning_college_id``
  (``question_versions`` from its question), which is dropped together with the
  ``copied_from_*`` columns ("copy to my college" is gone).
- Composite keys and foreign keys like the college tables: ``UNIQUE (college_id, id, version)``
  (``questions``: ``(college_id, id)``), the parts of a question reference
  ``questions (college_id, id)``, ``booklets`` reference ``exam_blueprints (college_id, id,
  version)``: a row cannot point at another college's content whatever RLS says.
- The college RLS policy (``college_isolation``) replaces ``global_read`` / ``owner_insert``.
  Grants stay SELECT, INSERT: versions are still append-only (deletion comes in P28, D166).
- The version trigger now also refuses a row for another college than the session's and a
  version in another college than version 1 (``tarn_check_college_content_version``).
- Content files may live under ``college/{id}/content/``; ``global/`` keys stay allowed until
  ``tarn content relocate-blobs`` has moved them (it updates the keys as the owner).

OCR and scoring calibrations stay Tarn-operator content (D72) and are not touched.

The upgrade first refuses to run when one college's booklets, scores, papers or questions use
another college's content (D60's ownership split): such data has to be removed or split by
hand first (P23 asked the user; the one dev booklet was deleted). The downgrade is for an empty
development database only and refuses when content rows exist.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"

VERSIONED = (
    "subjects",
    "reference_answers",
    "rubric_criteria",
    "glossaries",
    "reference_diagrams",
    "key_files",
    "exam_blueprints",
)
PARTS = ("reference_answers", "rubric_criteria", "glossaries", "reference_diagrams", "key_files")
CONTENT = ("questions", "question_versions", *VERSIONED)
_COPIED = ("copied_from_kind", "copied_from_id", "copied_from_version")
_BLOBS = {"reference_diagrams": "png_key", "key_files": "blob_key"}

# Rows of one college that use another college's content. Each query counts offending rows.
_CROSS_USE = {
    "booklets on another college's paper": """
        SELECT count(*) FROM booklets b JOIN exam_blueprints e
          ON e.id = b.blueprint_id AND e.version = b.blueprint_version
         WHERE e.owning_college_id <> b.college_id""",
    "scores of another college's question": """
        SELECT count(*) FROM answer_scores s JOIN questions q ON q.id = s.question_id
         WHERE q.owning_college_id <> s.college_id""",
    "criterion scores of another college's criterion": """
        SELECT count(*) FROM criterion_scores c JOIN rubric_criteria r
          ON r.id = c.criterion_id AND r.version = c.criterion_version
         WHERE r.owning_college_id <> c.college_id""",
    "papers linking another college's question": """
        SELECT count(*) FROM exam_blueprints e
         CROSS JOIN LATERAL jsonb_path_query(e.sections, 'lax $.**.question_id') AS l(qid)
          JOIN questions q ON jsonb_typeof(l.qid) = 'string' AND q.id = (l.qid #>> '{}')::uuid
         WHERE q.owning_college_id <> e.owning_college_id""",
    "papers on another college's subject": """
        SELECT count(*) FROM exam_blueprints e JOIN subjects s
          ON s.id = e.subject_id AND s.version = 1
         WHERE s.owning_college_id <> e.owning_college_id""",
    "questions on another college's subject": """
        SELECT count(*) FROM questions q JOIN subjects s ON s.id = q.subject_id AND s.version = 1
         WHERE s.owning_college_id <> q.owning_college_id""",
    **{
        f"{table} of another college's question": f"""
        SELECT count(*) FROM {table} t JOIN questions q ON q.id = t.question_id
         WHERE q.owning_college_id <> t.owning_college_id"""
        for table in PARTS
    },
    "diagram criteria on another college's diagram": """
        SELECT count(*) FROM rubric_criteria r JOIN reference_diagrams d
          ON d.id = (r.params ->> 'reference_diagram_id')::uuid AND d.version = 1
         WHERE r.type = 'diagram' AND d.owning_college_id <> r.owning_college_id""",
}
# Tables the checks read; FORCE RLS would hide rows from a non-superuser owner.
_CHECKED = (*CONTENT, "booklets", "answer_scores", "criterion_scores")


def _functions_sql() -> list[str]:
    return [
        """
        CREATE FUNCTION tarn_check_college_content_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            key_col text := TG_ARGV[0];
            current uuid := tarn_current_college();
            item uuid;
            latest integer;
            first_college uuid;
        BEGIN
            IF current IS NOT NULL AND NEW.college_id IS DISTINCT FROM current THEN
                RAISE EXCEPTION '%: content of another college', TG_TABLE_NAME
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            EXECUTE format('SELECT ($1).%I', key_col) INTO item USING NEW;
            EXECUTE format(
                'SELECT max(version), (array_agg(college_id ORDER BY version))[1] '
                'FROM %I.%I WHERE %I = $1', TG_TABLE_SCHEMA, TG_TABLE_NAME, key_col)
                INTO latest, first_college USING item;
            IF NEW.version <> coalesce(latest, 0) + 1 THEN
                RAISE EXCEPTION '%: % expected version %, got %',
                    TG_TABLE_NAME, item, coalesce(latest, 0) + 1, NEW.version
                    USING ERRCODE = 'check_violation';
            END IF;
            IF latest IS NOT NULL AND first_college IS DISTINCT FROM NEW.college_id THEN
                RAISE EXCEPTION '%: % cannot move to another college', TG_TABLE_NAME, item
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END $$
        """,
    ]


def _refuse_cross_use() -> None:
    bind = op.get_bind()
    found = {
        what: int(bind.execute(text(query)).scalar_one()) for what, query in _CROSS_USE.items()
    }
    problems = [f"{n} {what}" for what, n in found.items() if n]
    if problems:
        raise RuntimeError(
            "content is used across colleges; split or remove it before migrating (P23, D165): "
            + "; ".join(problems)
        )


def _add_college_column(table: str) -> list[str]:
    source = (
        "UPDATE question_versions v SET college_id = q.owning_college_id "
        "FROM questions q WHERE q.id = v.question_id"
        if table == "question_versions"
        else f"UPDATE {table} SET college_id = owning_college_id"
    )
    return [
        f"ALTER TABLE {table} ADD COLUMN college_id uuid",
        source,
        f"ALTER TABLE {table} ALTER COLUMN college_id SET NOT NULL",
        f"ALTER TABLE {table} ADD FOREIGN KEY (college_id) REFERENCES colleges (id)",
    ]


def _drop_owner_columns(table: str) -> list[str]:
    sql = [f"ALTER TABLE {table} DROP COLUMN {c}" for c in _COPIED]
    if table != "question_versions":
        sql.append(f"ALTER TABLE {table} DROP COLUMN owning_college_id")
    return sql


def upgrade() -> None:
    for table in _CHECKED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    _refuse_cross_use()
    statements = _functions_sql()
    # The old policies first: question_versions' insert policy reads questions' owner column.
    for table in CONTENT:
        statements += [
            f"DROP POLICY global_read ON {table}",
            f"DROP POLICY owner_insert ON {table}",
        ]
    for table in CONTENT:
        statements += _add_college_column(table)
    for table in CONTENT:
        statements += _drop_owner_columns(table)
    statements += [
        "ALTER TABLE questions ADD CONSTRAINT questions_college_id_id_key UNIQUE (college_id, id)",
        *(
            f"ALTER TABLE {t} ADD CONSTRAINT {t}_college_id_id_version_key "
            "UNIQUE (college_id, id, version)"
            for t in VERSIONED
        ),
        "ALTER TABLE question_versions DROP CONSTRAINT question_versions_question_id_fkey",
        "ALTER TABLE question_versions ADD CONSTRAINT question_versions_question_fkey "
        "FOREIGN KEY (college_id, question_id) REFERENCES questions (college_id, id)",
        *(
            stmt
            for t in PARTS
            for stmt in (
                f"ALTER TABLE {t} DROP CONSTRAINT {t}_question_id_fkey",
                f"ALTER TABLE {t} ADD CONSTRAINT {t}_question_fkey "
                "FOREIGN KEY (college_id, question_id) REFERENCES questions (college_id, id)",
            )
        ),
        "ALTER TABLE booklets DROP CONSTRAINT booklets_blueprint_id_blueprint_version_fkey",
        "ALTER TABLE booklets ADD CONSTRAINT booklets_blueprint_fkey "
        "FOREIGN KEY (college_id, blueprint_id, blueprint_version) "
        "REFERENCES exam_blueprints (college_id, id, version)",
        *(
            stmt
            for t, column in _BLOBS.items()
            for stmt in (
                f"ALTER TABLE {t} DROP CONSTRAINT {t}_{column}_check",
                f"ALTER TABLE {t} ADD CONSTRAINT {t}_{column}_check CHECK ({column} LIKE "
                f"'college/' || college_id::text || '/content/%' OR {column} LIKE 'global/%')",
            )
        ),
        *(
            stmt
            for t in CONTENT
            for stmt in (
                f"CREATE POLICY college_isolation ON {t} "
                f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
                f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
            )
        ),
        *(
            stmt
            for t in (*VERSIONED, "question_versions")
            for stmt in (
                f"DROP TRIGGER content_version ON {t}",
                f"CREATE TRIGGER content_version BEFORE INSERT ON {t} FOR EACH ROW "
                "EXECUTE FUNCTION tarn_check_college_content_version("
                f"'{'question_id' if t == 'question_versions' else 'id'}')",
            )
        ),
        *(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY" for t in _CHECKED if t not in CONTENT),
    ]
    for statement in statements:
        op.execute(statement)


# --- downgrade (empty development database only) --------------------------------------------

_META = """
    ADD COLUMN owning_college_id uuid NOT NULL REFERENCES colleges (id),
    ADD COLUMN copied_from_kind text,
    ADD COLUMN copied_from_id uuid,
    ADD COLUMN copied_from_version integer,
    ADD CHECK ((copied_from_kind IS NULL) = (copied_from_id IS NULL)
           AND (copied_from_id IS NULL) = (copied_from_version IS NULL))
"""
_VERSION_META = """
    ADD COLUMN copied_from_kind text,
    ADD COLUMN copied_from_id uuid,
    ADD COLUMN copied_from_version integer,
    ADD CHECK ((copied_from_kind IS NULL) = (copied_from_id IS NULL)
           AND (copied_from_id IS NULL) = (copied_from_version IS NULL))
"""


def downgrade() -> None:
    bind = op.get_bind()
    for table in CONTENT:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        if bind.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar_one():
            raise RuntimeError(
                f"{table} has rows: 0012 can only be downgraded on an empty database "
                "(development only)"
            )
    statements: list[str] = [
        "ALTER TABLE booklets DROP CONSTRAINT booklets_blueprint_fkey",
        *(f"ALTER TABLE {t} DROP CONSTRAINT {t}_question_fkey" for t in PARTS),
        "ALTER TABLE question_versions DROP CONSTRAINT question_versions_question_fkey",
        *(f"ALTER TABLE {t} DROP CONSTRAINT {t}_college_id_id_version_key" for t in VERSIONED),
        "ALTER TABLE questions DROP CONSTRAINT questions_college_id_id_key",
        *(f"ALTER TABLE {t} DROP CONSTRAINT {t}_{c}_check" for t, c in _BLOBS.items()),
    ]
    for t in CONTENT:
        statements += [
            f"DROP POLICY college_isolation ON {t}",
            f"ALTER TABLE {t} {_VERSION_META if t == 'question_versions' else _META}",
            f"ALTER TABLE {t} DROP COLUMN college_id",
        ]
    for t in (*VERSIONED, "question_versions"):
        key, owner = ("question_id", "false") if t == "question_versions" else ("id", "true")
        statements += [
            f"DROP TRIGGER content_version ON {t}",
            f"CREATE TRIGGER content_version BEFORE INSERT ON {t} FOR EACH ROW "
            f"EXECUTE FUNCTION tarn_check_content_version('{key}', '{owner}')",
        ]
    statements += [
        "DROP FUNCTION tarn_check_college_content_version()",
        "ALTER TABLE question_versions ADD CONSTRAINT question_versions_question_id_fkey "
        "FOREIGN KEY (question_id) REFERENCES questions (id)",
        *(
            f"ALTER TABLE {t} ADD CONSTRAINT {t}_question_id_fkey "
            "FOREIGN KEY (question_id) REFERENCES questions (id)"
            for t in PARTS
        ),
        "ALTER TABLE booklets ADD CONSTRAINT booklets_blueprint_id_blueprint_version_fkey "
        "FOREIGN KEY (blueprint_id, blueprint_version) REFERENCES exam_blueprints (id, version)",
        *(
            f"ALTER TABLE {t} ADD CONSTRAINT {t}_{c}_check CHECK ({c} LIKE 'global/%')"
            for t, c in _BLOBS.items()
        ),
    ]
    for t in CONTENT:
        owner_check = (
            "EXISTS (SELECT 1 FROM questions q WHERE q.id = question_id "
            f"AND q.owning_college_id = {_CURRENT})"
            if t == "question_versions"
            else f"owning_college_id = {_CURRENT}"
        )
        statements += [
            f"CREATE POLICY global_read ON {t} FOR SELECT USING (true)",
            f"CREATE POLICY owner_insert ON {t} FOR INSERT WITH CHECK ({owner_check})",
            f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY",
        ]
    for statement in statements:
        op.execute(statement)
