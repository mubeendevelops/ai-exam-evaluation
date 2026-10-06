"""Deleting a booklet removes its images, text and marks and leaves only a content-free
deletion record; earlier audit events keep who/when/what but lose their values (D14, D32)."""

from uuid import UUID

import pytest
from psycopg import sql

from tarn_adapters.postgres.testing import Opener, TestDatabase, World
from tarn_core.domain.audit import AuditAction
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration

# (table, column, which id set) for everything that hangs off a booklet.
DEPENDENTS = (
    ("booklets", "id", "booklet"),
    ("pages", "booklet_id", "booklet"),
    ("segments", "booklet_id", "booklet"),
    ("answers", "booklet_id", "booklet"),
    ("diagram_graphs", "booklet_id", "booklet"),
    ("result_sheets", "booklet_id", "booklet"),
    ("booklet_locks", "booklet_id", "booklet"),
    ("amendments", "booklet_id", "booklet"),
    ("region_edits", "booklet_id", "booklet"),
    ("regions", "page_id", "pages"),
    ("answer_scores", "answer_id", "answers"),
    ("reviews", "answer_id", "answers"),
    ("sentence_embeddings", "answer_id", "answers"),
)


def _counts(db: TestDatabase, ids: dict[str, list[UUID]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with db.owner() as conn:
        for table, column, key in DEPENDENTS:
            row = conn.execute(
                sql.SQL("SELECT count(*) FROM {} WHERE {} = ANY(%s)").format(
                    sql.Identifier(table), sql.Identifier(column)
                ),
                (ids[key],),
            ).fetchone()
            counts[table] = int(str(row[0])) if row else 0
        regions = conn.execute(
            "SELECT count(*) FROM line_readings r JOIN regions g ON g.id = r.region_id "
            "WHERE g.page_id = ANY(%s)",
            (ids["pages"],),
        ).fetchone()
        scores = conn.execute(
            "SELECT count(*) FROM criterion_scores c JOIN answer_scores s "
            "ON s.id = c.answer_score_id WHERE s.answer_id = ANY(%s)",
            (ids["answers"],),
        ).fetchone()
    counts["line_readings"] = int(str(regions[0])) if regions else 0
    counts["criterion_scores"] = int(str(scores[0])) if scores else 0
    return counts


def test_delete_leaves_only_the_deletion_record(
    test_database: TestDatabase, session: Opener, world: World
) -> None:
    a, b = world.a, world.b
    with session(a.id) as s:
        pages = s.booklets.pages(a.id, a.booklet.id)
        ids: dict[str, list[UUID]] = {
            "booklet": [a.booklet.id],
            "pages": [p.id for p in pages],
            "answers": [x.id for x in a.answers],
        }
    before = _counts(test_database, ids)
    assert all(n > 0 for n in before.values()), before  # everything existed

    with session(a.id) as s:
        make_services(s).booklets.delete(a.id, a.college.teacher.id, a.booklet.id)

    assert _counts(test_database, ids) == dict.fromkeys(before, 0)
    assert all(not session.blobs.exists(p.image) for p in pages)

    with test_database.owner() as conn:
        record = conn.execute(
            "SELECT college_id, actor_id, at FROM deletion_records WHERE booklet_id = %s",
            (a.booklet.id,),
        ).fetchall()
        assert record == [(a.id, a.college.teacher.id, session.clock.now())]
        events = conn.execute(
            "SELECT action, actor_id, before, after FROM audit_events "
            "WHERE booklet_id = %s ORDER BY seq",
            (a.booklet.id,),
        ).fetchall()
    actions = [e[0] for e in events]
    assert actions[0] == AuditAction.BOOKLET_REGISTERED
    assert actions.count(AuditAction.ANSWER_SCORED) == 3
    assert actions[-1] == AuditAction.BOOKLET_DELETED
    # Who and what happened stay; no values (marks, student id) survive.
    assert all(e[1] == a.college.teacher.id for e in events)
    assert all(e[2] is None and e[3] is None for e in events)

    # B's twin booklet, and its audit values, are untouched.
    with session(b.id) as s:
        assert s.booklets.get(b.id, b.booklet.id) == b.booklet
        assert len(s.booklets.answers(b.id, b.booklet.id)) == 3
        assert all(s.scores.scores(b.id, x.id) for x in b.answers)
    with test_database.owner() as conn:
        kept = conn.execute(
            "SELECT count(*) FROM audit_events WHERE booklet_id = %s AND after IS NOT NULL",
            (b.booklet.id,),
        ).fetchone()
    assert kept == (4,)  # registered + 3 scored
