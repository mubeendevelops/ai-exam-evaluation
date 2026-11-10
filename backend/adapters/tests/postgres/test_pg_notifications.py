"""Notifications on PostgreSQL (P37, migration 0022): the same contract as the in-memory adapter
(events, inbox, the due key, the sweep) as ``tarn_app``; row-level security keeps every college's
notices its own; the app role may set ``read_at`` and nothing else, and never deletes; the
worker queues one ``notify.due`` job per college and runs it, a rerun adding nothing."""

import psycopg
import pytest
from psycopg import errors

from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.testing import Opener, TestDatabase, new_college
from tarn_core.domain.notifications import NotificationKind
from tarn_core.ids import CollegeId
from tarn_core.testing.notifications_contract import (
    build,
    check_due_key,
    check_events,
    check_inbox,
    check_isolated,
    check_sweep,
)

pytestmark = pytest.mark.integration


def test_the_notifications_contract_on_postgres(
    session: Opener, test_database: TestDatabase
) -> None:
    a = new_college(session, "NTA")
    b = new_college(session, "NTB")
    with session(a.id) as s:
        scenario = build(s, a, session.clock)
        unit = check_events(s, s.notifications, scenario)
        check_inbox(s, s.notifications, scenario)
        check_due_key(s, s.notifications, scenario, unit)
    with session(b.id) as s:
        check_isolated(s.notifications, b.id, scenario)
        # B's session naming A's college: row-level security leaves nothing.
        check_isolated(s.notifications, a.id, scenario)

    with test_database.app(b.id) as conn:
        assert conn.execute("SELECT count(*) FROM notifications").fetchone() == (0,)
        # B cannot write a notice into A's college.
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "INSERT INTO notifications (college_id, id, user_id, kind, unit_id, day, "
                "payload, created_at) VALUES (%s, gen_random_uuid(), %s, 'assigned', %s, "
                "current_date, '{}', now())",
                (a.id, a.teacher.id, unit),
            )
    with test_database.app(a.id) as conn:
        counted = conn.execute("SELECT count(*) FROM notifications").fetchone()
        assert counted is not None and int(str(counted[0])) > 0
        for statement in (
            "UPDATE notifications SET kind = 'overdue'",
            "UPDATE notifications SET payload = '{}'",
            "UPDATE notifications SET user_id = college_id",
            "DELETE FROM notifications",
        ):
            assert _refused(conn, statement), statement
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute("UPDATE notifications SET read_at = created_at - interval '1 day'")
        with pytest.raises(errors.CheckViolation), conn.transaction():
            conn.execute(
                "INSERT INTO notifications (college_id, id, user_id, kind, unit_id, day, "
                "payload, created_at) VALUES (%s, gen_random_uuid(), %s, 'gossip', %s, "
                "current_date, '{}', now())",
                (a.id, a.teacher.id, unit),
            )
        # Everything stays: the failed statements changed nothing.
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM notifications")}
        assert NotificationKind.ASSIGNED.value in kinds


def _refused(conn: psycopg.Connection[tuple[object, ...]], statement: str) -> bool:
    try:
        with conn.transaction():
            conn.execute(statement.encode())
    except errors.InsufficientPrivilege:
        return True
    return False


def test_the_sweep_contract_on_postgres(session: Opener) -> None:
    a = new_college(session, "NTS")
    with session(a.id) as s:
        scenario = build(s, a, session.clock)
        check_sweep(s, s.notifications, scenario)


def test_the_table_is_forced_under_row_security(test_database: TestDatabase) -> None:
    with test_database.owner() as conn:
        row = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE relname = 'notifications'"
        ).fetchone()
    assert row == (True, True)


def test_the_worker_queues_and_runs_the_sweep_per_college(
    fresh: tuple[TestDatabase, PostgresDatabase],
) -> None:
    from datetime import timedelta

    from tarn_core.services.pipeline import QualityPolicy
    from tarn_core.testing import FakePageCleaner, FakePageSplitter, MemoryMailer
    from tarn_core.testing.notifications_contract import due_unit
    from tarn_worker.booklets import BookletJobRunner
    from tarn_worker.reminders import queue_reminders

    test_db, db = fresh
    opener = Opener(db)
    a = new_college(opener, "NWA")
    b = new_college(opener, "NWB")
    today = opener.clock.now().date()
    for college, code in ((a, "A"), (b, "B")):
        with opener(college.id) as s:
            scenario = build(s, college, opener.clock)
            due_unit(s, scenario, college.teacher, today + timedelta(days=2), f"W{code}")
            due_unit(s, scenario, college.teacher, today - timedelta(days=1), f"O{code}")
    mail = MemoryMailer()
    runner = BookletJobRunner(
        db=db,
        blobs=opener.blobs,
        splitter=FakePageSplitter(),
        cleaner=FakePageCleaner(),
        clock=opener.clock,
        ids=opener.ids,
        policy=QualityPolicy(),
        max_pages=10,
        mailer=mail,
    )

    def queue() -> int:
        return queue_reminders(db, clock=opener.clock, ids=opener.ids, blobs=opener.blobs)

    def drain() -> int:
        ran = 0
        while runner.run_one():
            ran += 1
        return ran

    assert queue() == 2
    assert queue() == 2  # the second call finds today's jobs waiting: no new ones
    with test_db.owner() as conn:
        jobs = conn.execute(
            "SELECT college_id, count(*) FROM jobs WHERE kind = 'notify.due' GROUP BY 1"
        ).fetchall()
    assert {(c, n) for c, n in jobs} == {(a.id, 1), (b.id, 1)}
    assert drain() == 2
    assert len(mail.sent) == 2  # one "due in 2 days" per college

    def notices(college_id: CollegeId) -> list[str]:
        with opener(college_id) as s:
            return sorted(n.kind.value for n in s.notifications.all(college_id))

    after_first = notices(a.id)
    # The same day again (the jobs are done, so new ones are queued): nothing new, no mail.
    queue()
    assert drain() == 2
    assert notices(a.id) == after_first
    assert len(mail.sent) == 2
    with test_db.owner() as conn:
        states = conn.execute(
            "SELECT status, count(*) FROM jobs WHERE kind = 'notify.due' GROUP BY 1"
        ).fetchall()
    assert {(str(state), int(str(n))) for state, n in states} == {("succeeded", 4)}
