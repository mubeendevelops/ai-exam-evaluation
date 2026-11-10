"""Where a question is used (P35, D223): the papers (newest version) that link it and their open
units. Another subject's paper is never listed; another college's question is not found."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.blueprint import ExamBlueprint, QuestionSlot
from tarn_core.domain.content import ContentMeta, Subject
from tarn_core.domain.unit import UnitState
from tarn_core.errors import NotFoundError, PermissionDeniedError
from tarn_core.ids import BlueprintId, QuestionId, SubjectId
from tarn_core.services.question_usage import QuestionUsage
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    add_college,
    add_question,
    ci_shaped_blueprint,
    make_services,
    unit_for,
)


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.a = add_college(self.mem, "A")
        self.b = add_college(self.mem, "B")
        self.paper = ci_shaped_blueprint(self.mem, self.a)
        self.svc = make_services(self.mem)
        self.q1 = QuestionId(self.paper.leaf("1")[1])
        self.q2 = QuestionId(self.paper.leaf("2")[1])

    def usage(self, question_id: QuestionId | None = None) -> QuestionUsage:
        return self.svc.usage.usage(self.a.id, self.a.teacher.id, question_id or self.q1)

    def copy_of_paper(self, title: str, subject_id: SubjectId | None = None) -> ExamBlueprint:
        """The paper again under another id (and title), optionally of another subject."""
        copy = replace(
            self.paper,
            id=BlueprintId(self.mem.ids.new()),
            title=title,
            subject_id=subject_id or self.paper.subject_id,
        )
        self.mem.content.save(self.a.id, copy)
        return copy


@pytest.fixture
def w() -> World:
    return World()


def test_a_question_no_paper_links_has_no_usage(w: World) -> None:
    lone = add_question(w.mem.content, w.a, w.mem.ids.new(), w.paper.subject_id, Decimal(2))
    assert w.usage(lone.id).papers == ()


def test_the_papers_that_link_it_are_listed_by_title_with_their_open_units(w: World) -> None:
    second = w.copy_of_paper("Aardvark paper")
    unit_a = unit_for(w.mem, w.a.id, w.paper.id, year="2024")
    unit_b = unit_for(w.mem, w.a.id, second.id, year="2025")
    found = w.usage().papers
    assert [p.title for p in found] == ["Aardvark paper", "Synthetic paper, CI shape"]
    assert [(u.unit_id, u.year) for u in found[0].open_units] == [(unit_b, "2025")]
    assert [(u.unit_id, u.code) for u in found[1].open_units] == [(unit_a, "U-0001")]


def test_only_open_units_count(w: World) -> None:
    unit_id = unit_for(w.mem, w.a.id, w.paper.id)
    unit = w.mem.units.get(w.a.id, unit_id)
    w.mem.units.save(w.a.id, replace(unit, state=UnitState.SUBMITTED, version=unit.version + 1))
    assert w.usage().papers[0].open_units == ()


def test_only_the_newest_version_of_a_paper_counts(w: World) -> None:
    # Version 2 drops the link to question 1: the paper no longer uses it, version 1 does.
    unlinked = replace(
        w.paper,
        meta=ContentMeta(version=2, college_id=w.a.id, created_by=w.a.teacher.id),
        sections=tuple(
            replace(
                s,
                items=tuple(
                    replace(i, question_id=None)
                    if isinstance(i, QuestionSlot) and i.question_id == w.q1
                    else i
                    for i in s.items
                ),
            )
            for s in w.paper.sections
        ),
    )
    w.mem.content.save(w.a.id, unlinked)
    assert w.usage(w.q1).papers == ()
    assert [p.title for p in w.usage(w.q2).papers] == ["Synthetic paper, CI shape"]


def test_a_paper_of_a_subject_the_sme_does_not_have_is_never_listed(w: World) -> None:
    foreign = Subject(
        id=SubjectId(w.mem.ids.new()),
        meta=ContentMeta(version=1, college_id=w.a.id, created_by=w.a.teacher.id),
        code="OTHER",
        name="Another subject",
    )
    w.mem.content.save(w.a.id, foreign)  # not granted to the SME
    w.copy_of_paper("Hidden paper", foreign.id)
    assert [p.title for p in w.usage().papers] == ["Synthetic paper, CI shape"]


def test_another_college_and_the_admin_are_refused(w: World) -> None:
    with pytest.raises(NotFoundError):
        w.svc.usage.usage(w.b.id, w.b.teacher.id, w.q1)
    with pytest.raises(PermissionDeniedError):
        w.svc.usage.usage(w.a.id, w.a.admin.id, w.q1)
