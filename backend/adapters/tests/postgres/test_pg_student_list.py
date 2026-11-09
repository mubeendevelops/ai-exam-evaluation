"""The SME's student list on PostgreSQL (P32, D217): the same scenario as the in-memory
adapter, filtered, paged and tallied by one query (and one aggregate for the tallies) as
``tarn_app`` under row-level security; another college's booklets never appear."""

import pytest

from tarn_adapters.postgres.testing import Opener, new_college
from tarn_core.testing.student_list_contract import build, check, check_isolated

pytestmark = pytest.mark.integration


def test_the_student_list_on_postgres_matches_the_contract(session: Opener) -> None:
    a = new_college(session, "SLA")
    b = new_college(session, "SLB")
    with session(a.id) as s:
        scenario = build(s, a)
        check(s.booklets, scenario)
    with session(b.id) as s:
        # B's session asks for A's unit in A's college: row-level security leaves nothing.
        check_isolated(s.booklets, a.id, scenario)
        check_isolated(s.booklets, b.id, scenario)
