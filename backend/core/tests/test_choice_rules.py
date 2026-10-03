"""Best N, OR pairs and negative marking, computed from a blueprint (C12, C19, D9)."""

from decimal import Decimal
from uuid import UUID

import pytest

from tarn_core.domain.blueprint import AnyN, ExamBlueprint, QuestionSlot, Section
from tarn_core.domain.content import ContentMeta
from tarn_core.errors import InvariantError
from tarn_core.ids import BlueprintId, CollegeId, QuestionId, SubjectId, UserId
from tarn_core.services.marking import Outcome, apply_choice_rules, slot_mark
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, ci_shaped_blueprint, ipr_shaped_blueprint


def d(values: dict[str, str]) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in values.items()}


@pytest.fixture
def ci() -> ExamBlueprint:
    mem = InMemory()
    return ci_shaped_blueprint(mem, add_college(mem, "A"))


@pytest.fixture
def ipr() -> ExamBlueprint:
    mem = InMemory()
    return ipr_shaped_blueprint(mem, add_college(mem, "A"))


def test_best_n_keeps_the_top_marks(ci: ExamBlueprint) -> None:
    # Section A is any 5 of 7 x 2: all seven answered.
    marks = d({"1": "2", "2": "0.5", "3": "1.5", "4": "2", "5": "1", "6": "0", "7": "1.5"})
    result = apply_choice_rules(ci, marks)
    section_a = result.sections[0]
    counted = [s.slot_label for s in section_a.slots if s.counted]
    dropped = {s.slot_label: s.outcome for s in section_a.slots if not s.counted}
    assert counted == ["1", "3", "4", "5", "7"]
    assert dropped == {"2": Outcome.NOT_COUNTED_BEST_N, "6": Outcome.NOT_COUNTED_BEST_N}
    assert section_a.total == Decimal(8)
    assert result.total == Decimal(8)
    # Dropped answers stay visible with their marks.
    assert next(s for s in section_a.slots if s.slot_label == "2").mark == Decimal("0.5")


def test_ties_resolve_in_paper_order(ci: ExamBlueprint) -> None:
    marks = d({str(n): "2" for n in range(1, 8)})
    section_a = apply_choice_rules(ci, marks).sections[0]
    assert [s.slot_label for s in section_a.slots if s.counted] == ["1", "2", "3", "4", "5"]


def test_fewer_than_n_attempted_all_count(ci: ExamBlueprint) -> None:
    result = apply_choice_rules(ci, d({"8": "4", "9": "3"}))
    section_b = result.sections[1]
    assert [s.slot_label for s in section_b.slots if s.counted] == ["8", "9"]
    assert all(
        s.outcome is Outcome.NOT_ATTEMPTED and s.mark is None
        for s in section_b.slots
        if s.slot_label not in {"8", "9"}
    )
    assert result.total == Decimal(7)
    assert result.max_marks == Decimal(50)


def test_or_pair_counts_the_higher_alternative(ipr: ExamBlueprint) -> None:
    result = apply_choice_rules(ipr, d({"12": "9", "13": "11.5"}))
    section_c = {s.slot_label: s for s in result.sections[2].slots}
    assert section_c["13"].counted
    assert section_c["12"].outcome is Outcome.NOT_COUNTED_OR
    assert result.total == Decimal("11.5")


def test_or_pair_single_attempt_and_tie(ipr: ExamBlueprint) -> None:
    only = {s.slot_label: s.outcome for s in apply_choice_rules(ipr, d({"13": "4"})).slots}
    assert only["13"] is Outcome.COUNTED
    assert only["12"] is Outcome.NOT_ATTEMPTED
    tie = {
        s.slot_label: s.outcome for s in apply_choice_rules(ipr, d({"12": "7", "13": "7"})).slots
    }
    assert tie["12"] is Outcome.COUNTED
    assert tie["13"] is Outcome.NOT_COUNTED_OR


def test_marks_outside_a_slot_are_rejected(ci: ExamBlueprint) -> None:
    with pytest.raises(InvariantError, match="no slot"):
        apply_choice_rules(ci, d({"99": "1"}))
    with pytest.raises(InvariantError, match="outside"):
        apply_choice_rules(ci, d({"1": "3"}))


def test_negative_marking() -> None:
    meta = ContentMeta(owning_college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
    q = QuestionId(UUID(int=3))
    slots = tuple(
        QuestionSlot(label=str(n), marks=Decimal(1), question_id=q, negative_marks=Decimal("0.25"))
        for n in range(1, 5)
    )
    bp = ExamBlueprint(
        id=BlueprintId(UUID(int=4)),
        meta=meta,
        subject_id=SubjectId(UUID(int=5)),
        title="MCQ",
        total_marks=Decimal(3),
        sections=(Section(label="A", rule=AnyN(3), items=slots),),
    )
    assert slot_mark(slots[0], Decimal(0)) == Decimal("-0.25")
    assert slot_mark(slots[0], Decimal(1)) == Decimal(1)
    with pytest.raises(InvariantError):
        slot_mark(slots[0], Decimal(2))
    # Two right, two wrong: best 3 keeps one penalty.
    marks = {"1": Decimal(1), "2": Decimal("-0.25"), "3": Decimal(1), "4": Decimal("-0.25")}
    result = apply_choice_rules(bp, marks)
    assert result.total == Decimal("1.75")
    assert [s.slot_label for s in result.slots if not s.counted] == ["4"]
