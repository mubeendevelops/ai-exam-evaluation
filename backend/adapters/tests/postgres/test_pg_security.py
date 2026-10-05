"""Row-level security and the append-only audit log, checked with raw SQL as the application
role: what a buggy or crafted query could do, not just what the repositories do."""

import json
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors, sql

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.testing import TestDatabase, World

pytestmark = pytest.mark.integration

COLLEGE_TABLES = [t.name for t in m.COLLEGE_TABLES]
GLOBAL_TABLES = [t.name for t in m.GLOBAL_TABLES]


def _key(table: str) -> str:
    return "id" if table == "colleges" else "college_id"


def _refused(conn: psycopg.Connection[tuple[object, ...]], query: sql.SQL | sql.Composed) -> bool:
    """True if the statement fails with insufficient_privilege (RLS or a missing grant)."""
    try:
        with conn.transaction():  # savepoint: keep the outer transaction usable
            conn.execute(query)
    except errors.InsufficientPrivilege:
        return True
    return False


def test_app_role_cannot_bypass_rls(test_database: TestDatabase) -> None:
    with test_database.owner() as conn:
        row = conn.execute(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname = 'tarn_app'"
        ).fetchone()
        assert row == (False, False, False, False)
        owned = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
            "WHERE r.rolname = 'tarn_app'"
        ).fetchone()
        assert owned == (0,)
        # Every table in the schema has RLS enabled and forced, so a table added later
        # without it fails here.
        tables = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' "
            "AND relname <> 'alembic_version'"
        ).fetchall()
    # ``college_directory`` is the one table that is neither: id and name of every college.
    assert {name for name, *_ in tables} == (
        set(COLLEGE_TABLES) | set(GLOBAL_TABLES) | {"college_directory"}
    )
    assert all(enabled and forced for _, enabled, forced in tables)


def test_every_college_table_has_rows_in_both_colleges(
    test_database: TestDatabase, world: World
) -> None:
    """Precondition of the crafted-query tests: nothing passes by being empty."""
    with test_database.owner() as conn:
        for table in COLLEGE_TABLES:
            for college in (world.a.id, world.b.id):
                query = sql.SQL("SELECT count(*) FROM {} WHERE {} = %s").format(
                    sql.Identifier(table), sql.Identifier(_key(table))
                )
                count = conn.execute(query, (college,)).fetchone()
                assert count is not None and count != (0,), (table, college)


def test_crafted_reads_see_only_own_college(test_database: TestDatabase, world: World) -> None:
    a, b = world.a.id, world.b.id
    with test_database.app(a) as conn:
        for table in COLLEGE_TABLES:
            t, k = sql.Identifier(table), sql.Identifier(_key(table))
            foreign = conn.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE {} = %s").format(t, k), (b,)
            ).fetchone()
            assert foreign == (0,), table
            seen = conn.execute(sql.SQL("SELECT DISTINCT {} FROM {}").format(k, t)).fetchall()
            assert seen == [(a,)], table


def test_no_college_set_sees_nothing(test_database: TestDatabase, world: World) -> None:
    with test_database.app(None) as conn:
        for table in COLLEGE_TABLES:
            count = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            ).fetchone()
            assert count == (0,), table


def test_crafted_writes_cannot_reach_another_college(
    test_database: TestDatabase, world: World
) -> None:
    a, b = world.a.id, world.b.id
    with test_database.owner() as owner:
        samples = {
            table: owner.execute(
                sql.SQL("SELECT row_to_json(x)::text FROM {} x WHERE {} = %s LIMIT 1").format(
                    sql.Identifier(table), sql.Identifier(_key(table))
                ),
                (b,),
            ).fetchone()
            for table in COLLEGE_TABLES
        }
    with test_database.app(a) as conn:
        for table in COLLEGE_TABLES:
            t, k = sql.Identifier(table), sql.Identifier(_key(table))
            # Insert a copy of one of B's rows (fresh id): refused by the policy's WITH CHECK.
            sample = samples[table]
            assert sample is not None
            columns = m.metadata.tables[table].c
            overrides: dict[str, object] = (
                {"id": str(uuid4())} if "id" in columns else {"ordinal": 999}
            )
            if "seq" in columns:
                overrides["seq"] = 999_999_999
            insert = sql.SQL(
                "INSERT INTO {t} OVERRIDING SYSTEM VALUE SELECT (r).* FROM (SELECT "
                "json_populate_record(NULL::{t}, ({row}::jsonb || {extra}::jsonb)::json) AS r) s"
            ).format(t=t, row=sql.Literal(sample[0]), extra=sql.Literal(json.dumps(overrides)))
            assert _refused(conn, insert), f"insert into {table}"

            # Update or delete B's rows: invisible, so nothing happens (or no grant at all).
            for stmt in (
                sql.SQL("UPDATE {} SET {} = {} WHERE {} = %s").format(t, k, k, k),
                sql.SQL("DELETE FROM {} WHERE {} = %s").format(t, k),
            ):
                try:
                    with conn.transaction():
                        cur = conn.execute(stmt, (b,))
                        assert cur.rowcount == 0, f"{stmt.as_string(conn)} on {table}"
                except errors.InsufficientPrivilege:
                    pass

            # Move one of A's rows into B: refused.
            move = sql.SQL("UPDATE {} SET {} = {} WHERE {} = {}").format(
                t, k, sql.Literal(str(b)), k, sql.Literal(str(a))
            )
            assert _refused(conn, move), f"move {table} row to B"


def test_rls_cannot_be_switched_off(test_database: TestDatabase, world: World) -> None:
    with test_database.app(world.a.id) as conn:
        conn.execute("SET LOCAL row_security = off")
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT count(*) FROM booklets")
    with test_database.app(world.a.id) as conn:
        assert _refused(conn, sql.SQL("SET ROLE tarn"))
        assert _refused(conn, sql.SQL("ALTER TABLE booklets DISABLE ROW LEVEL SECURITY"))


def test_global_content_is_read_by_all_written_by_owner(
    test_database: TestDatabase, world: World
) -> None:
    with test_database.owner() as owner:
        row = owner.execute(
            "SELECT question_id FROM question_versions WHERE created_by = %s LIMIT 1",
            (world.a.college.teacher.id,),
        ).fetchone()
    assert row is not None
    qid = row[0]
    with test_database.app(world.b.id) as conn:
        # B reads A's questions and blueprint ...
        count = conn.execute(
            "SELECT count(*) FROM exam_blueprints WHERE id = %s", (world.blueprint.id,)
        ).fetchone()
        assert count == (1,)
        # ... but cannot version, overwrite or delete them, nor claim content for A.
        assert _refused(
            conn,
            sql.SQL(
                "INSERT INTO question_versions (question_id, version, text, max_marks, "
                "created_by) VALUES ({}, 2, 'hijacked', 2, {})"
            ).format(sql.Literal(str(qid)), sql.Literal(str(world.b.college.teacher.id))),
        )
        assert _refused(
            conn,
            sql.SQL(
                "INSERT INTO subjects (id, version, code, name, owning_college_id, created_by) "
                "VALUES ({}, 1, 'X', 'X', {}, {})"
            ).format(
                sql.Literal(str(uuid4())),
                sql.Literal(str(world.a.id)),
                sql.Literal(str(world.b.college.teacher.id)),
            ),
        )
        for table in GLOBAL_TABLES:
            t = sql.Identifier(table)
            column = sql.Identifier("version" if table == "scoring_calibrations" else "created_by")
            assert _refused(conn, sql.SQL("UPDATE {} SET {} = {}").format(t, column, column))
            assert _refused(conn, sql.SQL("DELETE FROM {}").format(t))
        # Scoring calibrations are Tarn-operator content: colleges read them, never write.
        conn.execute("SELECT count(*) FROM scoring_calibrations")
        assert _refused(
            conn,
            sql.SQL(
                "INSERT INTO scoring_calibrations (id, version, embedder_name, params) "
                "VALUES ({}, 1, 'all-MiniLM-L6-v2', '{{}}')"
            ).format(sql.Literal(str(uuid4()))),
        )


def test_audit_is_append_only(test_database: TestDatabase, world: World) -> None:
    with test_database.app(world.a.id) as conn:
        for table in ("audit_events", "deletion_records"):
            t = sql.Identifier(table)
            assert _refused(conn, sql.SQL("UPDATE {} SET actor_id = actor_id").format(t))
            assert _refused(conn, sql.SQL("DELETE FROM {}").format(t))
            assert _refused(conn, sql.SQL("TRUNCATE {}").format(t))
        # Redaction only for a booklet that is gone and has a deletion record.
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute("SELECT tarn_redact_booklet_audit(%s)", (world.a.booklet.id,))
        # An INSERT is fine.
        conn.execute(
            "INSERT INTO audit_events (id, college_id, actor_id, at, action) "
            "VALUES (%s, %s, %s, now(), 'test.event')",
            (uuid4(), world.a.id, world.a.college.teacher.id),
        )
    # Even the owner cannot rewrite or remove an event; only redaction is let through.
    with test_database.owner() as owner:
        for stmt in (
            "UPDATE audit_events SET action = 'forged'",
            "DELETE FROM audit_events",
            "TRUNCATE audit_events",
            "DELETE FROM deletion_records",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                owner.execute(stmt)
