"""Rubric criteria: weights sum to the question's max marks; params fit the type."""

from decimal import Decimal
from uuid import UUID

import pytest

from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    Glossary,
    ListItem,
    ListParams,
    NumericParams,
    Question,
    Rubric,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, CriterionId, GlossaryId, QuestionId, SubjectId, UserId

META = ContentMeta(owning_college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
QUESTION = Question(
    id=QuestionId(UUID(int=10)),
    meta=META,
    subject_id=SubjectId(UUID(int=11)),
    code="KM-1",
    text="Compute the two cluster centroids after one k-means step.",
    max_marks=Decimal(5),
)


def criterion(n: int, weight: str, question_id: QuestionId = QUESTION.id) -> RubricCriterion:
    return RubricCriterion(
        id=CriterionId(UUID(int=100 + n)),
        meta=META,
        question_id=question_id,
        label=f"step {n}",
        type=CriterionType.NUMERIC,
        weight=Decimal(weight),
        params=NumericParams(expected=Decimal("2.5"), tolerance=Decimal("0.01")),
    )


def test_weights_must_sum_to_max_marks() -> None:
    rubric = Rubric(question=QUESTION, criteria=(criterion(1, "2"), criterion(2, "3")))
    assert len(rubric.content_refs) == 3  # question + two criteria
    with pytest.raises(InvariantError, match="sum to 4"):
        Rubric(question=QUESTION, criteria=(criterion(1, "2"), criterion(2, "2")))
    with pytest.raises(InvariantError):
        Rubric(question=QUESTION, criteria=())


def test_half_mark_weights_are_exact() -> None:
    parts = tuple(criterion(n, "0.5") for n in range(10))
    Rubric(question=QUESTION, criteria=parts)


def test_criteria_of_another_question_are_rejected() -> None:
    other = QuestionId(UUID(int=99))
    with pytest.raises(InvariantError, match="another question"):
        Rubric(question=QUESTION, criteria=(criterion(1, "2"), criterion(2, "3", other)))


def test_guidance_only_key_has_no_criteria() -> None:
    Rubric(question=QUESTION, criteria=(), guidance_only=True)
    with pytest.raises(InvariantError):
        Rubric(question=QUESTION, criteria=(criterion(1, "5"),), guidance_only=True)


def test_params_must_match_type_and_weight_be_positive() -> None:
    with pytest.raises(InvariantError, match="needs ListParams"):
        RubricCriterion(
            id=CriterionId(UUID(int=200)),
            meta=META,
            question_id=QUESTION.id,
            label="duties",
            type=CriterionType.LIST,
            weight=Decimal(5),
            params=SemanticParams(reference_statement="Any five duties."),
        )
    with pytest.raises(InvariantError):
        criterion(1, "0")


def test_list_params_bounds() -> None:
    items = (ListItem(term="a"), ListItem(term="b"))
    ListParams(items=items, required_count=2)
    with pytest.raises(InvariantError):
        ListParams(items=items, required_count=3)


def test_glossary_merges_teacher_terms_and_reference_labels() -> None:
    glossary = Glossary(
        id=GlossaryId(UUID(int=300)),
        meta=META,
        question_id=QUESTION.id,
        teacher_terms=("Centroid", "cluster"),
        reference_labels=("centroid", "assign points"),
    )
    assert glossary.terms == ("Centroid", "cluster", "assign points")
