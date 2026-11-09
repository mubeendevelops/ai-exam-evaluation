"""P33: schemas and papers, blueprint document 1.2 (D219, D231).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-09

``exam_blueprints`` gains the 1.2 header as columns, so the lists filter without reading the
sections: ``kind`` (schema | paper), ``target_batch``, ``weightage_note``, ``description``,
``descriptor`` (trimmed texts) and ``from_schema`` (the schema a paper was made from; no
foreign key, because a blueprint's identity is (id, version) and a deleted schema leaves its
papers, D231).

Back-fill, per blueprint (every version gets the same kind): a **paper** when any version has
an exam code or a linked question, or a unit or booklet uses it; otherwise a **schema**. Then a
CHECK keeps a schema free of an exam code, links and ``from_schema``. Indexes for the lists:
``(college_id, kind)`` and the papers made from a schema. The table's row-level security,
policy and grants already cover the new columns.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LINKED = "jsonb_path_exists({0}.sections, '$.**.question_id ? (@ != null)')"
_TEXT = "CHECK ({0} = btrim({0}) AND length({0}) <= {1})"
# The back-fill reads these as the owner; FORCE RLS would hide their rows.
_TOUCHED = ("exam_blueprints", "units", "booklets")


def upgrade() -> None:
    for table in _TOUCHED:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE exam_blueprints "
        "ADD COLUMN kind text NOT NULL DEFAULT 'paper' CHECK (kind IN ('schema', 'paper')), "
        f"ADD COLUMN target_batch text NOT NULL DEFAULT '' {_TEXT.format('target_batch', 100)}, "
        "ADD COLUMN weightage_note text NOT NULL DEFAULT '' "
        f"{_TEXT.format('weightage_note', 1000)}, "
        f"ADD COLUMN description text NOT NULL DEFAULT '' {_TEXT.format('description', 1000)}, "
        f"ADD COLUMN descriptor text NOT NULL DEFAULT '' {_TEXT.format('descriptor', 50)}, "
        "ADD COLUMN from_schema uuid"
    )
    op.execute(
        f"""
        UPDATE exam_blueprints e SET kind = 'schema'
         WHERE NOT EXISTS (
                SELECT 1 FROM exam_blueprints v
                 WHERE v.college_id = e.college_id AND v.id = e.id
                   AND (v.exam_code <> '' OR {_LINKED.format("v")}))
           AND NOT EXISTS (SELECT 1 FROM units u
                            WHERE u.college_id = e.college_id AND u.paper_id = e.id)
           AND NOT EXISTS (SELECT 1 FROM booklets b
                            WHERE b.college_id = e.college_id AND b.blueprint_id = e.id)
        """
    )
    op.execute(
        "ALTER TABLE exam_blueprints ADD CONSTRAINT exam_blueprints_schema_shape CHECK ("
        "kind = 'paper' OR (exam_code = '' AND from_schema IS NULL "
        f"AND NOT {_LINKED.format('exam_blueprints')}))"
    )
    op.execute("CREATE INDEX exam_blueprints_kind ON exam_blueprints (college_id, kind)")
    op.execute(
        "CREATE INDEX exam_blueprints_from_schema ON exam_blueprints (college_id, from_schema) "
        "WHERE from_schema IS NOT NULL"
    )
    for table in _TOUCHED:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE exam_blueprints DROP CONSTRAINT exam_blueprints_schema_shape, "
        "DROP COLUMN from_schema, DROP COLUMN descriptor, DROP COLUMN description, "
        "DROP COLUMN weightage_note, DROP COLUMN target_batch, DROP COLUMN kind"
    )
