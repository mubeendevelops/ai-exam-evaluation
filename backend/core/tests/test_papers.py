"""Manage Question Papers (P34): the structure lock (D222), the listing, creating papers and
the rule that a link names a question of the paper's subject with the leaf's marks."""

import copy
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from tarn_core.domain.blueprint import BlueprintKind, ExamBlueprint
from tarn_core.domain.content import Question
from tarn_core.errors import (
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
)
from tarn_core.ids import SubjectId, UserId
from tarn_core.services.access import Reach
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.services.blueprint_lock import METADATA_FIELDS, structure_changes
from tarn_core.services.blueprints import STARTER_MARKS, STARTER_QUESTIONS
from tarn_core.testing import InMemory
from tarn_core.testing.blueprint_list_contract import schema_document
from tarn_core.testing.builders import (
    add_college,
    add_question,
    add_subject,
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
        self.subject = self.paper.subject_id
        self.svc = make_services(self.mem)

    @property
    def sme(self) -> UserId:
        return self.a.teacher.id

    @property
    def reach(self) -> Reach:
        return Reach(frozenset({self.subject}))

    def document(self, **header: Any) -> dict[str, Any]:
        out = copy.deepcopy(blueprint_to_document(self.current()))
        out["course_code"] = out["course_code"] or "SYN101"  # the builder's paper has none
        out.update(header)
        return out

    def current(self) -> ExamBlueprint:
        return self.mem.content.get(self.a.id, ExamBlueprint, self.paper.id)

    def save(self, document: dict[str, Any]) -> ExamBlueprint:
        return self.svc.blueprints.update(self.a.id, self.sme, self.paper.id, document)

    def upload(self) -> None:
        """A booklet pinned to the paper's current version."""
        self.svc.booklets.register(
            self.a.id,
            self.sme,
            student_id=self.a.students[0].id,
            unit_id=unit_for(self.mem, self.a.id, self.paper.id),
            file_sha256="ab" * 32,
        )


@pytest.fixture
def w() -> World:
    return World()


def _slot_marks_changed(document: dict[str, Any]) -> dict[str, Any]:
    """Section A: questions 1 and 2 swap places (same marks, so the totals still add up)."""
    items = document["sections"][0]["items"]
    items[0]["label"], items[1]["label"] = items[1]["label"], items[0]["label"]
    return document


# Structural changes that keep the document valid.
STRUCTURAL: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "slot order": _slot_marks_changed,
    "choice rule": lambda d: (
        d["sections"][0].update(choice={"rule": "any", "n": 6}) or d.update(total_marks=52) or d
    ),
    "exam code": lambda d: d.update(exam_code="NEW-CODE") or d,
    "unlink": lambda d: d["sections"][0]["items"][0].update(question_id=None) or d,
    "section title": lambda d: d["sections"][0].update(title="Short answers") or d,
    "mark step": lambda d: d.update(mark_step=1) or d,
    "weightage note": lambda d: d.update(weightage_note="60/40") or d,
}


# --- the structure lock (D222) ----------------------------------------------------------------


@pytest.mark.parametrize("change", sorted(STRUCTURAL))
def test_before_any_upload_the_paper_is_freely_editable(w: World, change: str) -> None:
    edited = w.save(STRUCTURAL[change](w.document()))
    assert edited.meta.version == 2
    assert not w.svc.blueprints.structure_locked(w.a.id, edited)


@pytest.mark.parametrize("change", sorted(STRUCTURAL))
def test_after_an_upload_the_structure_is_locked(w: World, change: str) -> None:
    w.upload()
    assert w.svc.blueprints.structure_locked(w.a.id, w.current())
    with pytest.raises(IllegalTransitionError, match="clone it to change the structure"):
        w.save(STRUCTURAL[change](w.document()))
    assert w.current().meta.version == 1


def test_after_an_upload_every_metadata_field_may_change(w: World) -> None:
    w.upload()
    edited = w.save(
        w.document(
            title="Renamed paper",
            descriptor="Board Final",
            description="Card text",
            target_batch="2026 batch",
            duration_minutes=150,
            course_code="CIV-101",
        )
    )
    assert edited.meta.version == 2 and edited.title == "Renamed paper"
    assert edited.descriptor == "Board Final" and edited.duration_minutes == 150
    assert edited.sections == w.paper.sections
    # An unchanged document is a new version too (nothing structural changed).
    assert w.save(w.document()).meta.version == 3


def test_the_lock_names_exactly_the_metadata_fields() -> None:
    assert set(METADATA_FIELDS) == {
        "title",
        "descriptor",
        "description",
        "target_batch",
        "duration_minutes",
        "course_code",
    }


def test_structure_changes_says_what_changed(w: World) -> None:
    paper = w.current()
    assert structure_changes(paper, replace(paper, title="Other", course_code="X")) == ()
    assert structure_changes(paper, replace(paper, exam_code="E-1")) == ("exam code",)
    assert structure_changes(paper, replace(paper, mark_step=Decimal(1))) == ("mark step",)


def test_a_schema_is_never_locked(w: World) -> None:
    schema = w.svc.blueprints.create(w.a.id, w.sme, schema_document(w.subject, "Matrix"))
    assert not w.svc.blueprints.structure_locked(w.a.id, schema)


# --- links must agree --------------------------------------------------------------------------


def test_a_link_must_carry_the_slots_marks_and_subject(w: World) -> None:
    two = add_question(w.mem.content, w.a, w.mem.ids.new(), w.subject, Decimal(2))
    other = add_subject(w.mem, w.a, "SYN777", "Elsewhere")
    foreign = add_question(w.mem.content, w.a, w.mem.ids.new(), other.id, Decimal(5))
    document = w.document()
    slot_b = document["sections"][1]["items"][0]  # a 5-mark slot
    slot_b["question_id"] = str(two.id)
    with pytest.raises(InvariantError, match="carries 2 marks, the slot 5; they must agree"):
        w.save(document)
    slot_b["question_id"] = str(foreign.id)
    with pytest.raises(InvariantError, match="of another subject"):
        w.save(document)
    five = add_question(w.mem.content, w.a, w.mem.ids.new(), w.subject, Decimal(5))
    slot_b["question_id"] = str(five.id)
    assert w.save(document).slot("8").question_id == five.id


# --- the listing -------------------------------------------------------------------------------


def test_papers_lists_a_page_with_descriptors_and_units_in_use(w: World) -> None:
    made = [
        w.svc.blueprints.create_paper(
            w.a.id, w.sme, title=f"Paper {n}", subject_id=w.subject, descriptor=tag
        )
        for n, tag in enumerate(("Mid-term", "Board Final", "Mid-term", ""))
    ]
    unit_for(w.mem, w.a.id, w.paper.id)
    page = w.svc.blueprints.papers(w.a.id, reach=w.reach, limit=3)
    assert page.total == 5 and len(page.items) == 3
    assert list(page.descriptors) == ["Board Final", "Mid-term"]
    everything = w.svc.blueprints.papers(w.a.id, reach=w.reach, limit=10)
    assert everything.in_use == frozenset({w.paper.id})
    found = w.svc.blueprints.papers(w.a.id, reach=w.reach, search="mid-")
    assert {p.id for p in found.items} == {made[0].id, made[2].id}
    exact = w.svc.blueprints.papers(w.a.id, reach=w.reach, descriptor="board final")
    assert [p.id for p in exact.items] == [made[1].id]
    today = w.mem.runtime.clock.now().date()
    # The builder's paper was saved without a save time; the four made here match today.
    assert w.svc.blueprints.papers(w.a.id, reach=w.reach, created_on=today).total == 4
    # Schemas are not papers; another subject's papers are out of reach.
    w.svc.blueprints.create(w.a.id, w.sme, schema_document(w.subject, "Matrix"))
    assert w.svc.blueprints.papers(w.a.id, reach=w.reach, limit=10).total == 5
    nobody = w.svc.blueprints.papers(w.a.id, reach=Reach(frozenset({SubjectId(w.b.id)})))
    assert (nobody.total, list(nobody.descriptors)) == (0, [])
    assert w.svc.blueprints.papers(w.b.id, reach=Reach(None)).total == 0


# --- creating ----------------------------------------------------------------------------------


def test_create_paper_starts_from_a_starter_or_a_schema(w: World) -> None:
    starter = w.svc.blueprints.create_paper(
        w.a.id, w.sme, title="  Mid term 2026  ", subject_id=w.subject, descriptor=" Mid-term "
    )
    assert starter.kind is BlueprintKind.PAPER and starter.title == "Mid term 2026"
    assert starter.descriptor == "Mid-term" and starter.course_code == "SYN101"
    assert len(starter.unlinked_leaves()) == STARTER_QUESTIONS
    assert starter.total_marks == STARTER_MARKS * STARTER_QUESTIONS
    schema = w.svc.blueprints.create(w.a.id, w.sme, schema_document(w.subject, "Matrix"))
    paper = w.svc.blueprints.create_paper(
        w.a.id, w.sme, title="From matrix", subject_id=w.subject, schema_id=schema.id
    )
    assert paper.from_schema == schema.id and paper.title == "From matrix"
    assert paper.sections == schema.sections
    with pytest.raises(InvariantError, match="name"):
        w.svc.blueprints.create_paper(w.a.id, w.sme, title=" ", subject_id=w.subject)


def test_create_paper_stays_in_the_smes_subjects_and_college(w: World) -> None:
    other = add_subject(w.mem, w.a, "SYN888", "Other")
    schema = w.svc.blueprints.create(w.a.id, w.sme, schema_document(other.id, "Other matrix"))
    with pytest.raises(InvariantError, match="another subject"):
        w.svc.blueprints.create_paper(
            w.a.id, w.sme, title="X", subject_id=w.subject, schema_id=schema.id
        )
    w.mem.entitlements.revoke(w.a.id, w.sme, other.id)
    with pytest.raises(NotFoundError):
        w.svc.blueprints.create_paper(w.a.id, w.sme, title="X", subject_id=other.id)
    with pytest.raises(NotFoundError):  # the schema's subject is no longer the SME's
        w.svc.blueprints.create_paper(
            w.a.id, w.sme, title="X", subject_id=w.subject, schema_id=schema.id
        )
    with pytest.raises(InvariantError, match="no such subject"):  # another college's subject
        w.svc.blueprints.create_paper(w.b.id, w.b.teacher.id, title="X", subject_id=w.subject)
    with pytest.raises(PermissionDeniedError):
        w.svc.blueprints.create_paper(w.a.id, w.a.admin.id, title="X", subject_id=w.subject)


def test_clone_and_generate_take_a_descriptor(w: World) -> None:
    copy_ = w.svc.blueprints.clone(w.a.id, w.sme, w.paper.id, descriptor=" Board Final ")
    assert copy_.descriptor == "Board Final" and copy_.exam_code == ""
    schema = w.svc.blueprints.create(w.a.id, w.sme, schema_document(w.subject, "Matrix"))
    paper = w.svc.blueprints.generate_paper(w.a.id, w.sme, schema.id, descriptor="Unit test")
    assert paper.descriptor == "Unit test"
    # A schema has no descriptor of its own to change.
    assert w.svc.blueprints.clone(w.a.id, w.sme, schema.id, descriptor="x").descriptor == ""


def test_the_question_text_is_never_needed_to_list(w: World) -> None:
    """The cards come from summaries: no question is read."""
    calls: list[object] = []
    original = w.mem.content.get

    def spy(college_id: Any, kind: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(kind)
        return original(college_id, kind, *args, **kwargs)

    w.mem.content.get = spy  # type: ignore[method-assign]
    w.svc.blueprints.papers(w.a.id, reach=w.reach)
    assert Question not in calls
