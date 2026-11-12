"""P38 item 1: row-level security on what Stage G (P31-P37) added, checked with raw SQL as the
application role, and the blueprint hold of F20 (D245).

New table: ``notifications`` (0022). New columns on college tables: ``users.employee_id`` and
``department`` (0021), the blueprint document 1.2 columns (0019), ``delegation_requests.
suggested_to`` (0018). ``test_pg_security`` already sweeps every college table for crafted
reads and writes; this names the new table and columns (so moving one out of a protected table
fails here) and tries each through B's session against A's rows."""

from uuid import uuid4

import pytest
from psycopg import errors
from sqlalchemy.exc import DBAPIError

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.testing import Opener, TestDatabase, World

pytestmark = pytest.mark.integration

STAGE_G_TABLES = ("notifications",)
STAGE_G_COLUMNS = {
    "users": ("employee_id", "department"),
    "exam_blueprints": (
        "kind",
        "target_batch",
        "weightage_note",
        "description",
        "descriptor",
        "from_schema",
    ),
    "delegation_requests": ("suggested_to",),
}


def test_the_stage_g_table_and_columns_live_under_forced_row_security(
    test_database: TestDatabase,
) -> None:
    college_tables = {t.name for t in m.COLLEGE_TABLES}
    tables = (*STAGE_G_TABLES, *STAGE_G_COLUMNS)
    assert set(tables) <= college_tables
    with test_database.owner() as conn:
        rows = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relname = ANY(%s)",
            (list(tables),),
        ).fetchall()
        assert sorted(rows) == sorted((t, True, True) for t in tables)
        for table, columns in STAGE_G_COLUMNS.items():
            found = {
                str(r[0])
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                    (table,),
                )
            }
            assert set(columns) <= found, table


def test_notices_point_at_users_and_units_of_their_own_college(
    test_database: TestDatabase, world: World
) -> None:
    """Every reference of a notice names the college too, so B's notice cannot name A's
    SME or unit (tried by the owner, which row-level security does not stop)."""
    a, b = world.a, world.b
    with test_database.owner() as conn:
        refs = conn.execute(
            """
            SELECT c.confrelid::regclass::text,
                   array(SELECT a.attname FROM unnest(c.conkey) k(n)
                         JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n)
            FROM pg_constraint c
            WHERE c.contype = 'f' AND c.conrelid = 'notifications'::regclass
            """
        ).fetchall()
        targets = {str(ref): set(cols) for ref, cols in refs}  # type: ignore[call-overload]
        assert targets["users"] == {"college_id", "user_id"}
        assert targets["units"] == {"college_id", "unit_id"}
        a_unit = conn.execute("SELECT id FROM units WHERE college_id = %s", (a.id,)).fetchone()
        b_unit = conn.execute("SELECT id FROM units WHERE college_id = %s", (b.id,)).fetchone()
        assert a_unit is not None and b_unit is not None
        insert = (
            "INSERT INTO notifications (college_id, id, user_id, kind, unit_id, day, payload, "
            "created_at) VALUES (%s, %s, %s, 'assigned', %s, current_date, '{}', now())"
        )
        for user, unit in ((a.college.teacher.id, b_unit[0]), (b.college.teacher.id, a_unit[0])):
            with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
                conn.execute(insert, (b.id, uuid4(), user, unit))


def test_b_neither_reads_nor_changes_the_new_columns_of_a(
    test_database: TestDatabase, world: World
) -> None:
    a, b = world.a.id, world.b.id
    with test_database.owner() as conn:
        conn.execute(
            "UPDATE users SET employee_id = 'A-1', department = 'Physics' WHERE college_id = %s",
            (a,),
        )
        conn.execute(
            "UPDATE delegation_requests SET suggested_to = NULL WHERE college_id = %s", (a,)
        )
        before = conn.execute(
            "SELECT count(*) FROM users WHERE college_id = %s AND employee_id = 'A-1'", (a,)
        ).fetchone()
        assert before is not None and int(str(before[0])) > 0
    reads = [
        "SELECT count(*) FROM users WHERE employee_id IS NOT NULL OR department IS NOT NULL",
        "SELECT count(*) FROM users WHERE college_id = %(a)s",
        "SELECT count(*) FROM exam_blueprints WHERE college_id = %(a)s",
        "SELECT count(*) FROM exam_blueprints WHERE kind IN ('paper', 'schema') AND college_id <> "
        "%(b)s",
        "SELECT count(*) FROM delegation_requests WHERE college_id = %(a)s",
        "SELECT count(*) FROM notifications WHERE college_id = %(a)s",
    ]
    writes = [
        "UPDATE users SET employee_id = 'B-1', department = 'B' WHERE college_id = %(a)s",
        "UPDATE delegation_requests SET suggested_to = requested_by WHERE college_id = %(a)s",
        "UPDATE notifications SET read_at = now() WHERE college_id = %(a)s",
    ]
    with test_database.app(b) as conn:
        for query in reads:
            assert conn.execute(query, {"a": a, "b": b}).fetchone() == (0,), query
        for query in writes:
            with conn.transaction():
                assert conn.execute(query, {"a": a}).rowcount == 0, query
        # Blueprint versions are append-only for the app role: no UPDATE at all.
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("UPDATE exam_blueprints SET descriptor = 'x' WHERE college_id = %s", (a,))
    with test_database.owner() as conn:
        after = conn.execute(
            "SELECT count(*) FROM users WHERE college_id = %s AND employee_id = 'A-1'", (a,)
        ).fetchone()
        assert after == before


def test_a_new_paper_version_waits_for_an_upload_in_flight(session: Opener, world: World) -> None:
    """F20 (D245): an upload holds the paper shared while it pins a version; a save holds it
    exclusively before reading the newest version. Uploads do not wait for each other."""
    a = world.a.id
    paper = world.blueprint.id
    with session(a) as upload:
        upload.content.hold_blueprint(a, paper, exclusive=False)
        with session(a) as other_upload:  # shared + shared: no wait
            other_upload.conn.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
            other_upload.content.hold_blueprint(a, paper, exclusive=False)
        with pytest.raises(DBAPIError) as waited, session(a) as save:
            save.conn.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
            save.content.hold_blueprint(a, paper, exclusive=True)
        assert isinstance(waited.value.orig, errors.LockNotAvailable)
    with session(a) as save:
        save.content.hold_blueprint(a, paper, exclusive=True)
        with pytest.raises(DBAPIError) as waited, session(a) as late_upload:
            late_upload.conn.exec_driver_sql("SET LOCAL lock_timeout = '200ms'")
            late_upload.content.hold_blueprint(a, paper, exclusive=False)
        assert isinstance(waited.value.orig, errors.LockNotAvailable)
    with session(a) as later:  # released at commit
        later.content.hold_blueprint(a, paper, exclusive=True)
