"""College content in PostgreSQL (D165): private to the college, versioned, and refused by the
repository, row-level security and the composite foreign keys when another college reaches
for it."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_adapters.postgres.testing import Opener, World
from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    DiagramParams,
    Glossary,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
    content_blob_key,
)
from tarn_core.domain.diagram import DiagramGraph, DiagramNode, NodeShape
from tarn_core.errors import InvariantError, NotFoundError, TenantViolationError
from tarn_core.ids import GlossaryId, QuestionId, ReferenceAnswerId, ReferenceDiagramId
from tarn_core.ports.repositories import QuestionQuery
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration


def _first_question(world: World) -> QuestionId:
    slot = next(world.blueprint.slots())
    assert slot.question_id is not None
    return slot.question_id


def test_another_college_finds_nothing_and_writes_nothing(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    qid = _first_question(world)
    with session(a.id) as s:
        original = s.content.get(a.id, Question, qid)

    with session(b.id) as s:
        svc = make_services(s)
        with pytest.raises(NotFoundError):
            s.content.get(b.id, Question, qid)
        assert s.content.versions(b.id, Question, qid) == []
        assert s.content.for_question(b.id, RubricCriterion, qid) == []
        assert qid not in {q.id for q in s.content.latest(b.id, Question)}
        found = s.content.search_questions(b.id, QuestionQuery(code=original.code), limit=5)
        assert found.total == 0
        with pytest.raises(NotFoundError):
            svc.content.edit_question(b.id, b.college.teacher.id, qid, text="B's wording")
        # Asking for A by name through B's session: row-level security still says no.
        with pytest.raises(NotFoundError):
            s.content.get(a.id, Question, qid)
        assert s.content.latest(a.id, Question) == []
        # A's item written through B: refused before any SQL.
        with pytest.raises(TenantViolationError):
            s.content.save(b.id, replace(original, meta=replace(original.meta, version=2)))
        # Claimed as B's: B has no version 1 of it, so version 2 is refused.
        with pytest.raises(InvariantError):
            s.content.save(
                b.id,
                replace(
                    original,
                    meta=ContentMeta(version=2, college_id=b.id, created_by=b.college.teacher.id),
                ),
            )
        # A key of B's that points at A's question: the composite foreign key refuses it.
        with pytest.raises(NotFoundError):
            s.content.save(
                b.id,
                ReferenceAnswer(
                    id=ReferenceAnswerId(s.ids.new()),
                    meta=ContentMeta(college_id=b.id, created_by=b.college.teacher.id),
                    question_id=qid,
                    text="Hijacked key.",
                ),
            )

    with session(a.id) as s:
        assert s.content.versions(a.id, Question, qid) == [original]


def test_edits_make_versions_and_old_ones_stay(session: Opener, world: World) -> None:
    a = world.a
    qid = _first_question(world)
    with session(a.id) as s:
        original = s.content.get(a.id, Question, qid)
        meta = ContentMeta(college_id=a.id, created_by=a.college.teacher.id)
        s.content.save(
            a.id,
            Glossary(
                id=GlossaryId(s.ids.new()), meta=meta, question_id=qid, teacher_terms=("beta",)
            ),
        )
        edited = make_services(s).content.edit_question(
            a.id, a.college.teacher.id, qid, text="Better wording", max_marks=Decimal(2)
        )
        assert edited.meta.version == 2 and edited.meta.college_id == a.id
        assert [q.text for q in s.content.versions(a.id, Question, qid)] == [
            original.text,
            edited.text,
        ]
        assert s.content.get(a.id, Question, qid, 1) == original
        assert s.content.get(a.id, Question, qid) == edited
        terms = {t for g in s.content.for_question(a.id, Glossary, qid) for t in g.teacher_terms}
        assert "beta" in terms


def test_versions_are_contiguous(session: Opener, world: World) -> None:
    a = world.a
    qid = _first_question(world)
    with session(a.id) as s:
        criterion = next(
            c
            for c in s.content.for_question(a.id, RubricCriterion, qid)
            if c.type is CriterionType.LIST
        )
        meta = criterion.meta
        with pytest.raises(InvariantError):  # skips version 2
            s.content.save(
                a.id,
                RubricCriterion(
                    id=criterion.id,
                    meta=ContentMeta(version=3, college_id=a.id, created_by=meta.created_by),
                    question_id=qid,
                    label="Skipped",
                    type=criterion.type,
                    weight=criterion.weight,
                    params=criterion.params,
                ),
            )
        with pytest.raises(InvariantError):  # version 1 again
            s.content.save(a.id, criterion)
        # A diagram criterion's params round-trip.
        diagram_id = ReferenceDiagramId(s.ids.new())
        s.content.save(
            a.id,
            ReferenceDiagram(
                id=diagram_id,
                meta=ContentMeta(college_id=a.id, created_by=meta.created_by),
                question_id=qid,
                png=content_blob_key(a.id, "diagrams", str(qid), str(diagram_id), "d.png"),
                graph=DiagramGraph(nodes=(DiagramNode(id="n", shape=NodeShape.BLOCK, label="A"),)),
            ),
        )
        v2 = RubricCriterion(
            id=criterion.id,
            meta=ContentMeta(version=2, college_id=a.id, created_by=meta.created_by),
            question_id=qid,
            label="Draws the diagram",
            type=CriterionType.DIAGRAM,
            weight=criterion.weight,
            params=DiagramParams(reference_diagram_id=diagram_id),
        )
        s.content.save(a.id, v2)
        assert s.content.get(a.id, RubricCriterion, criterion.id) == v2
        assert s.content.versions(a.id, RubricCriterion, criterion.id) == [criterion, v2]
