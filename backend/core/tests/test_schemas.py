"""Schemas and papers (P33, D219, D220, D231): document 1.2, the schema rules, kind defaults and
immutability, ``from_schema``, clone, generate, the lists and units refusing a schema."""

import copy
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import (
    BlueprintKind,
    ExamBlueprint,
    QuestionSlot,
    Section,
)
from tarn_core.domain.content import ContentMeta, Subject
from tarn_core.errors import (
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
)
from tarn_core.ids import BlueprintId, CollegeId, QuestionId, SubjectId, UserId
from tarn_core.services.access import Reach
from tarn_core.services.blueprint_document import (
    SCHEMA_VERSION,
    blueprint_to_document,
    check_document,
)
from tarn_core.services.units import paper_problem
from tarn_core.testing import InMemory
from tarn_core.testing.blueprint_list_contract import build, check, check_isolated, schema_document
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    add_subject,
    ci_shaped_blueprint,
    make_services,
)

SUBJECT = "00000000-0000-4000-8000-000000000001"
LINK = "00000000-0000-4000-8000-0000000000aa"


def doc(**changes: Any) -> dict[str, Any]:
    out = schema_document(SubjectId(UUID(SUBJECT)), "Matrix", target_batch="Class 12")
    out.update(changes)
    return out


def issues(document: dict[str, Any]) -> dict[str, str]:
    return {i.path: i.message for i in check_document(document).issues}


# --- the document ------------------------------------------------------------------------------


def test_document_1_2_round_trips_every_new_field() -> None:
    document = doc(
        weightage_note="  40% recall, 60% analysis ",
        description="Card text",
        descriptor="Mid-term",
    )
    report = check_document(document)
    assert report.valid, report.issues
    assert report.parsed is not None and report.parsed.kind is BlueprintKind.SCHEMA
    meta = ContentMeta(college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
    built = report.parsed.build(id=BlueprintId(UUID(int=3)), meta=meta)
    assert built.weightage_note == "40% recall, 60% analysis"  # trimmed
    back = blueprint_to_document(built)
    assert back["schema_version"] == SCHEMA_VERSION == "1.2"
    assert {k: back[k] for k in ("kind", "target_batch", "descriptor", "from_schema")} == {
        "kind": "schema",
        "target_batch": "Class 12",
        "descriptor": "Mid-term",
        "from_schema": None,
    }
    again = check_document(back).parsed
    assert again is not None and again.build(id=built.id, meta=meta) == built


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_older_documents_are_still_read_as_papers(version: str) -> None:
    newer = {"kind", "target_batch", "weightage_note"}
    document = {k: v for k, v in doc().items() if k not in newer}
    document["schema_version"] = version
    document["sections"][0]["items"][0]["question_id"] = LINK  # a paper may link
    if version == "1.1":
        document["exam_code"] = "OLD-1"
    report = check_document(document)
    assert report.valid, report.issues
    assert report.parsed is not None and report.parsed.kind is None
    meta = ContentMeta(college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
    built = report.parsed.build(id=BlueprintId(UUID(int=3)), meta=meta)
    assert built.kind is BlueprintKind.PAPER and built.target_batch == ""


def test_a_schema_has_no_exam_code_no_links_and_no_origin() -> None:
    linked = doc(exam_code="X-1", from_schema=LINK)
    linked["sections"][0]["items"][1]["question_id"] = LINK
    linked["sections"][0]["items"][2] = {
        "type": "question",
        "label": "3",
        "marks": 2,
        "parts": [{"label": "a", "marks": 2, "question_id": LINK}],
    }
    found = issues(linked)
    assert "no exam code" in found["exam_code"]
    assert "Only a paper" in found["from_schema"]
    assert "unlinked" in found["sections[0].items[1].question_id"]
    assert "unlinked" in found["sections[0].items[2].parts[0].question_id"]
    # The same document as a paper is fine.
    assert issues({**linked, "kind": "paper"}) == {}


@pytest.mark.parametrize(
    ("change", "path", "text"),
    [
        ({"kind": "template"}, "kind", '"schema" or "paper"'),
        ({"target_batch": 2024}, "target_batch", "must be text"),
        ({"target_batch": "x" * 101}, "target_batch", "100 characters"),
        ({"descriptor": "x" * 51}, "descriptor", "50 characters"),
        ({"weightage_note": "x" * 1001}, "weightage_note", "1000 characters"),
        ({"description": "x" * 1001}, "description", "1000 characters"),
        ({"from_schema": 7}, "from_schema", "UUID"),
        ({"from_schema": "nope"}, "from_schema", "valid id"),
        ({"schema_version": "1.3"}, "schema_version", '"1.2"'),
    ],
)
def test_header_problems_of_1_2(change: dict[str, Any], path: str, text: str) -> None:
    assert text in issues(doc(**change))[path]


def test_the_domain_guards_a_schema_too() -> None:
    meta = ContentMeta(college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
    base: dict[str, Any] = {
        "id": BlueprintId(UUID(int=3)),
        "meta": meta,
        "subject_id": SubjectId(UUID(SUBJECT)),
        "title": "S",
        "total_marks": Decimal(2),
        "kind": BlueprintKind.SCHEMA,
    }
    linked = QuestionSlot(label="1", marks=Decimal(2), question_id=QuestionId(UUID(LINK)))
    with pytest.raises(InvariantError, match="unlinked"):
        ExamBlueprint(**base, sections=(Section(label="A", items=(linked,)),))
    plain = (Section(label="A", items=(QuestionSlot(label="1", marks=Decimal(2)),)),)
    with pytest.raises(InvariantError, match="exam code"):
        ExamBlueprint(**base, sections=plain, exam_code="X")
    with pytest.raises(InvariantError, match="another schema"):
        ExamBlueprint(**base, sections=plain, from_schema=BlueprintId(UUID(int=9)))
    with pytest.raises(InvariantError, match="trimmed"):
        ExamBlueprint(**base, sections=plain, target_batch=" x")


# --- the service -------------------------------------------------------------------------------


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.a = add_college(self.mem, "A")
        self.b = add_college(self.mem, "B")
        self.paper = ci_shaped_blueprint(self.mem, self.a)
        self.subject = self.paper.subject_id
        self.svc = make_services(self.mem)
        self.schema = self.svc.blueprints.create(
            self.a.id, self.a.teacher.id, schema_document(self.subject, "Matrix")
        )

    @property
    def sme(self) -> UserId:
        return self.a.teacher.id


@pytest.fixture
def w() -> World:
    return World()


def test_create_defaults_to_a_paper_and_stamps_the_save_time(w: World) -> None:
    document = schema_document(w.subject, "Untyped")
    del document["kind"]
    made = w.svc.blueprints.create(w.a.id, w.sme, document)
    assert made.kind is BlueprintKind.PAPER and made.saved_at == w.mem.runtime.clock.now()
    assert w.schema.kind is BlueprintKind.SCHEMA
    assert w.mem.content.get(w.a.id, ExamBlueprint, w.schema.id).saved_at is not None


def test_the_kind_never_changes_and_an_untyped_update_keeps_it(w: World) -> None:
    as_paper = {**schema_document(w.subject, "Matrix"), "kind": "paper"}
    with pytest.raises(InvariantError, match="stays one"):
        w.svc.blueprints.update(w.a.id, w.sme, w.schema.id, as_paper)
    untyped = schema_document(w.subject, "Matrix v2")
    del untyped["kind"]
    edited = w.svc.blueprints.update(w.a.id, w.sme, w.schema.id, untyped)
    assert edited.kind is BlueprintKind.SCHEMA and edited.meta.version == 2


def test_from_schema_must_be_a_schema_of_the_same_subject(w: World) -> None:
    other_subject = add_subject(w.mem, w.a, "SYN999", "Elsewhere")
    elsewhere = w.svc.blueprints.create(
        w.a.id, w.sme, schema_document(other_subject.id, "Elsewhere")
    )
    paper = {**schema_document(w.subject, "Paper"), "kind": "paper"}
    for bad in (elsewhere.id, w.paper.id, BlueprintId(UUID(int=77))):
        with pytest.raises(InvariantError, match="from_schema"):
            w.svc.blueprints.create(w.a.id, w.sme, {**paper, "from_schema": str(bad)})
    made = w.svc.blueprints.create(w.a.id, w.sme, {**paper, "from_schema": str(w.schema.id)})
    assert made.from_schema == w.schema.id
    # Another college's schema is not found either.
    b_subject = add_subject(w.mem, w.b, "SYN101", "B's")
    b_schema = w.svc.blueprints.create(w.b.id, w.b.teacher.id, schema_document(b_subject.id, "B"))
    with pytest.raises(InvariantError, match="from_schema"):
        w.svc.blueprints.create(w.a.id, w.sme, {**paper, "from_schema": str(b_schema.id)})


def test_a_paper_keeps_the_origin_of_a_deleted_schema(w: World) -> None:
    paper = w.svc.blueprints.generate_paper(w.a.id, w.sme, w.schema.id)
    w.svc.deletion.delete_paper(w.a.id, w.sme, w.schema.id)
    document = blueprint_to_document(paper)
    document["title"] = "Renamed"
    edited = w.svc.blueprints.update(w.a.id, w.sme, paper.id, document)
    assert edited.from_schema == w.schema.id  # unchanged: kept (D231)
    # Naming it anew is refused, and a clone drops it.
    other = {**schema_document(w.subject, "P"), "kind": "paper", "from_schema": str(w.schema.id)}
    with pytest.raises(InvariantError, match="from_schema"):
        w.svc.blueprints.create(w.a.id, w.sme, other)
    assert w.svc.blueprints.clone(w.a.id, w.sme, paper.id).from_schema is None


def test_clone_is_a_new_blueprint_with_the_source_content(w: World) -> None:
    edited = w.svc.blueprints.update(
        w.a.id,
        w.sme,
        w.schema.id,
        schema_document(w.subject, "Matrix", target_batch="Class 12", weightage_note="40/60"),
    )
    copy_ = w.svc.blueprints.clone(w.a.id, w.sme, edited.id)
    assert copy_.id != edited.id and copy_.meta.version == 1
    assert copy_.title == "Matrix (copy)" and copy_.kind is BlueprintKind.SCHEMA
    assert (copy_.target_batch, copy_.weightage_note, copy_.sections) == (
        "Class 12",
        "40/60",
        edited.sections,
    )
    named = w.svc.blueprints.clone(
        w.a.id, w.sme, edited.id, title=" Board ", target_batch="", weightage_note=" 50/50 "
    )
    assert (named.title, named.target_batch, named.weightage_note) == ("Board", "", "50/50")
    # A paper's copy has no exam code: it names one paper.
    coded = blueprint_to_document(w.paper)
    coded["exam_code"], coded["course_code"] = "SYN-CI-1", "SYN"
    w.svc.blueprints.update(w.a.id, w.sme, w.paper.id, coded)
    paper_copy = w.svc.blueprints.clone(w.a.id, w.sme, w.paper.id)
    assert paper_copy.exam_code == "" and paper_copy.kind is BlueprintKind.PAPER
    assert paper_copy.unlinked_leaves() == ()  # the links are kept
    long = w.svc.blueprints.clone(w.a.id, w.sme, w.schema.id, title=None)
    assert long.title.endswith(" (copy)")
    with pytest.raises(InvariantError, match="200"):
        w.svc.blueprints.clone(w.a.id, w.sme, w.schema.id, title="x" * 201)
    created = [e for e in w.mem.audit.events if e.action is AuditAction.CONTENT_CREATED]
    assert created[-1].after == {
        "kind": "blueprint",
        "id": str(long.id),
        "version": 1,
        "from": {"kind": "blueprint", "id": str(w.schema.id), "version": 2},
    }


def test_a_long_title_is_shortened_before_the_suffix(w: World) -> None:
    document = schema_document(w.subject, "y" * 200)
    long = w.svc.blueprints.create(w.a.id, w.sme, document)
    assert w.svc.blueprints.clone(w.a.id, w.sme, long.id).title == "y" * 193 + " (copy)"
    assert w.svc.blueprints.generate_paper(w.a.id, w.sme, long.id).title == "y" * 194 + " paper"


def test_generate_makes_an_unlinked_paper_draft_from_a_schema(w: World) -> None:
    schema = w.svc.blueprints.update(
        w.a.id,
        w.sme,
        w.schema.id,
        schema_document(w.subject, "Matrix", target_batch="Class 12", weightage_note="40/60"),
    )
    paper = w.svc.blueprints.generate_paper(w.a.id, w.sme, schema.id)
    assert paper.kind is BlueprintKind.PAPER and paper.from_schema == schema.id
    assert paper.title == "Matrix paper" and paper.exam_code == "" and paper.meta.version == 1
    assert paper.sections == schema.sections and paper.total_marks == schema.total_marks
    assert paper.target_batch == "Class 12" and len(paper.unlinked_leaves()) == 5
    titled = w.svc.blueprints.generate_paper(w.a.id, w.sme, schema.id, title="Mid-term 2026")
    assert titled.title == "Mid-term 2026"
    with pytest.raises(IllegalTransitionError, match="Only a schema"):
        w.svc.blueprints.generate_paper(w.a.id, w.sme, w.paper.id)


def test_clone_and_generate_stay_in_the_smes_subjects_and_college(w: World) -> None:
    other_subject = add_subject(w.mem, w.a, "SYN999", "Elsewhere")
    elsewhere = w.svc.blueprints.create(w.a.id, w.sme, schema_document(other_subject.id, "E"))
    w.mem.entitlements.revoke(w.a.id, w.sme, other_subject.id)
    for act in (w.svc.blueprints.clone, w.svc.blueprints.generate_paper):
        with pytest.raises(NotFoundError):
            act(w.a.id, w.sme, elsewhere.id)  # another subject: 404 (D178)
        with pytest.raises(NotFoundError):
            act(w.b.id, w.b.teacher.id, w.schema.id)  # another college: 404
        with pytest.raises(PermissionDeniedError):
            act(w.a.id, w.a.admin.id, w.schema.id)  # the admin writes no content


def test_units_take_papers_only(w: World) -> None:
    choices = w.svc.units.papers(w.a.id, w.a.admin.id)
    assert w.schema.id not in {c.paper.id for c in choices}
    subject = w.mem.content.get(w.a.id, Subject, w.subject)
    assert "schema" in (paper_problem(w.schema, subject) or "")
    with pytest.raises(InvariantError):
        w.svc.units.create(
            w.a.id,
            w.a.admin.id,
            paper_id=w.schema.id,
            year="2026",
            owner_id=w.sme,
            due_date=w.mem.runtime.clock.now().date(),
        )


def test_schemas_lists_cards_and_batches_in_reach(w: World) -> None:
    w.svc.blueprints.update(
        w.a.id, w.sme, w.schema.id, schema_document(w.subject, "Matrix", target_batch="2024")
    )
    w.svc.blueprints.generate_paper(w.a.id, w.sme, w.schema.id)
    listing = w.svc.blueprints.schemas(w.a.id, reach=Reach(frozenset({w.subject})))
    assert [(c.title, c.used_in) for c in listing.items] == [("Matrix", 1)]
    assert list(listing.batches) == ["2024"]
    assert w.svc.blueprints.schemas(w.a.id, reach=Reach(frozenset())).items == []
    assert w.svc.blueprints.schemas(w.a.id, reach=Reach(None), title=" mat ").items
    assert not w.svc.blueprints.schemas(w.a.id, reach=Reach(None), target_batch="2025").items
    kinds = {b.kind for b in w.svc.blueprints.list(w.a.id, reach=Reach(None))}
    assert kinds == {BlueprintKind.SCHEMA, BlueprintKind.PAPER}
    only = w.svc.blueprints.list(w.a.id, reach=Reach(None), kind=BlueprintKind.SCHEMA)
    assert [b.id for b in only] == [w.schema.id]


def test_the_list_contract_in_memory_and_another_college_sees_nothing() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b: CollegeFixture = add_college(mem, "B")
    scenario = build(mem, a)
    check(mem.content, scenario)
    check_isolated(mem.content, b.id)
    assert b.id in mem.log.colleges and a.id in mem.log.colleges


def test_documents_are_copied_not_shared() -> None:
    document = doc()
    before = copy.deepcopy(document)
    check_document(document)
    assert document == before
