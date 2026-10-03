"""The development seed (P8): idempotent, blueprints add up, keys are labelled, and nothing
comes from a real student."""

import hashlib
import re
from collections.abc import Iterator
from decimal import Decimal

import pytest

from tarn_core.domain.blueprint import AllOf, AnyN, ExamBlueprint, OrGroup
from tarn_core.domain.content import CriterionType, Question, ReferenceAnswer, RubricCriterion
from tarn_core.domain.tenancy import Role
from tarn_core.seed import COLLEGES, COMMERCE, ENGINEERING, SeedReport
from tarn_core.seed.data import aiml, assignments, constitution, ipr
from tarn_core.seed.data.papers import (
    ASSIGNMENT_1_TITLE,
    QP_CI_TITLE,
    QP_IPR_TITLE,
)
from tarn_core.seed.model import QuestionSeed
from tarn_core.seed.seeder import check_blueprints_marks
from tarn_core.services.blueprint_document import check_document
from tarn_core.testing import InMemory
from tarn_core.testing.builders import make_services
from tarn_core.testing.seed_world import SeededCollege, seed_in_memory


@pytest.fixture
def seeded() -> tuple[InMemory, list[SeededCollege], SeedReport]:
    mem = InMemory()
    report = SeedReport()
    return mem, seed_in_memory(mem, report), report


def all_questions() -> Iterator[QuestionSeed]:
    for college in COLLEGES:
        for _, q in college.questions:
            yield q


def test_seed_is_idempotent(seeded: tuple[InMemory, list[SeededCollege], SeedReport]) -> None:
    mem, first, report = seeded
    assert report.created["colleges"] == 2
    assert report.created["questions"] == 28 + 35
    events = len(mem.audit.events)
    users = {c.accounts.college_id: len(mem.users.list(c.accounts.college_id)) for c in first}
    questions = len(mem.content.latest(Question))

    again = SeedReport()
    second = seed_in_memory(mem, again)

    assert again.created == {}  # nothing new
    assert again.kept["colleges"] == 2
    assert again.kept["questions"] == 28 + 35
    assert again.kept["blueprints"] == 4
    assert len(mem.audit.events) == events
    assert len(mem.content.latest(Question)) == questions
    assert {c.accounts.college_id for c in second} == {c.accounts.college_id for c in first}
    assert users == {
        c.accounts.college_id: len(mem.users.list(c.accounts.college_id)) for c in second
    }
    # same ids: the seed finds the questions again, it does not copy them
    assert [dict(c.content.questions) for c in first] == [dict(c.content.questions) for c in second]


def test_each_college_has_an_admin_and_two_teachers_and_its_own_roster(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
) -> None:
    mem, colleges, _ = seeded
    assert len(colleges) == 2
    for c in colleges:
        users = mem.users.list(c.accounts.college_id)
        assert sorted(u.role.value for u in users) == ["admin", "teacher", "teacher"]
        assert all(u.active for u in users)
        assert len(mem.students.search(c.accounts.college_id, "DEMO", limit=50)) == 12
    admins = {c.seed.admin.email for c in colleges}
    assert len(admins) == 2
    # tenancy: one college's users are not visible through the other's id
    first, second = colleges
    assert {u.id for u in mem.users.list(first.accounts.college_id)}.isdisjoint(
        {u.id for u in mem.users.list(second.accounts.college_id)}
    )
    assert {u.role for u in mem.users.list(first.accounts.college_id)} == {Role.ADMIN, Role.TEACHER}


def test_teachers_can_sign_in_with_the_dev_password(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
) -> None:
    from tarn_core.seed import DEV_PASSWORD
    from tarn_core.services.auth import SignedIn
    from tarn_core.testing.auth_world import AuthWorld

    mem, colleges, _ = seeded
    world = AuthWorld(mem)
    for c in colleges:
        for person in (c.seed.admin, *c.seed.teachers):
            outcome = world.auth.login(c.accounts.college_id, person.email, DEV_PASSWORD)
            assert isinstance(outcome, SignedIn), person.email


def test_content_is_owned_by_the_college_that_seeded_it(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
) -> None:
    mem, colleges, _ = seeded
    engineering, commerce = colleges
    owners = {q.code: q.meta.owning_college_id for q in mem.content.latest(Question)}
    assert {owners[q.code] for q in aiml.QUESTIONS} == {engineering.accounts.college_id}
    for q in (*constitution.QUESTIONS, *ipr.QUESTIONS, *assignments.QUESTIONS_EL):
        assert owners[q.code] == commerce.accounts.college_id
    # global content is visible to the other college, which copies it with its key and rubric
    services = make_services(mem)
    source = engineering.content.questions["K-AI1-Q1"]
    copy = services.content.copy_question(
        commerce.accounts.college_id, commerce.accounts.teacher_ids[0], source
    )
    assert copy.meta.owning_college_id == commerce.accounts.college_id
    assert mem.content.for_question(RubricCriterion, copy.id)
    assert mem.content.for_question(ReferenceAnswer, copy.id)


@pytest.mark.parametrize(
    ("title", "total", "questions"),
    [(QP_CI_TITLE, 50, 17), (QP_IPR_TITLE, 60, 15), (ASSIGNMENT_1_TITLE, 20, 2)],
)
def test_blueprint_totals_validate_and_every_slot_is_linked(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
    title: str,
    total: int,
    questions: int,
) -> None:
    mem, _, _ = seeded
    blueprint = next(b for b in mem.content.latest(ExamBlueprint) if b.title == title)
    assert blueprint.total_marks == Decimal(total)
    assert sum((s.max_marks for s in blueprint.sections), Decimal(0)) == Decimal(total)
    assert list(blueprint.unlinked_leaves()) == []  # booklets can be registered on it
    check_blueprints_marks(blueprint, mem.content)  # each leaf carries its question's marks
    linked = {leaf for s in blueprint.slots() for leaf in _leaf_ids(s)}
    assert len(linked) == questions


def _leaf_ids(slot: object) -> list[object]:
    parts = getattr(slot, "parts", ())
    if parts:
        return [p.question_id for p in parts]
    return [getattr(slot, "question_id")]  # noqa: B009


def test_papers_keep_their_choice_rules(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
) -> None:
    mem, _, _ = seeded
    by_title = {b.title: b for b in mem.content.latest(ExamBlueprint)}
    ci = by_title[QP_CI_TITLE]  # any 5 of 7 x 2, any 4 of 7 x 5, any 2 of 3 x 10
    assert [(s.rule, len(s.items), s.max_marks) for s in ci.sections] == [
        (AnyN(5), 7, Decimal(10)),
        (AnyN(4), 7, Decimal(20)),
        (AnyN(2), 3, Decimal(20)),
    ]
    ipr_bp = by_title[QP_IPR_TITLE]  # any 5 of 7 x 3, any 3 of 4 x 10, Q12 or Q13
    assert [(s.rule, len(s.items), s.max_marks) for s in ipr_bp.sections] == [
        (AnyN(5), 7, Decimal(15)),
        (AnyN(3), 4, Decimal(30)),
        (AllOf(), 1, Decimal(15)),
    ]
    either = ipr_bp.sections[2].items[0]
    assert isinstance(either, OrGroup)
    assert [(a.label, [(p.label, p.marks) for p in a.parts]) for a in either.alternatives] == [
        ("12", [("a", Decimal(10)), ("b", Decimal(5))]),
        ("13", [("a", Decimal(10)), ("b", Decimal(5))]),
    ]


def test_seeded_documents_pass_the_public_validator() -> None:
    from uuid import uuid4

    from tarn_core.ids import QuestionId, SubjectId
    from tarn_core.seed.seeder import resolve_document

    for college in COLLEGES:
        for paper in college.papers:
            codes = {q.code: QuestionId(uuid4()) for _, q in college.questions}
            document = resolve_document(paper, SubjectId(uuid4()), codes)
            report = check_document(document)
            assert report.valid, (paper.title, report.issues)
            assert report.unlinked == ()


def test_every_unkeyed_question_has_a_synthetic_key(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
) -> None:
    """QP-CI, QP-IPR and Assignment 1 and 2 have no faculty key: their keys are labelled."""
    mem, _, _ = seeded
    unkeyed = [
        *constitution.QUESTIONS,
        *ipr.QUESTIONS,
        *assignments.QUESTIONS_EL,
        *assignments.QUESTIONS_SU,
    ]
    by_code = {q.code: q for q in mem.content.latest(Question)}
    for seed in unkeyed:
        answers = mem.content.for_question(ReferenceAnswer, by_code[seed.code].id)
        assert answers, seed.code
        assert all(a.synthetic for a in answers), seed.code
        assert not any(a.guidance_only for a in answers), seed.code
        criteria = mem.content.for_question(RubricCriterion, by_code[seed.code].id)
        assert sum((c.weight for c in criteria), Decimal(0)) == by_code[seed.code].max_marks
    # faculty keys are not labelled, and a key that only gives guidance is marked by hand
    for seed in aiml.QUESTIONS:
        answers = mem.content.for_question(ReferenceAnswer, by_code[seed.code].id)
        assert [a.synthetic for a in answers].count(True) <= 1
    q4 = mem.content.for_question(ReferenceAnswer, by_code["K-AI2-Q4"].id)
    assert [a.guidance_only for a in q4] == [True]
    assert not mem.content.for_question(RubricCriterion, by_code["K-AI2-Q4"].id)


def test_assignment_1_question_2_names_four_countries() -> None:
    text = assignments.QUESTIONS_EL[1].text
    for country in ("India", "Korea", "Japan", "China"):
        assert country in text


def test_aiml_keys_carry_the_step_marks_of_the_keys() -> None:
    marks = {q.code: q.marks for q in aiml.QUESTIONS}
    assert marks["K-AI1-Q1"] == Decimal(10)  # 5 + 5
    assert marks["K-AI1-Q2A"] == Decimal(5)  # 3 diagram + 2 text
    assert marks["K-AI1-Q2B"] == Decimal(5)  # 3 hidden + 2 output
    assert marks["K-AI1-Q3"] == Decimal(10)  # 5 per iteration
    assert marks["K-AI2-Q2"] == Decimal(10)  # 6 + 1 + 3
    assert marks["K-AI3-Q3B"] == Decimal(6)  # 6 x 1
    q1 = next(q for q in aiml.QUESTIONS if q.code == "K-AI1-Q1")
    assert [c.weight for c in q1.criteria][-2:] == [Decimal(3), Decimal(2)]


def test_reference_diagrams_come_from_k_ai1_and_k_ai3(
    seeded: tuple[InMemory, list[SeededCollege], SeedReport],
) -> None:
    _, _, report = seeded
    assert report.created["reference diagrams"] == 7
    files = {d.file for q in aiml.QUESTIONS for d in q.diagrams}
    assert files == {
        "k-ai1-q2a-unit.png",
        "k-ai1-q2b-network.png",
        "k-ai1-q5-dendrogram.png",
        "k-ai1-q7a-vector-form.png",
        "k-ai1-q8a-rnn.png",
        "k-ai3-q4-model-based-agent.png",
        "k-ai3-q4-utility-based-agent.png",
    }
    # a diagram criterion exists wherever the key marks a drawing
    for seed in aiml.QUESTIONS:
        if seed.diagrams and seed.code != "K-AI1-Q2B":
            assert any(c.type is CriterionType.DIAGRAM for c in seed.criteria), seed.code


# ---- no real student data -------------------------------------------------------------------

# SHA-256 of the lower-cased words of the K-EL cover (a student's name and USN), so that the
# repository does not hold them in order to forbid them.
FORBIDDEN = {
    "926084f942f557ca6056cb1ef7c8fc141527587e5dc14ca6810334d544ed889c",
    "8bf9de8265e3e2e621886ea09a4d9d301cc949e5225e8ba4d6863b2dafa32b03",
    "2ab12f1bbca64aa755f237dbfc067349ed994b3ffc0c7a2a8f023c89e5f0e83f",
}
USN_SHAPES = (
    re.compile(r"\b[A-Za-z]{3}\d{2}[A-Za-z]{2}\d{4}\b"),
    re.compile(r"\b\d[A-Za-z]{2}\d{2}[A-Za-z]{2,3}\d{3}\b"),
)


def _all_text() -> Iterator[str]:
    for college in COLLEGES:
        yield college.name
        for s in college.students:
            yield s.name
            yield s.usn
        for q in (q for _, q in college.questions):
            yield q.text
            yield q.topic
            yield from q.terms
            for a in q.answers:
                yield a.text
            for c in q.criteria:
                yield c.label
                yield c.statement
                for t in c.items:
                    yield t.term
                    yield from t.synonyms
        for p in college.papers:
            yield repr(p.document)


def test_the_k_el_cover_is_not_in_the_seed() -> None:
    for text in _all_text():
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            assert hashlib.sha256(word.encode()).hexdigest() not in FORBIDDEN
        for shape in USN_SHAPES:
            assert not shape.search(text), text[:60]
    # the cleaned K-EL text starts at its first heading, not at the cover
    answer = assignments.QUESTIONS_EL[0].answers[0].text
    assert answer.startswith("Ethical dilemma")
    assert "ASSIGNMENT" not in answer.upper().split("\n")[0]


def test_roster_is_fictitious() -> None:
    for college in (ENGINEERING, COMMERCE):
        for student in college.students:
            assert student.usn.startswith(("DEMOE", "DEMOC"))
            assert "demo" in student.usn.lower()
