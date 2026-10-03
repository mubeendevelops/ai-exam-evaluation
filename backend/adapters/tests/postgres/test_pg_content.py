"""Global content in PostgreSQL: owner edits make versions; other colleges copy (decision 3)."""

from decimal import Decimal

import pytest

from tarn_adapters.postgres.testing import Opener, World
from tarn_core.domain.common import global_blob_key
from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    DiagramParams,
    Glossary,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
)
from tarn_core.domain.diagram import DiagramGraph, DiagramNode, NodeShape
from tarn_core.errors import InvariantError, NotOwnerError
from tarn_core.ids import GlossaryId, QuestionId, ReferenceAnswerId, ReferenceDiagramId
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration


def _first_question(world: World) -> QuestionId:
    slot = next(world.blueprint.slots())
    assert slot.question_id is not None
    return slot.question_id


def test_copy_on_edit_for_another_college(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    qid = _first_question(world)

    # A's question gets a key, a glossary and a reference diagram before B copies it.
    with session(a.id) as s:
        meta = ContentMeta(owning_college_id=a.id, created_by=a.college.teacher.id)
        s.content.save(
            ReferenceAnswer(
                id=ReferenceAnswerId(s.ids.new()),
                meta=meta,
                question_id=qid,
                text="Alpha and beta.",
                synthetic=True,
            )
        )
        s.content.save(
            Glossary(
                id=GlossaryId(s.ids.new()), meta=meta, question_id=qid, teacher_terms=("alpha",)
            )
        )
        diagram = ReferenceDiagram(
            id=ReferenceDiagramId(s.ids.new()),
            meta=meta,
            question_id=qid,
            png=global_blob_key("diagrams", "synthetic.png"),
            graph=DiagramGraph(nodes=(DiagramNode(id="n", shape=NodeShape.BLOCK, label="A"),)),
        )
        s.content.save(diagram)

    with session(b.id) as s:
        svc = make_services(s)
        with pytest.raises(NotOwnerError):
            svc.content.edit_question(b.id, b.college.teacher.id, qid, text="B's wording")
        # The repository (row-level security) refuses too, without the service's check.
        original = s.content.get(Question, qid)
        with pytest.raises(NotOwnerError):
            s.content.save(
                Question(
                    id=qid,
                    meta=ContentMeta(
                        version=2, owning_college_id=a.id, created_by=b.college.teacher.id
                    ),
                    subject_id=original.subject_id,
                    text="B's wording",
                    max_marks=original.max_marks,
                )
            )
        copy = svc.content.copy_question(b.id, b.college.teacher.id, qid)

    with session(a.id) as s:
        # A sees B's copy (global visibility) with its provenance, but cannot edit it.
        got = s.content.get(Question, copy.id)
        assert got == copy
        assert got.meta.owning_college_id == b.id
        assert got.meta.copied_from == original.ref
        with pytest.raises(NotOwnerError):
            make_services(s).content.edit_question(a.id, a.college.teacher.id, copy.id, text="x")
        # Keys, rubric, glossary and diagrams came along, owned by B, pointing at the copy.
        parts: list[ReferenceAnswer | RubricCriterion | Glossary | ReferenceDiagram] = [
            *s.content.for_question(ReferenceAnswer, copy.id),
            *s.content.for_question(RubricCriterion, copy.id),
            *s.content.for_question(Glossary, copy.id),
            *s.content.for_question(ReferenceDiagram, copy.id),
        ]
        assert len(parts) == 5  # key, 2 criteria, glossary, diagram
        assert all(p.meta.owning_college_id == b.id for p in parts)
        assert all(p.meta.copied_from is not None for p in parts)
        assert s.content.for_question(Glossary, qid)[0].teacher_terms == ("alpha",)

        # A's own edit makes version 2; version 1 stays for the scores that used it.
        edited = make_services(s).content.edit_question(
            a.id, a.college.teacher.id, qid, text="Better wording", max_marks=Decimal(2)
        )
        assert edited.meta.version == 2
        assert [q.text for q in s.content.versions(Question, qid)] == [original.text, edited.text]
        assert s.content.get(Question, qid, 1) == original
        assert s.content.get(Question, qid) == edited


def test_versions_are_contiguous(session: Opener, world: World) -> None:
    a = world.a
    qid = _first_question(world)
    with session(a.id) as s:
        criterion = next(
            c for c in s.content.for_question(RubricCriterion, qid) if c.type is CriterionType.LIST
        )
        meta = criterion.meta
        with pytest.raises(InvariantError):  # skips version 2
            s.content.save(
                RubricCriterion(
                    id=criterion.id,
                    meta=ContentMeta(version=3, owning_college_id=a.id, created_by=meta.created_by),
                    question_id=qid,
                    label="Skipped",
                    type=criterion.type,
                    weight=criterion.weight,
                    params=criterion.params,
                )
            )
        with pytest.raises(InvariantError):  # version 1 again
            s.content.save(criterion)
        # A diagram criterion's params round-trip.
        diagram_id = ReferenceDiagramId(s.ids.new())
        s.content.save(
            ReferenceDiagram(
                id=diagram_id,
                meta=ContentMeta(owning_college_id=a.id, created_by=meta.created_by),
                question_id=qid,
                png=global_blob_key("diagrams", "d.png"),
            )
        )
        v2 = RubricCriterion(
            id=criterion.id,
            meta=ContentMeta(version=2, owning_college_id=a.id, created_by=meta.created_by),
            question_id=qid,
            label="Draws the diagram",
            type=CriterionType.DIAGRAM,
            weight=criterion.weight,
            params=DiagramParams(reference_diagram_id=diagram_id),
        )
        s.content.save(v2)
        assert s.content.get(RubricCriterion, criterion.id) == v2
        assert s.content.versions(RubricCriterion, criterion.id) == [criterion, v2]
