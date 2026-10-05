"""Scoring a whole booklet (P13): ``segmented`` → ``scored``, then best N, OR and negative
marking at booklet level through ``TotalsService``; re-scoring after edits; and the proof that
scoring sends nothing over the network (the embedder is local)."""

import socket
from collections.abc import Iterator
from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import AnyN, ExamBlueprint, QuestionSlot, Section
from tarn_core.domain.booklet import AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.content import (
    CriterionType,
    NumericParams,
    RubricCriterion,
    Subject,
)
from tarn_core.ids import BlueprintId, CriterionId, SubjectId
from tarn_core.ports.jobs import JOB_SCORE_BOOKLET
from tarn_core.services.marking import Outcome
from tarn_core.services.scoring import (
    FAILED_SCORING,
    AnswerApprovedError,
    BookletScorer,
    ScoringPolicy,
    ScoringService,
    queue_scoring,
)
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    add_question,
    ci_shaped_blueprint,
    ipr_shaped_blueprint,
    make_services,
    meta,
)
from tarn_core.testing.scoring import written_answer

POLICY = ScoringPolicy(half=0.25, full=0.45, relevance_min=0.0, relevance_soft=0.0)
GOOD = ["alpha and beta.", "The idea follows from alpha and beta."]  # full marks on add_rubric
HALF = ["alpha only."]


def scorer(mem: InMemory) -> BookletScorer:
    return BookletScorer(
        booklets=mem.booklets,
        scoring=ScoringService.standard(
            booklets=mem.booklets,
            scores=mem.scores,
            content=mem.content,
            runtime=mem.runtime,
            embedder=TrigramEmbedder(),
            policy=POLICY,
        ),
        runtime=mem.runtime,
    )


def register(mem: InMemory, college: CollegeFixture, blueprint: ExamBlueprint) -> Booklet:
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
    segmented = replace(booklet, status=BookletStatus.SEGMENTED, version=booklet.version + 1)
    mem.booklets.save(college.id, segmented)
    return segmented


def totals(mem: InMemory, booklet: Booklet) -> dict[str, tuple[Decimal | None, Outcome]]:
    preview = make_services(mem).totals.preview(booklet.college_id, booklet.id)
    return {s.slot_label: (s.mark, s.outcome) for s in preview.result.slots}


def test_segmented_booklet_is_scored_answer_by_answer() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    written_answer(mem, booklet, "1", GOOD)
    written_answer(mem, booklet, "2", HALF)

    assert scorer(mem).step(college.id, booklet.id)

    assert mem.booklets.get(college.id, booklet.id).status is BookletStatus.SCORED
    for answer in mem.booklets.answers(college.id, booklet.id):
        assert len(mem.scores.scores(college.id, answer.id)) == 1
    marks = totals(mem, booklet)
    assert marks["1"] == (Decimal(2), Outcome.COUNTED)
    assert marks["2"][0] is not None and marks["2"][0] < 2
    event = mem.audit.events[-1]
    assert event.action is AuditAction.BOOKLET_SCORED and event.actor_id is None
    assert event.after is not None and isinstance(event.after, dict)
    assert event.after["answers"] == 2 and event.after["embedder"] == "trigram 1-1024"
    # Safe to repeat: a scored booklet is left alone.
    assert scorer(mem).step(college.id, booklet.id)
    first = mem.booklets.answers(college.id, booklet.id)[0]
    assert len(mem.scores.scores(college.id, first.id)) == 1


def test_best_n_keeps_the_highest_scored_answers() -> None:
    """Section A is any 5 of 7 (2 marks each): six answered, the weakest is not counted."""
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    for label in ("1", "2", "3", "4", "6"):
        written_answer(mem, booklet, label, GOOD)
    written_answer(mem, booklet, "5", ["nothing relevant here at all"])

    scorer(mem).step(college.id, booklet.id)
    marks = totals(mem, booklet)
    assert marks["5"] == (Decimal(0), Outcome.NOT_COUNTED_BEST_N)
    assert all(marks[x] == (Decimal(2), Outcome.COUNTED) for x in ("1", "2", "3", "4", "6"))
    assert marks["7"] == (None, Outcome.NOT_ATTEMPTED)


def test_or_keeps_the_higher_scored_alternative() -> None:
    """QP-IPR shape: Q12 (a 10 + b 5) OR Q13 (a 10 + b 5); the better-written one counts."""
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ipr_shaped_blueprint(mem, college))
    written_answer(mem, booklet, "12.a", HALF)
    written_answer(mem, booklet, "12.b", HALF)
    written_answer(mem, booklet, "13.a", GOOD)
    written_answer(mem, booklet, "13.b", GOOD)

    scorer(mem).step(college.id, booklet.id)
    marks = totals(mem, booklet)
    assert marks["13"] == (Decimal(15), Outcome.COUNTED)
    assert marks["12"][1] is Outcome.NOT_COUNTED_OR


def mcq_paper(mem: InMemory, college: CollegeFixture) -> ExamBlueprint:
    """Section M: three 1-mark MCQs, a quarter mark off for a wrong attempted answer."""
    subject = Subject(id=SubjectId(mem.ids.new()), meta=meta(college), code="MCQ1", name="MCQ")
    mem.content.save(subject)
    slots = []
    for number, right in ((1, "3"), (2, "7"), (3, "12")):
        q = add_question(mem.content, college, mem.ids.new(), subject.id, Decimal(1))
        mem.content.save(
            RubricCriterion(
                id=CriterionId(mem.ids.new()),
                meta=meta(college),
                question_id=q.id,
                label="Right option",
                type=CriterionType.NUMERIC,
                weight=Decimal(1),
                params=NumericParams(expected=Decimal(right)),
            )
        )
        slots.append(
            QuestionSlot(
                label=str(number),
                marks=Decimal(1),
                question_id=q.id,
                negative_marks=Decimal("0.25"),
            )
        )
    blueprint = ExamBlueprint(
        id=BlueprintId(mem.ids.new()),
        meta=meta(college),
        subject_id=subject.id,
        title="MCQ paper",
        total_marks=Decimal(3),
        mark_step=Decimal("0.25"),
        sections=(Section(label="M", rule=AnyN(3), items=tuple(slots)),),
    )
    mem.content.save(blueprint)
    return blueprint


def test_negative_marking_for_a_wrong_mcq() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, mcq_paper(mem, college))
    written_answer(mem, booklet, "1", ["Answer: option 3"])
    written_answer(mem, booklet, "2", ["Answer: option 9"])  # wrong

    scorer(mem).step(college.id, booklet.id)
    marks = totals(mem, booklet)
    assert marks["1"] == (Decimal(1), Outcome.COUNTED)
    assert marks["2"] == (Decimal("-0.25"), Outcome.COUNTED)
    assert marks["3"] == (None, Outcome.NOT_ATTEMPTED)  # not attempted: no deduction
    preview = make_services(mem).totals.preview(college.id, booklet.id)
    assert preview.result.total == Decimal("0.75")


def test_rescore_touches_only_the_given_answers_and_refuses_approved_ones() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    one = written_answer(mem, booklet, "1", GOOD)
    two = written_answer(mem, booklet, "2", HALF)
    scorer(mem).step(college.id, booklet.id)

    again = scorer(mem).rescore(college.id, college.teacher.id, [two.id])
    assert [s.answer_id for s in again] == [two.id]
    assert len(mem.scores.scores(college.id, one.id)) == 1
    assert len(mem.scores.scores(college.id, two.id)) == 2
    assert mem.audit.events[-1].actor_id == college.teacher.id

    mem.booklets.save_answer(college.id, replace(one, status=AnswerStatus.APPROVED))
    with pytest.raises(AnswerApprovedError):
        scorer(mem).rescore(college.id, college.teacher.id, [one.id])


def test_an_emptied_answer_is_not_scored() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    answer = written_answer(mem, booklet, "1", GOOD)
    mem.booklets.save_answer(college.id, replace(answer, segment_ids=()))
    scorer(mem).step(college.id, booklet.id)
    assert mem.scores.scores(college.id, answer.id) == []


def test_abandoned_scoring_fails_the_booklet() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    scorer(mem).abandon(college.id, booklet.id)
    failed = mem.booklets.get(college.id, booklet.id)
    assert failed.status is BookletStatus.FAILED and failed.failure_reason == FAILED_SCORING


def test_queue_scoring_is_idempotent_per_booklet() -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    queue_scoring(mem.jobs, college.id, booklet.id)
    queue_scoring(mem.jobs, college.id, booklet.id)
    assert [j.kind for j in mem.jobs.jobs] == [JOB_SCORE_BOOKLET]


# --- no student text leaves the machine -------------------------------------------------------


class NetworkUsedError(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any attempt to open a connection or resolve a name fails the test."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise NetworkUsedError("scoring tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    yield


def test_the_guard_itself_catches_a_connection(no_network: None) -> None:
    with pytest.raises(NetworkUsedError):
        socket.create_connection(("127.0.0.1", 9))


def test_scoring_a_booklet_uses_no_network(no_network: None) -> None:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = register(mem, college, ci_shaped_blueprint(mem, college))
    written_answer(mem, booklet, "1", GOOD)
    written_answer(mem, booklet, "9", ["The idea follows from alpha and beta, said the student."])
    assert scorer(mem).step(college.id, booklet.id)
    assert mem.booklets.get(college.id, booklet.id).status is BookletStatus.SCORED
