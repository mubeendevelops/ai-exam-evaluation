"""My Reports on PostgreSQL (P36, D239): the same scenario as the in-memory adapter, computed by
SQL aggregates as ``tarn_app`` under row-level security, with the quarter edges and the on-time
date in UTC; another college's rows and another SME's units never enter a figure."""

from dataclasses import replace

import pytest

from tarn_adapters.postgres.testing import Opener, TestDatabase, new_college
from tarn_core.testing.builders import make_services
from tarn_core.testing.reports_contract import build, check

pytestmark = pytest.mark.integration


def test_the_figures_on_postgres_match_the_hand_counts(session: Opener) -> None:
    a = new_college(session, "RPA")
    b = new_college(session, "RPB")
    with session(a.id) as s:
        scenario = build(s, make_services(s), a)
        check(s.overview, scenario)
    # The same scenario in the other college: its figures are its own, whatever A holds.
    with session(b.id) as s:
        other = build(s, make_services(s), b)
        check(s.overview, other)
        # B's session asks for A's SME in A's college: row-level security leaves nothing.
        found = s.overview.report_counts(a.id, scenario.first.id, scenario.year, scenario.quarters)
        assert (found.handed_in, found.open_units, found.booklets_pending) == (0, 0, 0)
        assert all(q.units_submitted == q.booklets_approved == 0 for q in found.quarters)
    with session(a.id) as s:
        check(s.overview, scenario)


def test_the_profile_fields_round_trip_stay_in_the_college_and_the_database_checks_them(
    session: Opener, test_database: TestDatabase
) -> None:
    a = new_college(session, "RPC")
    b = new_college(session, "RPD")
    with session(a.id) as s:
        assert s.users.get(a.id, a.teacher.id).employee_id is None  # nothing set before
        s.users.save(a.id, replace(a.teacher, employee_id="TKS-8904", department="Quantum Physics"))
    with session(a.id) as s:
        got = s.users.get(a.id, a.teacher.id)
        assert (got.employee_id, got.department) == ("TKS-8904", "Quantum Physics")
        s.users.save(a.id, replace(got, department=None))
    with session(a.id) as s:
        assert s.users.get(a.id, a.teacher.id).department is None
    # Another college's session sees none of A's rows (row-level security).
    with session(b.id) as s:
        assert [u.id for u in s.users.list(a.id)] == []
    # Whatever the application does, the database refuses a blank, padded or over-long value.
    update = {
        "employee_id": "UPDATE users SET employee_id = %s WHERE id = %s",
        "department": "UPDATE users SET department = %s WHERE id = %s",
    }
    for column, bad in (
        ("employee_id", ""),
        ("employee_id", " padded"),
        ("employee_id", "x" * 41),
        ("department", "  "),
        ("department", "d" * 81),
    ):
        with test_database.owner() as conn, pytest.raises(Exception, match="check"):
            conn.execute(update[column], (bad, a.teacher.id))
