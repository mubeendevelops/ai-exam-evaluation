"""Every score records the content and scorer versions it used (rule 11)."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.blueprint import ExamBlueprint, QuestionSlot, Section
from tarn_core.domain.common import ContentRef, EngineRef
from tarn_core.domain.content import Question, ReferenceAnswer, RubricCriterion, Subject
from tarn_core.domain.scoring import AnswerFlag, AnswerScore, CriterionScore
from tarn_core.errors import InvariantError
from tarn_core.ids import AnswerId, AnswerScoreId, BlueprintId, ReferenceAnswerId, SubjectId
from tarn_core.ports.engines import ScoringInput
from tarn_core.testing import FixedCreditScorer, InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    add_answer,
    add_college,
    add_question,
    ci_shaped_blueprint,
    make_services,
    meta,
)


@pytest.fixture
def world() -> tuple[InMemory, CollegeFixture, ExamBlueprint]:
    mem = InMemory()
    college = add_college(mem, "A")
    return mem, college, ci_shaped_blueprint(mem, college)


def test_score_records_question_criteria_blueprint_and_scorer(
    world: tuple[InMemory, CollegeFixture, ExamBlueprint],
) -> None:
    mem, college, blueprint = world
    svc = make_services(mem, [FixedCreditScorer(credit=Decimal("0.5"))])
    booklet = svc.booklets.register(
        college.id,
        college.teacher.id,
        student_id=college.students[0].id,
        blueprint_id=blueprint.id,
        file_sha256="a" * 64,
    ).booklet
    answer = add_answer(mem, booklet, "8")  # section B, 5 marks

    score = svc.scoring.score_answer(college.id, college.teacher.id, answer.id, answer_text="...")
    assert score is not None
    assert score.mark == Decimal("2.5")
    question_id = blueprint.leaf("8")[1]
    criteria = mem.content.for_question(RubricCriterion, question_id)
    assert score.question.version == 1
    assert {c.ref for c in criteria} | {score.question, blueprint.ref} <= score.content_versions
    assert score.scorer_versions == {EngineRef(name="fixed-credit", version="1")}
    assert mem.scores.scores(college.id, answer.id) == [score]


def test_editing_a_question_leaves_old_scores_on_the_old_version(
    world: tuple[InMemory, CollegeFixture, ExamBlueprint],
) -> None:
    mem, college, blueprint = world
    svc = make_services(mem)
    booklet = svc.booklets.register(
        college.id,
        college.teacher.id,
        student_id=college.students[0].id,
        blueprint_id=blueprint.id,
        file_sha256="b" * 64,
    ).booklet
    answer = add_answer(mem, booklet, "1")
    first = svc.scoring.score_answer(college.id, college.teacher.id, answer.id, answer_text="x")
    question_id = blueprint.leaf("1")[1]

    svc.content.edit_question(college.id, college.teacher.id, question_id, text="Reworded.")
    second = svc.scoring.score_answer(college.id, college.teacher.id, answer.id, answer_text="x")

    assert first is not None and second is not None
    assert first.question.version == 1
    assert second.question.version == 2
    assert mem.content.get(Question, question_id, 1).text != "Reworded."
    assert [q.meta.version for q in mem.content.versions(Question, question_id)] == [1, 2]


def test_answer_score_must_name_every_content_version_it_used(
    world: tuple[InMemory, CollegeFixture, ExamBlueprint],
) -> None:
    mem, college, blueprint = world
    question_id = blueprint.leaf("1")[1]
    question = mem.content.get(Question, question_id)
    criteria = mem.content.for_question(RubricCriterion, question_id)
    engine = EngineRef(name="x", version="1")
    scores = tuple(
        CriterionScore(criterion=c.ref, weight=c.weight, credit=Decimal(1), scorer=engine)
        for c in criteria
    )
    all_refs = frozenset({question.ref, *(c.ref for c in criteria)})

    def make(content_versions: frozenset[ContentRef], mark: Decimal = Decimal(2)) -> AnswerScore:
        return AnswerScore(
            id=AnswerScoreId(mem.ids.new()),
            college_id=college.id,
            answer_id=AnswerId(mem.ids.new()),
            question=question.ref,
            criterion_scores=scores,
            mark_step=Decimal("0.5"),
            mark=mark,
            content_versions=content_versions,
            created_at=mem.clock.now(),
        )

    make(all_refs)
    with pytest.raises(InvariantError, match="not recorded"):
        make(frozenset({question.ref}))
    with pytest.raises(InvariantError, match="rounded sum"):
        make(all_refs, mark=Decimal(1))


def test_scorer_answering_for_another_criterion_version_is_rejected(
    world: tuple[InMemory, CollegeFixture, ExamBlueprint],
) -> None:
    mem, college, blueprint = world

    class StaleScorer(FixedCreditScorer):
        def score(self, item: ScoringInput) -> CriterionScore:
            result = super().score(item)
            return replace(result, criterion=replace(result.criterion, version=9))

    svc = make_services(mem, [StaleScorer()])
    booklet = svc.booklets.register(
        college.id,
        college.teacher.id,
        student_id=college.students[0].id,
        blueprint_id=blueprint.id,
        file_sha256="c" * 64,
    ).booklet
    answer = add_answer(mem, booklet, "1")
    with pytest.raises(InvariantError, match="different criterion version"):
        svc.scoring.score_answer(college.id, college.teacher.id, answer.id, answer_text="x")


def test_guidance_only_key_gets_a_mark_manually_score() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    subject = Subject(id=SubjectId(mem.ids.new()), meta=meta(college), code="S", name="S")
    mem.content.save(subject)
    question = add_question(mem.content, college, mem.ids.new(), subject.id, Decimal(5))
    mem.content.save(
        ReferenceAnswer(
            id=ReferenceAnswerId(mem.ids.new()),
            meta=meta(college),
            question_id=question.id,
            text="Student has to explain with an example.",
            guidance_only=True,
        )
    )
    blueprint = ExamBlueprint(
        id=BlueprintId(mem.ids.new()),
        meta=meta(college),
        subject_id=subject.id,
        title="One question",
        total_marks=Decimal(5),
        sections=(
            Section(
                label="A",
                items=(QuestionSlot(label="1", marks=Decimal(5), question_id=question.id),),
            ),
        ),
    )
    mem.content.save(blueprint)
    svc = make_services(mem)
    booklet = svc.booklets.register(
        college.id,
        college.teacher.id,
        student_id=college.students[0].id,
        blueprint_id=blueprint.id,
        file_sha256="d" * 64,
    ).booklet
    answer = add_answer(mem, booklet, "1")

    score = svc.scoring.score_answer(college.id, college.teacher.id, answer.id, answer_text="x")
    assert score.flags == (AnswerFlag.MARK_MANUALLY,)
    assert score.mark is None and score.criterion_scores == ()
    assert question.ref in score.content_versions  # the guidance key's version too
    assert mem.scores.scores(college.id, answer.id) == [score]
    preview = svc.totals.preview(college.id, booklet.id)
    assert preview.unmarked == (answer.id,)
    assert preview.result.total == 0
