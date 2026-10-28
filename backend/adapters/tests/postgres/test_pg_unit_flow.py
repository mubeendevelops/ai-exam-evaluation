"""Migration 0016 in raw SQL as the application role (P28): the database's own refusals behind
the core's checks. A closed unit's booklets do not change; a frozen unit never changes; content
is deleted only through the two functions, which apply D166 and the session's college."""

from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from psycopg import errors, sql

from tarn_adapters.postgres.testing import TestDatabase, World

pytestmark = pytest.mark.integration

Conn = psycopg.Connection[tuple[object, ...]]


def _raises(conn: Conn, error: type[Exception], query: str | sql.Composed, *args: object) -> bool:
    try:
        with conn.transaction():
            conn.execute(query, args or None)
    except error:
        return True
    return False


def _state(db: TestDatabase, world: World, state: str) -> None:
    with db.owner() as owner:
        owner.execute(
            "UPDATE units SET state = %s, version = version + 1 WHERE college_id = %s",
            (state, world.a.id),
        )


def _count(conn: Conn, query: str | sql.Composed, *args: object) -> Any:
    row = conn.execute(query, args or None).fetchone()
    assert row is not None
    return row[0]


def test_the_app_role_deletes_content_only_through_the_functions(
    test_database: TestDatabase, world: World
) -> None:
    with test_database.app(world.a.id) as conn:
        for table in ("questions", "question_versions", "exam_blueprints", "key_files"):
            assert _raises(
                conn,
                errors.InsufficientPrivilege,
                sql.SQL("DELETE FROM {}").format(sql.Identifier(table)),
            ), table
        # Another college's paper is not there; an unknown id neither.
        assert _raises(
            conn, errors.NoDataFound, "SELECT tarn_delete_paper(%s)", world.blueprint_b.id
        )
        # A's own paper: an open unit uses it.
        assert _raises(
            conn, errors.RestrictViolation, "SELECT tarn_delete_paper(%s)", world.blueprint.id
        )
        question = world.blueprint.leaf("1")[1]
        assert _raises(conn, errors.RestrictViolation, "SELECT tarn_delete_question(%s)", question)
    with test_database.app(None) as conn:
        assert _raises(
            conn,
            errors.InsufficientPrivilege,
            "SELECT tarn_delete_paper(%s)",
            world.blueprint.id,
        )


def test_a_closed_units_booklets_do_not_change(test_database: TestDatabase, world: World) -> None:
    booklet, answer = world.a.booklet.id, world.a.answers[0].id
    writes: list[tuple[str, tuple[object, ...]]] = [
        ("UPDATE booklets SET version = version + 1 WHERE id = %s", (booklet,)),
        ("UPDATE answers SET version = version + 1 WHERE id = %s", (answer,)),
        ("UPDATE pages SET reading_order = reading_order WHERE booklet_id = %s", (booklet,)),
        ("DELETE FROM segments WHERE booklet_id = %s", (booklet,)),
        ("DELETE FROM result_sheets WHERE booklet_id = %s", (booklet,)),
        ("DELETE FROM booklets WHERE id = %s", (booklet,)),
    ]
    with test_database.app(world.a.id) as conn:  # open: allowed (rolled back)
        for query, args in writes[:2]:
            with conn.transaction():
                conn.execute(query, args)
    for state in ("submitted", "frozen"):
        _state(test_database, world, state)
        with test_database.app(world.a.id) as conn:
            for query, args in writes:
                assert _raises(conn, errors.CheckViolation, query, *args), (state, query)
    # A frozen unit itself never changes (not even by the owner role).
    with test_database.owner() as owner:
        assert _raises(
            owner,
            errors.CheckViolation,
            "UPDATE units SET due_date = due_date WHERE college_id = %s",
            world.a.id,
        )


def test_after_the_freeze_the_paper_and_its_questions_go(
    test_database: TestDatabase, world: World
) -> None:
    _state(test_database, world, "submitted")
    _state(test_database, world, "frozen")
    question = world.blueprint.leaf("1")[1]
    with test_database.app(world.a.id) as conn:
        # The paper still links the question.
        assert _raises(conn, errors.RestrictViolation, "SELECT tarn_delete_question(%s)", question)
        assert _count(conn, "SELECT tarn_delete_paper(%s)", world.blueprint.id) == 1
        rows = _count(conn, "SELECT tarn_delete_question(%s)", question)
        assert rows >= 4  # identity, version, rubric criterion, reference answer, key file, …
        for table, column in (
            ("questions", "id"),
            ("question_versions", "question_id"),
            ("key_files", "question_id"),
            ("reference_diagrams", "question_id"),
        ):
            assert (
                _count(
                    conn,
                    sql.SQL("SELECT count(*) FROM {} WHERE {} = %s").format(
                        sql.Identifier(table), sql.Identifier(column)
                    ),
                    question,
                )
                == 0
            )
        # The booklet, its scores and the frozen record stay.
        booklet = world.a.booklet.id
        assert _count(conn, "SELECT count(*) FROM booklets WHERE id = %s", booklet) == 1
        assert _count(conn, "SELECT count(*) FROM answer_scores") > 0
        assert _count(conn, "SELECT count(*) FROM frozen_booklets WHERE booklet_id = %s", booklet)
        conn.commit()


def test_unmarked_reasons_go_with_their_booklet(test_database: TestDatabase, world: World) -> None:
    booklet = world.a.booklet.id
    with test_database.app(world.a.id) as conn:
        query = "SELECT count(*) FROM unmarked_booklets WHERE booklet_id = %s"
        assert _count(conn, query, booklet) == 1
        conn.execute("DELETE FROM booklets WHERE id = %s", (booklet,))
        assert _count(conn, query, booklet) == 0


def test_a_booklet_or_unit_names_a_paper_of_its_own_college(
    test_database: TestDatabase, world: World
) -> None:
    """The foreign keys to the paper are gone (frozen booklets outlive it); the trigger still
    refuses another college's paper (D170)."""
    sets: list[tuple[str, Callable[[], tuple[object, ...]]]] = [
        (
            "UPDATE booklets SET blueprint_id = %s WHERE id = %s",
            lambda: (world.blueprint_b.id, world.a.booklet.id),
        ),
        (
            "UPDATE units SET paper_id = %s WHERE college_id = %s",
            lambda: (world.blueprint_b.id, world.a.id),
        ),
    ]
    with test_database.app(world.a.id) as conn:
        for query, args in sets:
            assert _raises(conn, errors.ForeignKeyViolation, query, *args()), query
