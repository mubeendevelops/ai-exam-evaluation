"""The SME's global search (P37, D228, D243): the scope contract in memory, with a second
college built the same way."""

import pytest

from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import PermissionDeniedError
from tarn_core.ids import UserId
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college
from tarn_core.testing.search_contract import build, check, check_isolated


def test_the_search_contract_in_memory() -> None:
    mem = InMemory()
    a = build(mem, add_college(mem, "A"))
    b = build(mem, add_college(mem, "B"))
    check(a)
    check(b)
    check_isolated(b, a)
    check_isolated(a, b)


def test_a_governor_and_an_admin_cannot_search() -> None:
    mem = InMemory()
    s = build(mem, add_college(mem, "A"))
    governor = User(
        id=UserId(mem.ids.new()),
        college_id=s.cid,
        display_name="Governor",
        email="gov@example.test",
        role=Role.GOVERNOR,
    )
    mem.users.save(s.cid, governor)
    for who in (governor.id, s.college.admin.id):
        with pytest.raises(PermissionDeniedError):
            s.services.search.search(s.cid, who, "orbit")
