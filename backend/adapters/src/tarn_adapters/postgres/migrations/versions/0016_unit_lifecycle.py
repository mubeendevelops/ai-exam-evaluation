"""P28: submit, return and freeze units; delete content after the freeze (D164, D166).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-28

- ``units.state``: open, submitted, frozen. A frozen unit row never changes again (trigger).
- ``unit_submissions``: each time the SME submits a unit; the admin's return fills
  ``returned_by``, ``returned_at`` and ``return_note`` once (trigger: nothing else changes and
  a returned row is closed). SELECT, INSERT, UPDATE.
- ``unmarked_booklets``: the SME's reason per booklet not marked (absent, unreadable, other
  with a note); goes with the booklet (ON DELETE CASCADE: a returned unit's booklet may still
  be deleted). SELECT, INSERT.
- ``frozen_units``, ``frozen_booklets``, ``frozen_answers``: the frozen record written by the
  Governor's freeze, insert only (SELECT, INSERT). Foreign keys to the unit and its submission
  only, never to content or booklets: the record outlives both (D166).
- **Closed units take no writes** (second line behind the core's ``ensure_unit_open``): no
  booklet is inserted into a unit that is not open; booklets of a submitted or frozen unit,
  and their pages, segments, answers, drawings, amendments, OCR edits and result sheets,
  cannot be inserted, updated or deleted.
- **Content deletion**: the foreign keys from ``booklets`` and ``units`` to ``exam_blueprints``
  are dropped (the columns stay, like the scores' content references, D171), because frozen
  booklets and units outlive a deleted paper. The app role gets no DELETE on content: it
  calls ``tarn_delete_paper(uuid)`` / ``tarn_delete_question(uuid)`` (SECURITY DEFINER), which
  check the session's college and the rule of D166, then delete every version (and a
  question's parts).

College RLS forced on the new tables. The downgrade is for development: it refuses while a
unit is not open, a submission or a frozen record exists.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"
_CURRENT = "tarn_current_college()"
_NOTE = "CHECK ({0} = btrim({0}) AND length({0}) <= 500)"
_UNIT_DATA = (
    "answers",
    "pages",
    "segments",
    "diagram_graphs",
    "amendments",
    "region_edits",
    "result_sheets",
)
"""Tables keyed by ``booklet_id`` that a closed unit's booklets may not change."""
_PARTS = (
    "key_files",
    "reference_diagrams",
    "glossaries",
    "rubric_criteria",
    "reference_answers",
    "question_versions",
)
_LINKS = (
    "jsonb_path_exists({0}, '$.**.question_id ? (@ == $q)', jsonb_build_object('q', {1}::text))"
)


def _rls(table: str, grants: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY college_isolation ON {table} "
        f"USING (college_id = {_CURRENT}) WITH CHECK (college_id = {_CURRENT})",
        f"GRANT {grants} ON {table} TO {APP_ROLE}",
    ]


def _tables() -> list[str]:
    return [
        f"""
        CREATE TABLE unit_submissions (
            college_id uuid NOT NULL REFERENCES colleges (id),
            id uuid NOT NULL,
            unit_id uuid NOT NULL,
            submitted_by uuid NOT NULL,
            submitted_at timestamptz NOT NULL,
            returned_by uuid,
            returned_at timestamptz,
            return_note text NOT NULL DEFAULT '' {_NOTE.format("return_note")},
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, id),
            UNIQUE (id),
            CHECK ((returned_by IS NULL) = (returned_at IS NULL)),
            CHECK ((returned_at IS NULL) = (return_note = '')),
            FOREIGN KEY (college_id, unit_id) REFERENCES units (college_id, id),
            FOREIGN KEY (college_id, submitted_by) REFERENCES users (college_id, id),
            FOREIGN KEY (college_id, returned_by) REFERENCES users (college_id, id)
        )
        """,
        f"""
        CREATE TABLE unmarked_booklets (
            college_id uuid NOT NULL,
            submission_id uuid NOT NULL,
            booklet_id uuid NOT NULL,
            reason text NOT NULL CHECK (reason IN ('absent', 'unreadable', 'other')),
            note text NOT NULL DEFAULT '' {_NOTE.format("note")},
            PRIMARY KEY (college_id, submission_id, booklet_id),
            CHECK (reason <> 'other' OR note <> ''),
            FOREIGN KEY (college_id, submission_id)
                REFERENCES unit_submissions (college_id, id),
            FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE frozen_units (
            college_id uuid NOT NULL REFERENCES colleges (id),
            unit_id uuid NOT NULL,
            unit_code text NOT NULL,
            year text NOT NULL,
            subject_id uuid NOT NULL,
            subject_code text NOT NULL,
            subject_name text NOT NULL,
            submission_id uuid NOT NULL,
            frozen_by uuid NOT NULL,
            frozen_at timestamptz NOT NULL,
            papers jsonb NOT NULL CHECK (jsonb_typeof(papers) = 'array'),
            PRIMARY KEY (college_id, unit_id),
            FOREIGN KEY (college_id, unit_id) REFERENCES units (college_id, id),
            FOREIGN KEY (college_id, submission_id)
                REFERENCES unit_submissions (college_id, id),
            FOREIGN KEY (college_id, frozen_by) REFERENCES users (college_id, id)
        )
        """,
        """
        CREATE TABLE frozen_booklets (
            college_id uuid NOT NULL,
            unit_id uuid NOT NULL,
            booklet_id uuid NOT NULL,
            student_id uuid NOT NULL,
            paper_id uuid NOT NULL,
            paper_version integer NOT NULL CHECK (paper_version >= 1),
            outcome text NOT NULL CHECK (outcome IN ('approved', 'unmarked')),
            reason text CHECK (reason IN ('absent', 'unreadable', 'other')),
            note text NOT NULL DEFAULT '',
            total numeric,
            max_marks numeric NOT NULL,
            sheets jsonb NOT NULL CHECK (jsonb_typeof(sheets) = 'array'),
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, booklet_id),
            CHECK ((outcome = 'unmarked') = (reason IS NOT NULL)),
            FOREIGN KEY (college_id, unit_id) REFERENCES frozen_units (college_id, unit_id)
        )
        """,
        """
        CREATE TABLE frozen_answers (
            college_id uuid NOT NULL,
            booklet_id uuid NOT NULL,
            answer_id uuid NOT NULL,
            slot_label text NOT NULL,
            question_id uuid,
            question_version integer,
            question_code text NOT NULL DEFAULT '',
            max_marks numeric NOT NULL,
            ai_mark numeric,
            sme_mark numeric,
            reviewer uuid,
            reviewed_at timestamptz,
            counted boolean NOT NULL,
            seq bigint GENERATED ALWAYS AS IDENTITY,
            PRIMARY KEY (college_id, answer_id),
            CHECK ((sme_mark IS NULL) = (reviewer IS NULL)),
            FOREIGN KEY (college_id, booklet_id)
                REFERENCES frozen_booklets (college_id, booklet_id)
        )
        """,
        "CREATE INDEX ON unit_submissions (college_id, unit_id)",
        "CREATE INDEX ON frozen_booklets (college_id, unit_id)",
        "CREATE INDEX ON frozen_answers (college_id, booklet_id)",
        *_rls("unit_submissions", "SELECT, INSERT, UPDATE"),
        *_rls("unmarked_booklets", "SELECT, INSERT"),
        *_rls("frozen_units", "SELECT, INSERT"),
        *_rls("frozen_booklets", "SELECT, INSERT"),
        *_rls("frozen_answers", "SELECT, INSERT"),
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ]


def _triggers() -> list[str]:
    sql = [
        """
        CREATE FUNCTION tarn_frozen_unit_stays() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.state = 'frozen' THEN
                RAISE EXCEPTION 'unit % is frozen', OLD.id USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
        """,
        "CREATE TRIGGER frozen_unit_stays BEFORE UPDATE ON units "
        "FOR EACH ROW EXECUTE FUNCTION tarn_frozen_unit_stays()",
        """
        CREATE FUNCTION tarn_submission_returned_once() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.returned_at IS NOT NULL
               OR NEW.unit_id <> OLD.unit_id OR NEW.submitted_by <> OLD.submitted_by
               OR NEW.submitted_at <> OLD.submitted_at OR NEW.id <> OLD.id THEN
                RAISE EXCEPTION 'a submission changes only by its return, once'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END $$
        """,
        "CREATE TRIGGER submission_returned_once BEFORE UPDATE ON unit_submissions "
        "FOR EACH ROW EXECUTE FUNCTION tarn_submission_returned_once()",
        # A booklet goes into an open unit only; a closed unit's booklets do not change.
        """
        CREATE FUNCTION tarn_booklet_unit_open() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            unit uuid;
            closed text;
        BEGIN
            unit := CASE WHEN TG_OP = 'INSERT' THEN NEW.unit_id ELSE OLD.unit_id END;
            SELECT state INTO closed FROM units
             WHERE id = unit AND college_id = tarn_current_college() AND state <> 'open';
            IF closed IS NOT NULL THEN
                RAISE EXCEPTION 'unit % is %', unit, closed USING ERRCODE = 'check_violation';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """,
        "CREATE TRIGGER booklet_unit_open BEFORE INSERT OR UPDATE OR DELETE ON booklets "
        "FOR EACH ROW EXECUTE FUNCTION tarn_booklet_unit_open()",
        """
        CREATE FUNCTION tarn_booklet_data_unit_open() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            booklet uuid;
            closed text;
        BEGIN
            booklet := CASE WHEN TG_OP = 'DELETE' THEN OLD.booklet_id ELSE NEW.booklet_id END;
            SELECT u.state INTO closed FROM booklets b JOIN units u
                ON u.college_id = b.college_id AND u.id = b.unit_id
             WHERE b.id = booklet AND b.college_id = tarn_current_college()
               AND u.state <> 'open';
            IF closed IS NOT NULL THEN
                RAISE EXCEPTION 'the booklet''s unit is %', closed
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END $$
        """,
    ]
    sql += [
        f"CREATE TRIGGER booklet_data_unit_open BEFORE INSERT OR UPDATE OR DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION tarn_booklet_data_unit_open()"
        for table in _UNIT_DATA
    ]
    return sql


def _deletion() -> list[str]:
    parts = "\n".join(
        f"            DELETE FROM {t} WHERE question_id = p_question AND college_id = college;\n"
        "            GET DIAGNOSTICS n = ROW_COUNT; total := total + n;"
        for t in _PARTS
    )
    return [
        "ALTER TABLE booklets DROP CONSTRAINT booklets_blueprint_fkey",
        "ALTER TABLE units DROP CONSTRAINT units_college_id_paper_id_paper_version_fkey",
        f"""
        CREATE FUNCTION tarn_delete_paper(p_paper uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
            college uuid := {_CURRENT};
            n integer;
        BEGIN
            IF college IS NULL THEN
                RAISE EXCEPTION 'no college set' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM exam_blueprints
                           WHERE id = p_paper AND college_id = college) THEN
                RAISE EXCEPTION 'no such paper' USING ERRCODE = 'no_data_found';
            END IF;
            IF EXISTS (
                SELECT 1 FROM units u
                 WHERE u.college_id = college AND u.state <> 'frozen'
                   AND (u.paper_id = p_paper OR EXISTS (
                        SELECT 1 FROM booklets b
                         WHERE b.college_id = college AND b.unit_id = u.id
                           AND b.blueprint_id = p_paper))
            ) THEN
                RAISE EXCEPTION 'a unit that is not frozen uses the paper'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            DELETE FROM exam_blueprints WHERE id = p_paper AND college_id = college;
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$
        """,
        f"""
        CREATE FUNCTION tarn_delete_question(p_question uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
            college uuid := {_CURRENT};
            n integer;
            total integer := 0;
        BEGIN
            IF college IS NULL THEN
                RAISE EXCEPTION 'no college set' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM questions
                           WHERE id = p_question AND college_id = college) THEN
                RAISE EXCEPTION 'no such question' USING ERRCODE = 'no_data_found';
            END IF;
            IF EXISTS (
                SELECT 1 FROM exam_blueprints e
                 WHERE e.college_id = college
                   AND e.version = (SELECT max(e2.version) FROM exam_blueprints e2
                                     WHERE e2.id = e.id AND e2.college_id = college)
                   AND {_LINKS.format("e.sections", "p_question")}
            ) OR EXISTS (
                SELECT 1 FROM booklets b
                  JOIN units u ON u.college_id = b.college_id AND u.id = b.unit_id
                  JOIN exam_blueprints e ON e.college_id = b.college_id
                   AND e.id = b.blueprint_id AND e.version = b.blueprint_version
                 WHERE b.college_id = college AND u.state <> 'frozen'
                   AND {_LINKS.format("e.sections", "p_question")}
            ) OR EXISTS (
                SELECT 1 FROM answer_scores s
                  JOIN answers a ON a.college_id = s.college_id AND a.id = s.answer_id
                  JOIN booklets b ON b.college_id = a.college_id AND b.id = a.booklet_id
                  JOIN units u ON u.college_id = b.college_id AND u.id = b.unit_id
                 WHERE s.college_id = college AND s.question_id = p_question
                   AND u.state <> 'frozen'
            ) THEN
                RAISE EXCEPTION 'a paper or a unit that is not frozen uses the question'
                    USING ERRCODE = 'restrict_violation';
            END IF;
{parts}
            DELETE FROM questions WHERE id = p_question AND college_id = college;
            GET DIAGNOSTICS n = ROW_COUNT; total := total + n;
            RETURN total;
        END $$
        """,
        # The dropped foreign keys made sure a booklet or unit names a paper of its own
        # college (D170); this trigger keeps that check for every new or changed reference.
        # AFTER the row is written, so row-level security and conflicts are decided first.
        """
        CREATE FUNCTION tarn_paper_of_college() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            paper uuid;
            v integer;
        BEGIN
            IF TG_TABLE_NAME = 'booklets' THEN
                paper := NEW.blueprint_id; v := NEW.blueprint_version;
            ELSE
                paper := NEW.paper_id; v := NEW.paper_version;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM exam_blueprints
                           WHERE college_id = NEW.college_id AND id = paper AND version = v) THEN
                RAISE EXCEPTION 'no such paper in the college'
                    USING ERRCODE = 'foreign_key_violation';
            END IF;
            RETURN NEW;
        END $$
        """,
        "CREATE TRIGGER booklet_paper_of_college AFTER INSERT OR UPDATE OF blueprint_id, "
        "blueprint_version, college_id ON booklets "
        "FOR EACH ROW EXECUTE FUNCTION tarn_paper_of_college()",
        "CREATE TRIGGER unit_paper_of_college AFTER INSERT OR UPDATE OF paper_id, "
        "paper_version, college_id ON units "
        "FOR EACH ROW EXECUTE FUNCTION tarn_paper_of_college()",
        "REVOKE ALL ON FUNCTION tarn_delete_paper(uuid) FROM PUBLIC",
        "REVOKE ALL ON FUNCTION tarn_delete_question(uuid) FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION tarn_delete_paper(uuid) TO {APP_ROLE}",
        f"GRANT EXECUTE ON FUNCTION tarn_delete_question(uuid) TO {APP_ROLE}",
    ]


def upgrade() -> None:
    op.execute("ALTER TABLE units DROP CONSTRAINT units_state_check")
    op.execute(
        "ALTER TABLE units ADD CONSTRAINT units_state_check "
        "CHECK (state IN ('open', 'submitted', 'frozen'))"
    )
    for statement in (*_tables(), *_triggers(), *_deletion()):
        op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    tables = ("units", "unit_submissions", "frozen_units")
    for table in tables:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    used = bind.execute(
        text(
            "SELECT EXISTS (SELECT 1 FROM units WHERE state <> 'open') "
            "OR EXISTS (SELECT 1 FROM unit_submissions) OR EXISTS (SELECT 1 FROM frozen_units)"
        )
    ).scalar()
    for table in tables:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if used:
        raise RuntimeError(
            "units were submitted or frozen: 0016 can only be downgraded without them"
        )
    statements = [
        "DROP TRIGGER IF EXISTS booklet_paper_of_college ON booklets",
        "DROP TRIGGER IF EXISTS unit_paper_of_college ON units",
        "DROP FUNCTION IF EXISTS tarn_paper_of_college()",
        "DROP FUNCTION tarn_delete_question(uuid)",
        "DROP FUNCTION tarn_delete_paper(uuid)",
        *(f"DROP TRIGGER booklet_data_unit_open ON {t}" for t in _UNIT_DATA),
        "DROP TRIGGER booklet_unit_open ON booklets",
        "DROP TRIGGER frozen_unit_stays ON units",
        "DROP FUNCTION tarn_booklet_data_unit_open()",
        "DROP FUNCTION tarn_booklet_unit_open()",
        "DROP FUNCTION tarn_frozen_unit_stays()",
        "DROP TABLE frozen_answers",
        "DROP TABLE frozen_booklets",
        "DROP TABLE frozen_units",
        "DROP TABLE unmarked_booklets",
        "DROP TABLE unit_submissions",
        "DROP FUNCTION tarn_submission_returned_once()",
        "ALTER TABLE units DROP CONSTRAINT units_state_check",
        "ALTER TABLE units ADD CONSTRAINT units_state_check CHECK (state IN ('open'))",
        "ALTER TABLE booklets ADD CONSTRAINT booklets_blueprint_fkey "
        "FOREIGN KEY (college_id, blueprint_id, blueprint_version) "
        "REFERENCES exam_blueprints (college_id, id, version)",
        "ALTER TABLE units ADD CONSTRAINT units_college_id_paper_id_paper_version_fkey "
        "FOREIGN KEY (college_id, paper_id, paper_version) "
        "REFERENCES exam_blueprints (college_id, id, version)",
    ]
    for statement in statements:
        op.execute(statement)
