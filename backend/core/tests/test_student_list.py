"""The SME's student list (P32, D217): row statuses, tallies, filters and pages."""

import pytest

from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.student_list import (
    ROW_STATUS_OF,
    AnswerTally,
    RowStatus,
    UnitRowQuery,
    UnitRows,
    statuses_of,
)
from tarn_core.errors import InvariantError
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college
from tarn_core.testing.student_list_contract import build, check, check_isolated


def test_every_booklet_status_has_a_row_status() -> None:
    assert set(ROW_STATUS_OF) == set(BookletStatus)
    assert set(ROW_STATUS_OF.values()) == set(RowStatus)
    assert ROW_STATUS_OF[BookletStatus.SCORED] is RowStatus.READY
    assert ROW_STATUS_OF[BookletStatus.AMENDMENT_IN_PROGRESS] is RowStatus.COMPLETED
    assert statuses_of(frozenset({RowStatus.FAILED})) == {BookletStatus.FAILED}
    assert len(statuses_of(frozenset({RowStatus.PROCESSING}))) == 6


def test_query_and_tally_invariants() -> None:
    with pytest.raises(InvariantError):
        AnswerTally(attempted=1, approved=2)
    with pytest.raises(InvariantError):
        UnitRowQuery(limit=0)
    with pytest.raises(InvariantError):
        UnitRowQuery(limit=101)
    with pytest.raises(InvariantError):
        UnitRowQuery(offset=-1)
    with pytest.raises(InvariantError):
        UnitRowQuery(text="x" * 81)
    with pytest.raises(InvariantError):
        UnitRows(booklets=[object()], matching=0)  # type: ignore[list-item]
    assert UnitRowQuery(text="  Ab C ").needle == "ab c"


def test_the_list_in_memory_and_another_college_sees_nothing() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    scenario = build(mem, a)
    check(mem.booklets, scenario)
    check_isolated(mem.booklets, b.id, scenario)
