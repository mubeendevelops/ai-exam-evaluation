"""P30 item 1: row-level security and composite foreign keys on the Stage F tables (subject
grants, units, delegation requests, transfers, submissions, frozen records), checked with raw
SQL as the application role. ``test_pg_security`` already sweeps every college table for
crafted reads and writes; this adds the tables by name (so dropping one from
``COLLEGE_TABLES`` fails here) and tries each composite foreign key with another college's
parent row."""

import json
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors, sql

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.testing import TestDatabase, World

pytestmark = pytest.mark.integration

STAGE_F_TABLES = (
    "sme_subjects",
    "units",
    "delegation_requests",
    "unit_transfers",
    "unit_submissions",
    "unmarked_booklets",
    "frozen_units",
    "frozen_booklets",
    "frozen_answers",
)

Conn = psycopg.Connection[tuple[object, ...]]


def _foreign_keys(conn: Conn, table: str) -> list[tuple[str, list[str], str, list[str]]]:
    """``(name, columns, referenced table, referenced columns)`` of each foreign key of
    ``table`` that includes ``college_id`` and at least one other column."""
    rows = conn.execute(
        """
        SELECT c.conname, c.confrelid::regclass::text,
               array(SELECT a.attname FROM unnest(c.conkey) WITH ORDINALITY k(n, i)
                     JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n
                     ORDER BY k.i),
               array(SELECT a.attname FROM unnest(c.confkey) WITH ORDINALITY k(n, i)
                     JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n
                     ORDER BY k.i)
        FROM pg_constraint c
        WHERE c.contype = 'f' AND c.conrelid = %s::regclass
        """,
        (table,),
    ).fetchall()
    return [
        (str(name), list(cols), str(ref), list(ref_cols))  # type: ignore[call-overload]
        for name, ref, cols, ref_cols in rows
        if "college_id" in cols and len(cols) > 1  # type: ignore[operator, arg-type]
    ]


def test_the_stage_f_tables_are_college_tables_with_rls_forced(
    test_database: TestDatabase,
) -> None:
    names = {t.name for t in m.COLLEGE_TABLES}
    assert set(STAGE_F_TABLES) <= names
    with test_database.owner() as conn:
        rows = conn.execute(
            "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relname = ANY(%s)",
            (list(STAGE_F_TABLES),),
        ).fetchall()
    assert sorted(rows) == sorted((t, True, True) for t in STAGE_F_TABLES)


def test_each_stage_f_table_has_a_composite_key_to_its_college_parents(
    test_database: TestDatabase,
) -> None:
    """Every reference from a Stage F table to college data names the college too."""
    with test_database.owner() as conn:
        for table in STAGE_F_TABLES:
            assert _foreign_keys(conn, table), table
            singles = conn.execute(
                """
                SELECT c.conname FROM pg_constraint c
                WHERE c.contype = 'f' AND c.conrelid = %s::regclass
                  AND c.confrelid <> 'colleges'::regclass
                  AND NOT EXISTS (
                      SELECT 1 FROM unnest(c.conkey) k(n)
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n
                      WHERE a.attname = 'college_id')
                """,
                (table,),
            ).fetchall()
            assert singles == [], (table, singles)


def test_a_stage_f_row_cannot_point_at_another_colleges_parent(
    test_database: TestDatabase, world: World
) -> None:
    """A copy of one of B's rows, made by B, whose reference names A's row (unit, user,
    subject, submission, booklet, frozen unit): refused by the composite foreign key."""
    a, b = world.a.id, world.b.id
    tried = 0
    with test_database.owner() as owner:
        plans = []
        for table in STAGE_F_TABLES:
            for name, cols, ref, ref_cols in _foreign_keys(owner, table):
                # One of B's rows that uses this reference (an optional one may be empty).
                filled = sql.SQL(" AND ").join(
                    sql.SQL("{} IS NOT NULL").format(sql.Identifier(c)) for c in cols
                )
                found = owner.execute(
                    sql.SQL(
                        "SELECT row_to_json(x)::text FROM {} x WHERE college_id = %s AND {} LIMIT 1"
                    ).format(sql.Identifier(table), filled),
                    (b,),
                ).fetchone()
                if found is None:
                    continue
                parent = owner.execute(
                    sql.SQL(
                        "SELECT row_to_json(x)::text FROM {} x WHERE college_id = %s LIMIT 1"
                    ).format(sql.Identifier(ref)),
                    (a,),
                ).fetchone()
                assert parent is not None, (table, ref)
                values = json.loads(str(parent[0]))
                overrides: dict[str, object] = {
                    col: values[ref_col]
                    for col, ref_col in zip(cols, ref_cols, strict=True)
                    if col != "college_id"
                }
                columns = m.metadata.tables[table].c
                if "id" in columns and "id" not in overrides:
                    overrides["id"] = str(uuid4())
                if "seq" in columns:
                    overrides["seq"] = 999_999_999
                plans.append((table, name, str(found[0]), overrides))
        assert {p[0] for p in plans} == set(STAGE_F_TABLES)
    collided = []
    with test_database.app(b) as conn:
        for table, name, sample, overrides in plans:
            t = sql.Identifier(table)
            insert = sql.SQL(
                "INSERT INTO {t} OVERRIDING SYSTEM VALUE SELECT (r).* FROM (SELECT "
                "json_populate_record(NULL::{t}, ({row}::jsonb || {extra}::jsonb)::json) AS r) s"
            ).format(t=t, row=sql.Literal(sample), extra=sql.Literal(json.dumps(overrides)))
            try:
                with conn.transaction():
                    conn.execute(insert)
            except errors.ForeignKeyViolation as refused:
                assert name in str(refused), (table, name, str(refused))
            except errors.UniqueViolation:
                # The copy keeps B's key (the key is the reference itself, e.g. a grant's
                # granted_by): tried below by moving B's row instead.
                collided.append((table, name, sample, overrides))
            else:
                raise AssertionError(f"{table}: {name} let B's row name A's parent")
            tried += 1
    for table, name, sample, overrides in collided:
        # The owner moves B's row onto A's parent in a transaction that is rolled back: the
        # foreign key, not row-level security, is what refuses it.
        key = json.loads(sample)
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(col), sql.Literal(value))
            for col, value in overrides.items()
            if col not in ("id", "seq")
        )
        where = sql.SQL(" AND ").join(
            sql.SQL("{} = {}").format(sql.Identifier(col), sql.Literal(value))
            for col, value in key.items()
            if value is not None and not isinstance(value, dict | list)
        )
        with test_database.owner() as owner, pytest.raises(errors.ForeignKeyViolation) as fk:
            owner.execute(
                sql.SQL("UPDATE {} SET {} WHERE {}").format(
                    sql.Identifier(table), assignments, where
                )
            )
        assert name in str(fk.value), (table, name)
    assert tried >= len(STAGE_F_TABLES)
