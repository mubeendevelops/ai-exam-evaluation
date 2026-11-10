"""The SME's global search on PostgreSQL (P37, D228, D243): the in-memory scope contract as
``tarn_app`` (students only of the SME's own units, content only of their subjects, LIKE
wildcards as text), and a second college built the same way finding nothing of the first.
Mutation-checked: dropping the unit filter from ``find_students`` fails it."""

import pytest

from tarn_adapters.postgres.testing import Opener, new_college
from tarn_core.testing.search_contract import build, check, check_isolated

pytestmark = pytest.mark.integration


def test_the_search_contract_on_postgres(session: Opener) -> None:
    a = new_college(session, "SRA")
    b = new_college(session, "SRB")
    with session(a.id) as s:
        scenario_a = build(s, a)
        check(scenario_a)
    with session(b.id) as s:
        scenario_b = build(s, b)
        check(scenario_b)
        check_isolated(scenario_b, scenario_a)
        # B's session asking with A's unit ids: row-level security leaves nothing.
        assert (
            s.booklets.find_students(
                a.id, frozenset({scenario_a.mine, scenario_a.theirs}), "mira", limit=5
            )
            == []
        )
