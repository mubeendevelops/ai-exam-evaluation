"""Blueprint structure: totals, any N of M, OR groups, sub-parts and step marks."""

from decimal import Decimal
from uuid import UUID

import pytest

from tarn_core.domain.blueprint import (
    AnyN,
    ExamBlueprint,
    OrGroup,
    QuestionSlot,
    Section,
    Step,
    SubPart,
)
from tarn_core.domain.content import ContentMeta, Question
from tarn_core.errors import InvariantError
from tarn_core.ids import BlueprintId, CollegeId, QuestionId, SubjectId, UserId
from tarn_core.services.marking import check_blueprint_questions
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, ci_shaped_blueprint, ipr_shaped_blueprint

META = ContentMeta(owning_college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
Q = QuestionId(UUID(int=3))


def slot(label: str, marks: str, **kw: object) -> QuestionSlot:
    return QuestionSlot(label=label, marks=Decimal(marks), question_id=Q, **kw)  # type: ignore[arg-type]


def blueprint(total: str, *sections: Section) -> ExamBlueprint:
    return ExamBlueprint(
        id=BlueprintId(UUID(int=4)),
        meta=META,
        subject_id=SubjectId(UUID(int=5)),
        title="T",
        total_marks=Decimal(total),
        sections=sections,
    )


def test_sample_shaped_papers_add_up() -> None:
    mem = InMemory()
    owner = add_college(mem, "A")
    ci = ci_shaped_blueprint(mem, owner)
    ipr = ipr_shaped_blueprint(mem, owner)
    assert [s.max_marks for s in ci.sections] == [10, 20, 20]
    assert [s.max_marks for s in ipr.sections] == [15, 30, 15]
    assert ci.total_marks == 50
    assert ipr.total_marks == 60
    assert len(list(ipr.slots())) == 13
    assert ipr.leaf("12.b")[2] == Decimal(5)


def test_total_must_match_sections() -> None:
    section = Section(
        label="A", rule=AnyN(2), items=(slot("1", "5"), slot("2", "5"), slot("3", "5"))
    )
    assert blueprint("10", section).total_marks == 10
    with pytest.raises(InvariantError, match="add up to 10"):
        blueprint("15", section)


def test_any_n_of_uneven_items_takes_the_largest() -> None:
    section = Section(label="A", rule=AnyN(1), items=(slot("1", "4"), slot("2", "6")))
    assert section.max_marks == 6


def test_n_cannot_exceed_m() -> None:
    with pytest.raises(InvariantError, match="impossible"):
        Section(label="A", rule=AnyN(3), items=(slot("1", "5"), slot("2", "5")))


def test_or_alternatives_need_equal_marks() -> None:
    with pytest.raises(InvariantError, match="equal marks"):
        OrGroup(alternatives=(slot("12", "15"), slot("13", "10")))
    with pytest.raises(InvariantError):
        OrGroup(alternatives=(slot("12", "15"),))


def test_sub_parts_and_steps_must_sum() -> None:
    parts = (
        SubPart(label="a", marks=Decimal(10), question_id=Q),
        SubPart(label="b", marks=Decimal(5), question_id=Q),
    )
    QuestionSlot(label="12", marks=Decimal(15), parts=parts)
    with pytest.raises(InvariantError, match="sub-part marks"):
        QuestionSlot(label="12", marks=Decimal(14), parts=parts)
    with pytest.raises(InvariantError, match="not both"):
        QuestionSlot(label="12", marks=Decimal(15), parts=parts, question_id=Q)
    steps = (Step(label="assign", marks=Decimal(2)), Step(label="update", marks=Decimal(2)))
    slot("5", "4", steps=steps)
    with pytest.raises(InvariantError, match="step marks"):
        slot("5", "5", steps=steps)
    with pytest.raises(InvariantError, match="step marks"):
        SubPart(label="a", marks=Decimal(5), question_id=Q, steps=steps)


def test_labels_must_be_unique() -> None:
    with pytest.raises(InvariantError, match="question labels"):
        blueprint("10", Section(label="A", items=(slot("1", "5"), slot("1", "5"))))
    with pytest.raises(InvariantError, match="section labels"):
        blueprint(
            "10",
            Section(label="A", items=(slot("1", "5"),)),
            Section(label="A", items=(slot("2", "5"),)),
        )


def test_blueprint_leaves_must_match_question_marks() -> None:
    question = Question(
        id=Q, meta=META, subject_id=SubjectId(UUID(int=5)), text="Q", max_marks=Decimal(5)
    )
    bp = blueprint("5", Section(label="A", items=(slot("1", "5"),)))
    check_blueprint_questions(bp, {Q: question})
    with pytest.raises(InvariantError, match="unknown question"):
        check_blueprint_questions(bp, {})
    bigger = blueprint("6", Section(label="A", items=(slot("1", "6"),)))
    with pytest.raises(InvariantError, match="carries 6 marks"):
        check_blueprint_questions(bigger, {Q: question})
