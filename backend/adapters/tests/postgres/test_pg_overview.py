"""The admin's figures on PostgreSQL (P29, migration 0017): the same scenario as the in-memory
adapter, computed by SQL aggregates as ``tarn_app`` under row-level security; another
college's rows never enter a figure; the queue depth counts the college's jobs only."""

import pytest

from tarn_adapters.postgres.testing import Opener, TestDatabase, new_college
from tarn_core.domain.overview import QueueDepth, SmeCounts
from tarn_core.testing.builders import make_services
from tarn_core.testing.overview_contract import (
    build,
    check_activity,
    check_after_transfer,
    check_before,
)

pytestmark = pytest.mark.integration


def test_the_figures_on_postgres_match_the_hand_counts(session: Opener) -> None:
    a = new_college(session, "OVA")
    b = new_college(session, "OVB")
    with session(a.id) as s:
        scenario = build(s, make_services(s), a, session.clock)
        check_before(s.overview, scenario, s)
        make_services(s).transfers.transfer(
            a.id, a.admin.id, scenario.units["u1"], owner_id=scenario.second.id, reason="Capacity"
        )
        check_after_transfer(s.overview, scenario, s)
        check_activity(s.overview, scenario)
    # The same scenario in the other college: its figures are its own, whatever A holds.
    with session(b.id) as s:
        other = build(s, make_services(s), b, session.clock)
        check_before(s.overview, other, s)
        assert s.overview.sme_counts(b.id) == SmeCounts(active=1, idle=1, suspended=1)


def test_another_colleges_id_shows_nothing(session: Opener) -> None:
    a = new_college(session, "OVC")
    b = new_college(session, "OVD")
    with session(a.id) as s:
        build(s, make_services(s), a, session.clock)
    with session(b.id) as s:
        today = session.clock.now().date()
        # B's session asks for A's college id: row-level security leaves nothing.
        assert s.overview.sme_counts(a.id) == SmeCounts()
        assert s.overview.unit_counts(a.id, today).open == 0
        assert s.overview.booklet_stages(a.id).total == 0
        assert list(s.overview.sme_progress(a.id, today)) == []
        assert list(s.overview.unit_progress(a.id, today)) == []
        assert s.overview.owners_of_open_units(a.id) == frozenset()
        assert list(s.overview.activity(a.id, 10)) == []
        assert s.overview.queue_depth(a.id) == QueueDepth()


def test_the_queue_depth_counts_the_colleges_own_jobs(
    session: Opener, test_database: TestDatabase
) -> None:
    a = new_college(session, "OVE")
    b = new_college(session, "OVF")
    with session(a.id) as s:
        s.jobs.enqueue(a.id, "booklet.prepare", {})
        s.jobs.enqueue(a.id, "booklet.read", {})
    with session(b.id) as s:
        s.jobs.enqueue(b.id, "booklet.prepare", {})
    with session(a.id) as s:
        assert s.overview.queue_depth(a.id) == QueueDepth(queued=2, running=0)
    # One of A's jobs is running (set by the owner: a claim would depend on the other tests'
    # leases in this shared database).
    with test_database.owner() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'running', locked_until = now() + interval '1 minute' "
            "WHERE id = (SELECT id FROM jobs WHERE college_id = %s LIMIT 1)",
            (a.id,),
        )
    try:
        with session(a.id) as s:
            assert s.overview.queue_depth(a.id) == QueueDepth(queued=1, running=1)
        with session(b.id) as s:
            assert s.overview.queue_depth(b.id) == QueueDepth(queued=1, running=0)
    finally:  # no live lease may outlive the test: other tests claim jobs in this database
        with test_database.owner() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'succeeded', locked_until = NULL "
                "WHERE college_id IN (%s, %s)",
                (a.id, b.id),
            )


def test_the_aggregates_use_the_new_indexes(session: Opener, test_database: TestDatabase) -> None:
    with test_database.owner() as conn:
        names = {
            r[0]
            for r in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename IN ('audit_events', 'jobs')"
            ).fetchall()
        }
    assert {"audit_events_actor_at", "audit_events_action_at", "jobs_college_status"} <= names
