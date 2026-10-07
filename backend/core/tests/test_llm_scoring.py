"""The LLM scorer in the core (P19): off unless the college's flag is on; a second opinion beside
the non-LLM credit (never the mark); the scorer-disagreement flag at more than one band; ``llm``
criteria scored by the model only; a model that is down leaves the other scorers' marks alone;
the audit event names the scorers and holds counts, never text. All text is synthetic."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, QuestionSlot, Section
from tarn_core.domain.booklet import Booklet
from tarn_core.domain.common import ContentKind, ContentRef, EngineRef
from tarn_core.domain.content import (
    CriterionType,
    LlmParams,
    RubricCriterion,
    Subject,
)
from tarn_core.domain.scoring import (
    DISAGREE,
    AnswerFlag,
    AnswerScore,
    CriterionScore,
    LlmUsage,
    SecondOpinion,
    scorers_disagree,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import BlueprintId, CriterionId, SubjectId
from tarn_core.ports.engines import ScoringInput
from tarn_core.services.college_settings import CollegeSettings
from tarn_core.services.scoring import UNSCORED, ScoringPolicy, ScoringService
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    add_question,
    add_rubric,
    make_services,
    meta,
)
from tarn_core.testing.llm import LLM_REF, ScriptedLlm
from tarn_core.testing.scoring import written_answer

POLICY = ScoringPolicy(half=0.25, full=0.45, relevance_min=0.0, relevance_soft=0.0)
GOOD = ["alpha and beta.", "The idea follows from alpha and beta."]  # full marks on add_rubric
HALF = ["alpha only."]


@dataclass
class Paper:
    mem: InMemory
    college: CollegeFixture
    booklet: Booklet

    def service(self, llm: ScriptedLlm | None) -> ScoringService:
        return ScoringService.standard(
            booklets=self.mem.booklets,
            scores=self.mem.scores,
            content=self.mem.content,
            runtime=self.mem.runtime,
            embedder=TrigramEmbedder(),
            policy=POLICY,
            llm=llm,
            colleges=self.mem.colleges,
        )

    def switch(self, on: bool) -> None:
        college = self.mem.colleges.get(self.college.id)
        self.mem.colleges.save(replace(college, llm_scoring=on))

    def score(self, slot: str, lines: list[str], llm: ScriptedLlm | None) -> AnswerScore:
        answer = written_answer(self.mem, self.booklet, slot, lines)
        return self.service(llm).score_answer(self.college.id, None, answer.id)


@pytest.fixture
def paper() -> Paper:
    """Question 1: a list and a semantic criterion (4 marks). Question 2: one ``llm`` criterion
    (2 marks)."""
    mem = InMemory()
    college = add_college(mem, "LLM")
    subject = Subject(
        id=SubjectId(mem.ids.new()), meta=meta(college), code="SYN301", name="Synthetic LLM"
    )
    mem.content.save(subject)
    q1 = add_question(mem.content, college, mem.ids.new(), subject.id, Decimal(4))
    add_rubric(mem, college, q1)
    q2 = add_question(mem.content, college, mem.ids.new(), subject.id, Decimal(2))
    mem.content.save(
        RubricCriterion(
            id=CriterionId(mem.ids.new()),
            meta=meta(college),
            question_id=q2.id,
            label="Argues the point",
            type=CriterionType.LLM,
            weight=Decimal(2),
            params=LlmParams(instructions="Give full credit when the answer argues the point."),
        )
    )
    blueprint = ExamBlueprint(
        id=BlueprintId(mem.ids.new()),
        meta=meta(college),
        subject_id=subject.id,
        title="Synthetic LLM paper",
        total_marks=Decimal(6),
        sections=(
            Section(
                label="A",
                items=(
                    QuestionSlot(label="1", marks=Decimal(4), question_id=q1.id),
                    QuestionSlot(label="2", marks=Decimal(2), question_id=q2.id),
                ),
            ),
        ),
    )
    mem.content.save(blueprint)
    booklet = (
        make_services(mem)
        .booklets.register(
            college.id,
            college.teacher.id,
            student_id=college.students[0].id,
            blueprint_id=blueprint.id,
            file_sha256=f"{mem.ids.new().int:064x}"[-64:],
        )
        .booklet
    )
    return Paper(mem, college, booklet)


# --- off by default ---------------------------------------------------------------------------


def test_the_llm_is_not_asked_while_the_college_flag_is_off(paper: Paper) -> None:
    llm = ScriptedLlm(1.0)
    score = paper.score("1", GOOD, llm)
    assert llm.asked == []
    assert all(c.second_opinion is None for c in score.criterion_scores)
    assert score.llm_usage is None
    assert AnswerFlag.SCORER_DISAGREEMENT not in score.flags


def test_a_service_without_an_llm_ignores_the_flag(paper: Paper) -> None:
    paper.switch(True)
    score = paper.score("1", GOOD, None)
    assert all(c.second_opinion is None for c in score.criterion_scores)


def test_llm_criteria_go_to_the_teacher_while_it_is_off(paper: Paper) -> None:
    score = paper.score("2", ["I argue the point at length."], ScriptedLlm(1.0))
    (criterion,) = score.criterion_scores
    assert criterion.scorer == UNSCORED
    assert score.mark == Decimal(0)


# --- a second opinion --------------------------------------------------------------------------


def test_the_second_opinion_sits_beside_the_credit_and_never_changes_the_mark(paper: Paper) -> None:
    without = paper.score("1", GOOD, None)
    paper.switch(True)
    llm = ScriptedLlm(0.5)
    score = paper.score("1", GOOD, llm)
    assert score.mark == without.mark == Decimal(4)
    assert len(llm.asked) == 2
    for c in score.criterion_scores:
        assert c.second_opinion is not None
        assert c.second_opinion.scorer == LLM_REF
        assert c.second_opinion.credit == Decimal("0.5")
        assert c.credit == Decimal(1)
    assert score.llm_usage == LlmUsage(calls=2, input_tokens=200, output_tokens=20)
    assert LLM_REF in score.scorer_versions
    assert EngineRef(name="list", version="1") in score.scorer_versions


def test_one_band_apart_is_not_a_disagreement(paper: Paper) -> None:
    paper.switch(True)
    for credit in (0.5, 1.0):
        score = paper.score("1", GOOD, ScriptedLlm(credit))
        assert not any(DISAGREE in c.flags for c in score.criterion_scores)
        assert AnswerFlag.SCORER_DISAGREEMENT not in score.flags


def test_more_than_one_band_apart_raises_the_disagreement_flag(paper: Paper) -> None:
    paper.switch(True)
    score = paper.score("1", GOOD, ScriptedLlm(0.0))
    assert all(DISAGREE in c.flags for c in score.criterion_scores)
    assert AnswerFlag.SCORER_DISAGREEMENT in score.flags
    assert any("differ by more than one band on 2" in r for r in score.reasons)
    assert score.mark == Decimal(4)  # the suggestion is still the non-LLM one


def test_the_flag_follows_the_criterion_that_disagrees(paper: Paper) -> None:
    paper.switch(True)

    def by_type(item: ScoringInput) -> float:
        return 0.0 if item.criterion.type is CriterionType.SEMANTIC else 1.0

    score = paper.score("1", GOOD, ScriptedLlm(by_type))
    flagged = [c.criterion for c in score.criterion_scores if DISAGREE in c.flags]
    semantic = [
        c.criterion
        for c in score.criterion_scores
        if paper.mem.content.get(RubricCriterion, c.criterion.id).type is CriterionType.SEMANTIC
    ]
    assert flagged == semantic


def test_the_domain_refuses_a_flag_that_does_not_match_the_credits() -> None:
    ref = ContentRef(kind=ContentKind.RUBRIC_CRITERION, id=CriterionId(uuid4()), version=1)
    opinion = SecondOpinion(scorer=LLM_REF, credit=Decimal(0), reason="none")
    with pytest.raises(InvariantError, match="disagree"):
        CriterionScore(
            criterion=ref,
            weight=Decimal(1),
            credit=Decimal(1),
            scorer=EngineRef(name="list", version="1"),
            second_opinion=opinion,
        )
    assert scorers_disagree(Decimal(1), Decimal(0))
    assert not scorers_disagree(Decimal(1), Decimal("0.5"))
    assert not scorers_disagree(Decimal("0.6"), Decimal("0.1"))  # exactly one band: not more


# --- llm criteria ------------------------------------------------------------------------------


def test_an_llm_criterion_is_scored_by_the_model_when_the_flag_is_on(paper: Paper) -> None:
    paper.switch(True)
    score = paper.score("2", ["I argue the point at length."], ScriptedLlm(0.5))
    (criterion,) = score.criterion_scores
    assert criterion.scorer == LLM_REF
    assert criterion.credit == Decimal("0.5")
    assert criterion.second_opinion is None
    assert criterion.reason is not None and criterion.reason.summary == "scripted reason"
    assert score.mark == Decimal(1)
    assert score.llm_usage is not None and score.llm_usage.calls == 1


# --- the model is down -------------------------------------------------------------------------


def test_a_model_that_is_down_leaves_the_other_scorers_alone_and_is_asked_once(
    paper: Paper,
) -> None:
    paper.switch(True)
    llm = ScriptedLlm(lambda _item: None)
    score = paper.score("1", GOOD, llm)
    assert len(llm.asked) == 1  # bounded waiting: not asked again for this answer
    assert score.mark == Decimal(4)
    assert all(c.second_opinion is None for c in score.criterion_scores)
    assert any("LLM scorer was unavailable for 2 criterion" in r for r in score.reasons)
    assert score.llm_usage is None

    only = paper.score("2", ["I argue the point."], ScriptedLlm(lambda _item: None))
    (criterion,) = only.criterion_scores
    assert criterion.scorer == UNSCORED and "manual" in criterion.flags
    assert "unavailable" in (criterion.reason.summary if criterion.reason else "")


# --- the audit log -----------------------------------------------------------------------------


def test_the_audit_event_names_the_scorers_and_holds_counts_only(paper: Paper) -> None:
    paper.switch(True)
    score = paper.score("1", GOOD, ScriptedLlm(0.0))
    event = next(e for e in paper.mem.audit.events if e.action is AuditAction.ANSWER_SCORED)
    after: Any = event.after
    assert after["llm_usage"] == {"calls": 2, "input": 200, "output": 20}
    semantic = TrigramEmbedder().ref
    assert {t["scorer"] for t in after["scorers"]} == {
        "list 1",
        f"semantic 1+{semantic.name}@{semantic.version}",
    }
    assert all(t["second_opinion"] == "llm-scripted 1" for t in after["scorers"])
    assert all(t["disagree"] for t in after["scorers"])
    assert "scorer_disagreement" in after["flags"]
    assert "alpha" not in repr(after)  # no answer text
    assert score.mark == Decimal(4)


# --- the college flag ---------------------------------------------------------------------------


def test_an_operator_switches_the_flag_and_the_audit_log_names_them(paper: Paper) -> None:
    settings = CollegeSettings(colleges=paper.mem.colleges, runtime=paper.mem.runtime)
    assert paper.mem.colleges.get(paper.college.id).llm_scoring is False
    settings.set_llm_scoring(paper.college.id, True, operator="Ops One")
    assert paper.mem.colleges.get(paper.college.id).llm_scoring is True
    settings.set_llm_scoring(paper.college.id, True, operator="Ops One")  # no second event
    events = [e for e in paper.mem.audit.events if e.action is AuditAction.COLLEGE_LLM_SCORING_SET]
    assert len(events) == 1
    assert events[0].before == {"llm_scoring": False}
    assert events[0].after == {"llm_scoring": True, "operator": "Ops One"}
    with pytest.raises(InvariantError):
        settings.set_llm_scoring(paper.college.id, False, operator="  ")
