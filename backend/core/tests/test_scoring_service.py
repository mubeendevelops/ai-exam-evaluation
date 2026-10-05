"""Scoring one answer (P13) on the seeded QP-CI paper: text assembly (struck-out lines, labels,
duplicates), the flags, the off-target guard, versions, and the vector cache.

The off-target fixture is a synthetic answer written in the pattern of B-CI2 Q12 (the US
President, Congress, the Marines, for "the executive powers of the President"); no sample text
is copied."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import pytest

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, RegionKind, SegmentFlag
from tarn_core.domain.common import ContentKind, EngineRef
from tarn_core.domain.content import CriterionType, Glossary, Question, ReferenceAnswer
from tarn_core.domain.scoring import AnswerFlag, AnswerScore, ScoringCalibration
from tarn_core.seed.data.papers import QP_CI_TITLE
from tarn_core.services.scoring import UNSCORED, NoScorerError, ScoringPolicy, ScoringService
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import FixedCreditScorer, InMemory
from tarn_core.testing.builders import make_services
from tarn_core.testing.scoring import Line, Written, written_answer
from tarn_core.testing.seed_world import SeededCollege, seed_in_memory

# The trigram embedder's cosines run lower than a sentence model's: test thresholds to match.
TRIGRAM_POLICY = ScoringPolicy(half=0.25, full=0.45, relevance_min=0.25, relevance_soft=0.4)

ON_TARGET = [
    "12. Under Article 53 the executive power of the Union is vested in the President.",
    "The President appoints the Prime Minister and the council of ministers.",
    "He appoints the Governors of the states and the judges of the Supreme Court.",
    "He is the Supreme Commander of the defence forces.",
    "He can declare an emergency.",
]

OFF_TARGET = [
    "12. The President is the head of state and of the government.",
    "He signs the bills passed by Congress into law or sends them back.",
    "He is the commander in chief of the army, the navy and the Marines.",
    "He appoints ambassadors and federal judges with the consent of the Senate.",
    "He is elected by all Americans every four years.",
]


class CountingEmbedder(TrigramEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.texts: list[str] = []

    def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        self.texts.extend(texts)
        return super().embed(texts)


@dataclass
class Paper:
    mem: InMemory
    college: SeededCollege
    blueprint: ExamBlueprint
    booklet: Booklet
    embedder: CountingEmbedder

    def service(
        self, *, policy: ScoringPolicy | None = TRIGRAM_POLICY, **kw: object
    ) -> ScoringService:
        return ScoringService.standard(
            booklets=self.mem.booklets,
            scores=self.mem.scores,
            content=self.mem.content,
            runtime=self.mem.runtime,
            embedder=self.embedder,
            calibrations=self.mem.scoring_calibrations,
            policy=policy,
            **kw,  # type: ignore[arg-type]
        )

    def score(self, slot: str, *segments: Written | Sequence[str | Line]) -> AnswerScore:
        answer = written_answer(self.mem, self.booklet, slot, *segments)
        return self.service().score_answer(self.booklet.college_id, None, answer.id)

    def question(self, slot: str) -> Question:
        _, question_id, _ = self.blueprint.leaf(slot)
        assert question_id is not None
        return self.mem.content.get(Question, question_id)


@pytest.fixture(scope="module")
def seeded() -> tuple[InMemory, list[SeededCollege]]:
    mem = InMemory()
    return mem, seed_in_memory(mem)


@pytest.fixture
def paper(seeded: tuple[InMemory, list[SeededCollege]]) -> Paper:
    mem, colleges = seeded
    blueprint = next(b for b in mem.content.latest(ExamBlueprint) if b.title == QP_CI_TITLE)
    college = next(c for c in colleges if c.accounts.college_id == blueprint.meta.owning_college_id)
    college_id = college.accounts.college_id
    student = mem.students.list(college_id)[0]
    booklet = (
        make_services(mem)
        .booklets.register(
            college_id,
            college.accounts.teacher_ids[0],
            student_id=student.id,
            blueprint_id=blueprint.id,
            file_sha256=f"{mem.ids.new().int:064x}"[-64:],
        )
        .booklet
    )
    return Paper(mem, college, blueprint, booklet, CountingEmbedder())


# --- the off-target guard (C13) ---------------------------------------------------------------


def test_an_on_target_answer_scores_well_and_is_not_flagged(paper: Paper) -> None:
    score = paper.score("12", ON_TARGET)
    assert AnswerFlag.OFF_TARGET not in score.flags
    assert score.mark is not None and score.mark >= Decimal(4)
    assert score.relevance is not None and score.relevance > TRIGRAM_POLICY.relevance_min


def test_b_ci2_q12_pattern_is_flagged_off_target_despite_keyword_credit(paper: Paper) -> None:
    score = paper.score("12", OFF_TARGET)
    assert AnswerFlag.OFF_TARGET in score.flags
    assert any("Congress" in r and "Marines" in r for r in score.reasons)
    # Keywords alone would reward it (C13): the flag is what sends it to the teacher.
    assert score.mark is not None and score.mark > 0
    listed = score.criterion_scores[0]
    assert listed.reason is not None and "ambassadors" in listed.reason.matched


def test_the_flag_comes_from_the_teachers_terms_not_from_the_mark(paper: Paper) -> None:
    glossary = paper.mem.content.for_question(Glossary, paper.question("12").id)[0]
    assert "Congress" in glossary.off_target_terms
    assert glossary.ref in paper.score("12", OFF_TARGET).content_versions


def test_low_relevance_alone_flags_the_answer(paper: Paper) -> None:
    score = paper.score(
        "12", ["12. Photosynthesis turns sunlight water and carbon dioxide into sugar."]
    )
    assert AnswerFlag.OFF_TARGET in score.flags
    assert any(r.startswith("low relevance") for r in score.reasons)


def test_unfamiliar_names_flag_only_with_middling_relevance(paper: Paper) -> None:
    strict = ScoringPolicy(half=0.25, full=0.45, relevance_min=0.0, relevance_soft=1.0)
    answer = written_answer(
        paper.mem,
        paper.booklet,
        "12",
        ["12. The President appoints the Governors, said Zorblatt and Quenwick."],
    )
    flagged = paper.service(policy=strict).score_answer(paper.booklet.college_id, None, answer.id)
    assert any("not found in the paper" in r for r in flagged.reasons)
    relaxed = ScoringPolicy(half=0.25, full=0.45, relevance_min=0.0, relevance_soft=0.0)
    calm = paper.service(policy=relaxed).score_answer(paper.booklet.college_id, None, answer.id)
    assert AnswerFlag.OFF_TARGET not in calm.flags


# --- text assembly and flags ------------------------------------------------------------------


def test_struck_out_lines_are_not_scored(paper: Paper) -> None:
    lines: list[str | Line] = [
        "12. The President appoints the Prime Minister.",
        Line("He appoints the Governors and the judges.", struck_out=True),
    ]
    score = paper.score("12", lines)
    listed = score.criterion_scores[0]
    assert listed.reason is not None
    assert "appoints governors" not in listed.reason.matched
    assert "appoints judges" not in listed.reason.matched
    assert any("struck-out" in r for r in score.reasons)
    again = paper.score("12", [lines[0], Line("He appoints the Governors and the judges.")])
    assert again.criterion_scores[0].credit > listed.credit


def test_the_answer_label_is_not_read_as_a_number(paper: Paper) -> None:
    """QP-CI has no numeric criterion; the label stripping is checked on the text itself."""
    score = paper.score("12", ["12 b) Supreme Commander of the defence forces"])
    assert score.criterion_scores[0].reason is not None
    assert score.criterion_scores[0].reason.sentences == (0,)


def test_a_blank_answer_is_flagged_and_scores_zero(paper: Paper) -> None:
    score = paper.score("12", ["12.", Line("", kind=RegionKind.DIAGRAM)])
    assert score.flags[0] is AnswerFlag.BLANK
    assert score.mark == 0
    assert AnswerFlag.OFF_TARGET not in score.flags  # blank, not off-target


def test_low_ocr_share_flags_the_answer(paper: Paper) -> None:
    lines = [Line(text, flagged=k < 2) for k, text in enumerate(ON_TARGET[:4])]
    score = paper.score("12", lines)
    assert AnswerFlag.LOW_OCR in score.flags
    assert "2 of 4 lines were hard to read" in score.reasons
    clean = paper.score("12", ON_TARGET[:4])
    assert AnswerFlag.LOW_OCR not in clean.flags


def test_a_question_answered_twice_suggests_the_better_copy(paper: Paper) -> None:
    twice = (SegmentFlag.DUPLICATE,)
    weak = Written(lines=["12. The President is the head of state."], flags=twice)
    strong = Written(lines=ON_TARGET, flags=twice)
    score = paper.score("12", weak, strong)
    assert AnswerFlag.DUPLICATE in score.flags
    assert any("copy 2 scored highest" in r for r in score.reasons)
    alone = paper.score("12", ON_TARGET)
    assert score.mark == alone.mark


def test_a_continuation_joins_the_copy_before_it(paper: Paper) -> None:
    twice = (SegmentFlag.DUPLICATE,)
    first = Written(lines=["12. The President appoints the Prime Minister."], flags=twice)
    more = Written(lines=["He appoints the Governors and the judges."])
    second = Written(lines=["12. He can declare an emergency."], flags=twice)
    score = paper.score("12", first, more, second)
    assert any("copy 1 scored highest" in r for r in score.reasons)


# --- versions, scorers, policy ----------------------------------------------------------------


def test_every_score_records_content_scorer_and_embedder_versions(paper: Paper) -> None:
    question = paper.question("12")
    score = paper.score("12", ON_TARGET)
    kinds = {ref.kind for ref in score.content_versions}
    assert {
        ContentKind.BLUEPRINT,
        ContentKind.QUESTION,
        ContentKind.RUBRIC_CRITERION,
        ContentKind.REFERENCE_ANSWER,
        ContentKind.GLOSSARY,
    } <= kinds
    key = paper.mem.content.for_question(ReferenceAnswer, question.id)[0]
    assert key.ref in score.content_versions and question.ref == score.question
    assert score.scorer_versions == {EngineRef(name="list", version="1")}
    assert score.embedder == paper.embedder.ref
    assert all(c.reason is not None and c.evidence for c in score.criterion_scores)


def test_a_stored_calibration_sets_the_policy_and_is_recorded(paper: Paper) -> None:
    calibration = ScoringCalibration(
        embedder=paper.embedder.ref.name,
        half=0.2,
        full=0.4,
        margin=0.02,
        relevance_min=0.05,
        relevance_soft=0.1,
        samples=12,
    )
    paper.mem.scoring_calibrations.save(calibration)
    service = paper.service(policy=None)
    assert service.policy.full == 0.4 and service.policy.calibration == calibration.ref
    answer = written_answer(paper.mem, paper.booklet, "12", ON_TARGET)
    score = service.score_answer(paper.booklet.college_id, None, answer.id)
    assert calibration.ref in score.content_versions


def _semantic_slot(paper: Paper) -> str:
    """A QP-CI leaf whose rubric has a semantic criterion."""
    from tarn_core.domain.blueprint import leaves
    from tarn_core.domain.content import RubricCriterion

    for slot in paper.blueprint.slots():
        for label, question_id, _ in leaves(slot):
            assert question_id is not None
            criteria = paper.mem.content.for_question(RubricCriterion, question_id)
            if any(c.type is CriterionType.SEMANTIC for c in criteria):
                return label
    raise AssertionError("no semantic criterion on QP-CI")


def test_sentence_vectors_are_stored_and_reused_while_the_text_is_unchanged(paper: Paper) -> None:
    slot = _semantic_slot(paper)
    answer = written_answer(paper.mem, paper.booklet, slot, ON_TARGET)
    service = paper.service()
    college_id = paper.booklet.college_id
    service.score_answer(college_id, None, answer.id)
    stored = paper.mem.scores.vectors(college_id, answer.id, paper.embedder.ref)
    assert len(stored) == 5
    before = list(paper.embedder.texts)
    service.score_answer(college_id, None, answer.id)
    new = paper.embedder.texts[len(before) :]
    assert not set(new) & set(ON_TARGET[1:])  # no student sentence embedded again


def test_criteria_without_a_scorer_are_left_to_the_teacher(paper: Paper) -> None:
    answer = written_answer(paper.mem, paper.booklet, "12", ON_TARGET)
    lists_off = ScoringService(
        booklets=paper.mem.booklets,
        scores=paper.mem.scores,
        content=paper.mem.content,
        scorers=[FixedCreditScorer(types=frozenset({CriterionType.NUMERIC}))],
        runtime=paper.mem.runtime,
    )
    score = lists_off.score_answer(paper.booklet.college_id, None, answer.id)
    assert score.mark == 0
    assert all(c.scorer == UNSCORED and "manual" in c.flags for c in score.criterion_scores)
    strict = ScoringService(
        booklets=paper.mem.booklets,
        scores=paper.mem.scores,
        content=paper.mem.content,
        scorers=[],
        runtime=paper.mem.runtime,
        strict=True,
    )
    with pytest.raises(NoScorerError):
        strict.score_answer(paper.booklet.college_id, None, answer.id)


def test_scoring_audit_holds_marks_and_flags_but_no_text(paper: Paper) -> None:
    paper.score("12", OFF_TARGET)
    event = paper.mem.audit.events[-1]
    assert event.action.value == "answer.scored"
    assert event.actor_id is None
    rendered = repr(event.after)
    assert "off_target" in rendered
    assert not any(word in rendered for word in ("Congress", "Marines", "President"))
