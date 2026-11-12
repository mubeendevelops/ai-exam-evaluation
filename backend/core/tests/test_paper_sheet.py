"""The question paper document (P34, D224) built from a seeded paper: question text and marks,
sections and their rules, OR pairs and sub-parts; never keys, rubrics or reference answers."""

from dataclasses import fields, is_dataclass, replace
from typing import Any

import pytest

from tarn_core.domain.blueprint import ExamBlueprint, OrGroup, QuestionSlot, leaves
from tarn_core.domain.content import Question, ReferenceAnswer, RubricCriterion
from tarn_core.domain.paper_sheet import PaperChoice, PaperDocument, PaperQuestion
from tarn_core.errors import NotFoundError
from tarn_core.services.access import Reach
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.services.paper_sheet import PaperSheets
from tarn_core.testing import InMemory
from tarn_core.testing.blueprint_list_contract import schema_document
from tarn_core.testing.builders import make_services
from tarn_core.testing.seed_world import seed_in_memory, seeded_blueprints, seeded_college_ids
from tarn_core.testing.sheets import FakePaperRenderer

IPR = "QP-IPR Intellectual Property Rights, July 2021"
EVERYONE = Reach(None)


def strings(value: Any) -> list[str]:
    """Every text in a document, however deep."""
    if isinstance(value, str):
        return [value]
    if is_dataclass(value) and not isinstance(value, type):
        return [s for f in fields(value) for s in strings(getattr(value, f.name))]
    if isinstance(value, tuple):
        return [s for v in value for s in strings(v)]
    return []


@pytest.fixture(scope="module")
def world() -> tuple[InMemory, ExamBlueprint]:
    mem = InMemory()
    seed_in_memory(mem)
    return mem, next(b for b in seeded_blueprints(mem) if b.title == IPR)


def sheets(mem: InMemory) -> PaperSheets:
    return PaperSheets(content=mem.content, colleges=mem.colleges, renderer=FakePaperRenderer())


def test_the_document_shows_the_paper_and_its_questions(
    world: tuple[InMemory, ExamBlueprint],
) -> None:
    mem, paper = world
    cid = paper.meta.college_id
    doc = sheets(mem).document(cid, paper.id, paper.meta.version, reach=EVERYONE)
    assert doc.title == IPR and doc.exam_code == "QP-IPR-2021-07"
    assert doc.college_name == mem.colleges.get(cid).name
    assert doc.subject_name and doc.total_marks == paper.total_marks and doc.unlinked == 0
    assert [s.label for s in doc.sections] == [s.label for s in paper.sections]
    for section, original in zip(doc.sections, paper.sections, strict=True):
        assert section.rule.startswith("Answer ")
        assert len(section.items) == len(original.items)
    # Every linked question's text is on the paper.
    texts = set(strings(doc))
    for slot in paper.slots():
        for _, question_id, _ in leaves(slot):
            assert question_id is not None
            assert mem.content.get(cid, Question, question_id).text in texts
    choices = [i for s in doc.sections for i in s.items if isinstance(i, PaperChoice)]
    assert choices and all(len(c.alternatives) == 2 for c in choices)
    split = [
        q
        for s in doc.sections
        for i in s.items
        for q in (i.alternatives if isinstance(i, PaperChoice) else (i,))
        if q.parts
    ]
    assert split and all(q.text is None for q in split)
    assert all(sum(p.marks for p in q.parts) == q.marks for q in split)


def test_no_key_rubric_or_reference_answer_reaches_the_document(
    world: tuple[InMemory, ExamBlueprint],
) -> None:
    mem, paper = world
    cid = paper.meta.college_id
    doc = sheets(mem).document(cid, paper.id, paper.meta.version, reach=EVERYONE)
    texts = "\n".join(strings(doc))
    secrets: list[str] = []
    for slot in paper.slots():
        for _, question_id, _ in leaves(slot):
            assert question_id is not None
            for answer in mem.content.for_question(cid, ReferenceAnswer, question_id):
                secrets.append(answer.text)
            for criterion in mem.content.for_question(cid, RubricCriterion, question_id):
                secrets.append(criterion.label)
    secrets = [s for s in secrets if len(s) > 12]
    assert secrets, "the seeded paper has keys to leave out"
    assert not [s for s in secrets if s in texts]
    # The type itself has no place for them.
    names = {f.name for f in fields(PaperDocument)} | {f.name for f in fields(PaperQuestion)}
    assert not names & {"key", "keys", "rubric", "reference_answer", "answer", "criteria"}


def test_an_unlinked_draft_and_the_pdf(world: tuple[InMemory, ExamBlueprint]) -> None:
    mem, paper = world
    cid = paper.meta.college_id
    sme = paper.meta.created_by
    svc = make_services(mem).blueprints
    schema = svc.create(cid, sme, schema_document(paper.subject_id, "Sheet matrix"))
    draft = svc.generate_paper(cid, sme, schema.id)
    renderer = FakePaperRenderer()
    builder = PaperSheets(content=mem.content, colleges=mem.colleges, renderer=renderer)
    doc, pdf = builder.pdf(cid, draft.id, 1, reach=EVERYONE)
    assert pdf.startswith(b"%PDF") and renderer.documents == [doc]
    assert doc.unlinked == 5
    items = [q for s in doc.sections for q in s.items]
    assert all(isinstance(q, PaperQuestion) and q.text is None for q in items)


def test_out_of_reach_or_not_a_paper_is_not_found(world: tuple[InMemory, ExamBlueprint]) -> None:
    mem, paper = world
    cid = paper.meta.college_id
    builder = sheets(mem)
    with pytest.raises(NotFoundError):
        builder.document(cid, paper.id, paper.meta.version, reach=Reach(frozenset()))
    with pytest.raises(NotFoundError):
        builder.document(cid, paper.id, paper.meta.version + 5, reach=EVERYONE)
    other = next(c for c in seeded_college_ids(mem) if c != cid)
    with pytest.raises(NotFoundError):  # this college's paper, asked for from another
        builder.document(other, paper.id, 1, reach=EVERYONE)
    sme = paper.meta.created_by
    schema = make_services(mem).blueprints.create(
        cid, sme, schema_document(paper.subject_id, "Not a paper")
    )
    with pytest.raises(NotFoundError):
        builder.document(cid, schema.id, 1, reach=EVERYONE)


def test_the_newest_question_text_is_printed(world: tuple[InMemory, ExamBlueprint]) -> None:
    mem, paper = world
    cid = paper.meta.college_id
    slot = next(s for s in paper.slots() if s.question_id is not None)
    assert slot.question_id is not None
    question = mem.content.get(cid, Question, slot.question_id)
    mem.content.save(
        cid,
        replace(
            question,
            meta=replace(question.meta, version=question.meta.version + 1),
            text="Explain the εr and μr of a medium — newer wording.",
        ),
    )
    doc = sheets(mem).document(cid, paper.id, paper.meta.version, reach=EVERYONE)
    assert "Explain the εr and μr of a medium — newer wording." in strings(doc)
    assert blueprint_to_document(paper)  # the paper itself did not change


def test_a_question_of_another_subject_is_never_printed(
    world: tuple[InMemory, ExamBlueprint],
) -> None:
    """F21 (P38): a version saved before D234 may link another subject's question; the PDF
    treats that slot as unlinked, so an SME of the paper's subject never reads its text."""
    mem, paper = world
    cid = paper.meta.college_id
    foreign = next(q for q in mem.content.latest(cid, Question) if q.subject_id != paper.subject_id)
    slot = next(s for s in paper.slots() if s.question_id is not None and not s.parts)

    def relink(item: Any) -> Any:
        if isinstance(item, QuestionSlot):
            return replace(item, question_id=foreign.id) if item == slot else item
        if isinstance(item, OrGroup):
            return replace(item, alternatives=tuple(relink(a) for a in item.alternatives))
        return item

    sections = tuple(replace(s, items=tuple(relink(i) for i in s.items)) for s in paper.sections)
    assert sections != paper.sections
    newest = mem.content.get(cid, ExamBlueprint, paper.id).meta.version
    hand_saved = replace(paper, meta=replace(paper.meta, version=newest + 1), sections=sections)
    mem.content.save(cid, hand_saved)
    doc = sheets(mem).document(cid, paper.id, hand_saved.meta.version, reach=EVERYONE)
    assert foreign.text not in strings(doc)
