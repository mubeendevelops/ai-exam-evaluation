"""P4 on the application database: roster import and search through RLS-bound sessions,
class/section round trip, and audit events without an acting user."""

import pytest
from sqlalchemy import select

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.testing import Opener, World
from tarn_core.domain.audit import AuditAction
from tarn_core.services.roster import RosterService

pytestmark = pytest.mark.integration

CSV = (
    "name,usn,class/section\n"
    "Asha 100% Rao,1AB21CS101,CSE-A\n"
    "Ravi_Kumar,1AB21CS102,CSE-B\n"
    "Meena Iyer,1AB21EC103,\n"
)


def test_roster_import_search_and_isolation(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    with session(a.id) as s:
        roster = RosterService(students=s.students, users=s.users, runtime=s.runtime)
        report = roster.import_csv(a.id, a.college.teacher.id, CSV)
        assert report.imported and report.created == 3
        again = roster.import_csv(a.id, a.college.teacher.id, CSV.replace("CSE-B", "CSE-C"))
        assert (again.created, again.updated, again.unchanged) == (0, 1, 2)
        found = roster.search(a.id, "1ab21cs1", 10)
        assert [x.usn for x in found] == ["1AB21CS101", "1AB21CS102"]
        assert found[1].class_section == "CSE-C"
        # LIKE wildcards in the query are literal.
        assert [x.name for x in roster.search(a.id, "100%", 10)] == ["Asha 100% Rao"]
        assert [x.name for x in roster.search(a.id, "_", 10)] == ["Ravi_Kumar"]
        assert roster.search(b.id, "", 10) == []  # B through A's session: RLS says nothing
    with session(b.id) as s:
        roster = RosterService(students=s.students, users=s.users, runtime=s.runtime)
        assert [x.usn for x in roster.search(b.id, "1AB21", 10)] == []


def test_audit_event_without_actor_is_stored(session: Opener, world: World) -> None:
    with session(world.a.id) as s:
        s.runtime.record(world.a.id, None, AuditAction.LOGIN_FAILED, after={"reason": "unknown"})
    with session(world.a.id) as s:
        t = m.audit_events
        rows = s.conn.execute(
            select(t.c.actor_id, t.c.action).where(t.c.action == "auth.login_failed")
        ).all()
    assert [(r.actor_id, r.action) for r in rows] == [(None, "auth.login_failed")]
