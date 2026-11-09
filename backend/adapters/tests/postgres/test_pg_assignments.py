"""The SME's own figures on PostgreSQL (P31, D216): the same scenario as the in-memory adapter,
computed by one SQL aggregate as ``tarn_app`` under row-level security; another SME's units
and another college's rows never enter a figure."""

import pytest

from tarn_adapters.postgres.testing import Opener, new_college
from tarn_core.domain.assignments import AssignmentCounts
from tarn_core.testing.assignments_contract import build, check

pytestmark = pytest.mark.integration


def test_the_figures_on_postgres_match_the_hand_counts(session: Opener) -> None:
    a = new_college(session, "ASA")
    b = new_college(session, "ASB")
    with session(a.id) as s:
        scenario = build(s, a)
        check(s.overview, scenario)
    # The same scenario in the other college: its figures are its own, whatever A holds.
    with session(b.id) as s:
        other = build(s, b)
        check(s.overview, other)
        # B's session asks for A's SME in A's college: row-level security leaves nothing.
        assert s.overview.assignment_counts(a.id, scenario.first.id, scenario.year) == (
            AssignmentCounts()
        )
    with session(a.id) as s:
        check(s.overview, scenario)
