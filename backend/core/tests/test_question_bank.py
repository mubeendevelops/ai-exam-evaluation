"""The question bank (P7): weights, edit vs copy, uploads, glossary, search filters."""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.common import global_blob_key
from tarn_core.domain.content import (
    CriterionType,
    DiagramComponent,
    DiagramParams,
    Difficulty,
    Glossary,
    KeyFile,
    ListItem,
    ListParams,
    NumericParams,
    ReferenceAnswer,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.domain.diagram import DiagramGraph, DiagramNode, NodeShape
from tarn_core.errors import AlreadyExistsError, InvariantError, NotFoundError, NotOwnerError
from tarn_core.ids import CriterionId
from tarn_core.ports.repositories import QuestionQuery
from tarn_core.services.question_bank import (
    NO_STUDENT_DATA,
    CriterionSpec,
    safe_file_name,
    sniff_media_type,
)
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, make_services

PDF = b"%PDF-1.7\nsynthetic key"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def spec(label: str, weight: str, **kw: Any) -> CriterionSpec:
    kw.setdefault("type", CriterionType.SEMANTIC)
    kw.setdefault("params", SemanticParams(reference_statement=f"{label} statement"))
    return CriterionSpec(label=label, weight=Decimal(weight), **kw)


def list_spec(weight: str, required: int = 2) -> CriterionSpec:
    items = (ListItem(term="alpha", synonyms=("a",)), ListItem(term="beta"), ListItem(term="gamma"))
    return CriterionSpec(
        label="Names the items",
        type=CriterionType.LIST,
        weight=Decimal(weight),
        params=ListParams(items=items, required_count=required),
    )


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.a = add_college(self.mem, "A")
        self.b = add_college(self.mem, "B")
        self.svc = make_services(self.mem)
        self.subject = self.svc.subjects.create(
            self.a.id, self.a.teacher.id, code="PHY", name="Physics"
        )
        self.maths = self.svc.subjects.create(
            self.a.id, self.a.teacher.id, code="MAT", name="Maths"
        )

    def question(self, code: str = "PHY-Q1", marks: str = "4", **kw: Any) -> Any:
        kw.setdefault("text", f"Explain {code}.")
        return self.svc.bank.create_question(
            self.a.id,
            self.a.teacher.id,
            subject_id=kw.pop("subject_id", self.subject.id),
            code=code,
            max_marks=Decimal(marks),
            **kw,
        )

    def upload(self, question: Any, **kw: Any) -> KeyFile:
        kw.setdefault("file_name", "key.pdf")
        kw.setdefault("data", PDF)
        kw.setdefault("declared_type", "application/pdf")
        kw.setdefault("no_student_data", True)
        return self.svc.bank.upload_key_file(self.a.id, self.a.teacher.id, question.id, **kw)


@pytest.fixture
def w() -> World:
    return World()


# --- weights ----------------------------------------------------------------------------------


def test_rubric_weights_must_add_up_to_the_max_marks(w: World) -> None:
    with pytest.raises(InvariantError, match=r"add up to 3\.5 marks, but the question carries 4"):
        w.question(criteria=[list_spec("2"), spec("Explains", "1.5")])
    # nothing was written, not even the question
    assert w.mem.content.search_questions(QuestionQuery(), limit=10).total == 0

    q = w.question(criteria=[list_spec("2"), spec("Explains", "2")])
    assert sorted(c.weight for c in w.mem.content.for_question(RubricCriterion, q.id)) == [2, 2]


def test_all_four_criterion_types_with_their_parameters(w: World) -> None:
    q = w.question(marks="8", text="Compute and draw.")
    diagram = w.svc.bank.upload_reference_diagram(
        w.a.id,
        w.a.teacher.id,
        q.id,
        file_name="flow.png",
        data=PNG,
        declared_type="image/png",
        no_student_data=True,
    )
    specs = [
        list_spec("2", required=2),  # items + synonyms + required count
        CriterionSpec(  # numeric: one step each, expected value and tolerance
            label="Step 1",
            type=CriterionType.NUMERIC,
            weight=Decimal(1),
            params=NumericParams(expected=Decimal("2.5"), tolerance=Decimal("0.1"), unit="V"),
        ),
        CriterionSpec(
            label="Step 2",
            type=CriterionType.NUMERIC,
            weight=Decimal(1),
            params=NumericParams(expected=Decimal(7), tolerance=Decimal(0)),
        ),
        spec("States the law", "1"),
        *(  # diagram: separate weights for nodes, edges and labels
            CriterionSpec(
                label=f"Diagram {c.value}",
                type=CriterionType.DIAGRAM,
                weight=Decimal(w_),
                params=DiagramParams(reference_diagram_id=diagram.id, component=c),
            )
            for c, w_ in (
                (DiagramComponent.NODES, "1"),
                (DiagramComponent.EDGES, "1"),
                (DiagramComponent.LABELS, "1"),
            )
        ),
    ]
    saved = w.svc.bank.set_rubric(w.a.id, w.a.teacher.id, q.id, specs)
    assert len(saved) == 7 and sum(c.weight for c in saved) == 8
    assert {c.type for c in saved} == {
        CriterionType.LIST,
        CriterionType.NUMERIC,
        CriterionType.SEMANTIC,
        CriterionType.DIAGRAM,
    }


def test_bad_parameters_and_foreign_diagrams_are_refused(w: World) -> None:
    q = w.question()
    other = w.question("PHY-Q2")
    foreign = w.svc.bank.upload_reference_diagram(
        w.a.id,
        w.a.teacher.id,
        other.id,
        file_name="d.png",
        data=PNG,
        declared_type=None,
        no_student_data=True,
    )
    with pytest.raises(InvariantError, match="required_count"):
        list_spec("4", required=9)
    with pytest.raises(InvariantError, match="does not belong to this question"):
        w.svc.bank.set_rubric(
            w.a.id,
            w.a.teacher.id,
            q.id,
            [
                CriterionSpec(
                    label="D",
                    type=CriterionType.DIAGRAM,
                    weight=Decimal(4),
                    params=DiagramParams(reference_diagram_id=foreign.id),
                )
            ],
        )
    with pytest.raises(InvariantError, match="not part of this question"):
        w.svc.bank.set_rubric(
            w.a.id,
            w.a.teacher.id,
            q.id,
            [spec("X", "4", id=CriterionId(w.mem.ids.new()))],
        )


def test_editing_the_rubric_versions_changed_criteria_and_retires_removed_ones(w: World) -> None:
    q = w.question(criteria=[list_spec("2"), spec("Explains", "2")])
    first = {c.label: c for c in w.mem.content.for_question(RubricCriterion, q.id)}

    w.svc.bank.set_rubric(
        w.a.id,
        w.a.teacher.id,
        q.id,
        [  # keep "Names the items" unchanged, drop "Explains", add two new ones
            CriterionSpec(
                label="Names the items",
                type=CriterionType.LIST,
                weight=Decimal(2),
                params=first["Names the items"].params,
                id=first["Names the items"].id,
            ),
            spec("Part one", "1"),
            spec("Part two", "1"),
        ],
    )
    live = {c.label: c for c in w.mem.content.for_question(RubricCriterion, q.id)}
    assert set(live) == {"Names the items", "Part one", "Part two"}
    assert live["Names the items"].meta.version == 1  # unchanged: no new version
    history = w.mem.content.versions(RubricCriterion, first["Explains"].id)
    assert [(c.meta.version, c.retired) for c in history] == [(1, False), (2, True)]

    # Changing a weight makes a new version and keeps the old one.
    w.svc.bank.set_rubric(
        w.a.id,
        w.a.teacher.id,
        q.id,
        [
            spec(
                "Names the items",
                "3",
                id=live["Names the items"].id,
                type=CriterionType.LIST,
                params=live["Names the items"].params,
            ),
            spec("Part one", "1", id=live["Part one"].id),
        ],
    )
    again = w.mem.content.versions(RubricCriterion, live["Names the items"].id)
    assert [c.weight for c in again] == [2, 3]


def test_changing_the_marks_needs_the_rubric_with_it(w: World) -> None:
    q = w.question(criteria=[spec("Only", "4")])
    same: dict[str, Any] = {
        "code": q.code,
        "text": q.text,
        "difficulty": q.difficulty,
        "category": "",
    }
    with pytest.raises(InvariantError, match="Change the rubric together with the marks"):
        w.svc.bank.update_question(w.a.id, w.a.teacher.id, q.id, max_marks=Decimal(6), **same)
    live = w.mem.content.for_question(RubricCriterion, q.id)[0]
    edited = w.svc.bank.update_question(
        w.a.id,
        w.a.teacher.id,
        q.id,
        max_marks=Decimal(6),
        **same,
        criteria=[spec("Only", "6", id=live.id)],
    )
    assert edited.max_marks == 6 and edited.meta.version == 2
    # An edit that changes nothing makes no new version.
    unchanged = w.svc.bank.update_question(
        w.a.id, w.a.teacher.id, q.id, max_marks=Decimal(6), **same
    )
    assert unchanged.meta.version == 2


# --- edit vs copy -----------------------------------------------------------------------------


def test_only_the_owner_edits_others_copy_with_everything_attached(w: World) -> None:
    q = w.question(
        reference_answer="The model answer.",
        category="Optics",
        criteria=[list_spec("2"), spec("Explains", "2")],
    )
    w.upload(q, keywords=["lens"])
    w.svc.bank.set_glossary_terms(w.a.id, w.a.teacher.id, q.id, ["focal length"])
    same: dict[str, Any] = {
        "code": q.code,
        "text": "Hijack",
        "max_marks": q.max_marks,
        "difficulty": q.difficulty,
        "category": "",
    }
    b, bank = w.b, w.svc.bank
    with pytest.raises(NotOwnerError):
        bank.update_question(b.id, b.teacher.id, q.id, **same)
    with pytest.raises(NotOwnerError):
        bank.add_reference_answer(b.id, b.teacher.id, q.id, text="mine")
    with pytest.raises(NotOwnerError):
        bank.set_rubric(b.id, b.teacher.id, q.id, [])
    with pytest.raises(NotOwnerError):
        bank.set_glossary_terms(b.id, b.teacher.id, q.id, ["x"])
    with pytest.raises(NotOwnerError):
        w.svc.bank.upload_key_file(
            b.id,
            b.teacher.id,
            q.id,
            file_name="k.pdf",
            data=PDF,
            declared_type=None,
            no_student_data=True,
        )

    copy = w.svc.content.copy_question(b.id, b.teacher.id, q.id)
    assert copy.meta.owning_college_id == b.id and copy.meta.copied_from == q.ref
    assert copy.code == q.code  # B has no such code yet
    detail = bank.detail(copy.id)
    assert [a.text for a in detail.answers] == ["The model answer."]
    assert len(detail.criteria) == 2 and detail.rubric_total == 4
    assert [(f.name, f.keywords) for f in detail.key_files] == [("key.pdf", ("lens",))]
    assert detail.key_files[0].blob == w.mem.content.for_question(KeyFile, q.id)[0].blob
    assert detail.glossary is not None and detail.glossary.teacher_terms == ("focal length",)
    # ... and B now edits its own copy, not A's.
    bank.add_reference_answer(b.id, b.teacher.id, copy.id, text="B's variant")
    assert len(w.mem.content.for_question(ReferenceAnswer, q.id)) == 1


def test_copy_gets_a_free_code_in_the_copying_college(w: World) -> None:
    q = w.question("PHY-Q1")
    mine = w.svc.content.copy_question(w.a.id, w.a.teacher.id, q.id)  # a copy in the same college
    again = w.svc.content.copy_question(w.a.id, w.a.teacher.id, q.id)
    assert (mine.code, again.code) == ("PHY-Q1-2", "PHY-Q1-3")


def test_codes_are_unique_per_college_not_global(w: World) -> None:
    w.question("PHY-Q1")
    with pytest.raises(AlreadyExistsError):
        w.question("phy-q1")  # case-insensitive
    other = w.svc.bank.create_question(
        w.b.id,
        w.b.teacher.id,
        subject_id=w.subject.id,
        code="PHY-Q1",
        text="B's own",
        max_marks=Decimal(2),
    )
    assert other.meta.owning_college_id == w.b.id
    with pytest.raises(InvariantError):
        w.question(" ")
    with pytest.raises(InvariantError, match="no such subject"):
        w.svc.bank.create_question(
            w.a.id,
            w.a.teacher.id,
            subject_id=w.mem.ids.new(),  # type: ignore[arg-type]
            code="X",
            text="t",
            max_marks=Decimal(1),
        )


def test_reference_answers_edit_and_retire(w: World) -> None:
    q = w.question()
    a1 = w.svc.bank.add_reference_answer(w.a.id, w.a.teacher.id, q.id, text="First")
    a2 = w.svc.bank.add_reference_answer(
        w.a.id, w.a.teacher.id, q.id, text="Use judgement", guidance_only=True
    )
    edited = w.svc.bank.edit_reference_answer(
        w.a.id, w.a.teacher.id, q.id, a1.id, text="First, better", guidance_only=False
    )
    assert edited.meta.version == 2
    w.svc.bank.retire_reference_answer(w.a.id, w.a.teacher.id, q.id, a2.id)
    live = w.svc.bank.detail(q.id).answers
    assert [(a.text, a.guidance_only) for a in live] == [("First, better", False)]
    assert [a.retired for a in w.mem.content.versions(ReferenceAnswer, a2.id)] == [False, True]
    with pytest.raises(NotFoundError):
        w.svc.bank.retire_reference_answer(w.a.id, w.a.teacher.id, q.id, a2.id)
    assert w.mem.audit.events[-1].action is AuditAction.CONTENT_RETIRED


# --- uploads ----------------------------------------------------------------------------------


def test_pdf_and_image_keys_are_stored_and_read_back(w: World) -> None:
    q = w.question()
    for name, data, kind in (
        ("k.pdf", PDF, "application/pdf"),
        ("k.png", PNG, "image/png"),
        ("k.jpg", JPEG, "image/jpeg"),
    ):
        kf = w.upload(q, file_name=name, data=data, declared_type=kind)
        assert kf.media_type == kind and kf.size_bytes == len(data)
        stored = w.svc.bank.read_key_file(q.id, kf.id)
        assert stored[1] == data and kf.blob.value.startswith(f"global/keys/{q.id}/")
        assert w.mem.blobs.exists(kf.blob)
    assert w.svc.bank.search(QuestionQuery(), limit=5).items[0].key_count == 3


def test_the_type_is_read_from_the_bytes(w: World) -> None:
    q = w.question()
    with pytest.raises(InvariantError, match="Only PDF, PNG or JPEG"):
        w.upload(q, file_name="key.pdf", data=b"just some text", declared_type="application/pdf")
    with pytest.raises(InvariantError, match="Only PDF, PNG or JPEG"):
        w.upload(q, file_name="key.docx", data=b"PK\x03\x04docx", declared_type=None)
    with pytest.raises(
        InvariantError, match="says it is image/png but its content is application/pdf"
    ):
        w.upload(q, data=PDF, declared_type="image/png")
    assert (
        w.upload(q, data=PDF, declared_type="application/octet-stream").media_type
        == "application/pdf"
    )
    assert w.upload(q, data=PDF, declared_type=None).media_type == "application/pdf"
    assert sniff_media_type(b"") is None


def test_the_no_student_data_confirmation_is_required(w: World) -> None:
    q = w.question()
    with pytest.raises(InvariantError, match="no student data"):
        w.upload(q, no_student_data=False)
    with pytest.raises(InvariantError, match="no student data"):
        w.svc.bank.upload_reference_diagram(
            w.a.id,
            w.a.teacher.id,
            q.id,
            file_name="d.png",
            data=PNG,
            declared_type=None,
            no_student_data=False,
        )
    assert NO_STUDENT_DATA.startswith("Confirm that this file holds no student data")
    assert w.mem.content.for_question(KeyFile, q.id) == []
    assert not w.mem.blobs.exists(global_blob_key("keys", str(q.id)))
    # The record itself cannot exist without the confirmation.
    ok = w.upload(q)
    with pytest.raises(InvariantError):
        replace(ok, no_student_data_confirmed=False)
    assert w.mem.audit.events[-1].after == {
        "kind": "key_file",
        "id": str(ok.id),
        "version": 1,
        "no_student_data_confirmed": True,
    }


def test_size_limits_and_empty_files(w: World) -> None:
    q = w.question()
    with pytest.raises(InvariantError, match="empty"):
        w.upload(q, data=b"")
    with pytest.raises(InvariantError, match="larger than 10 MB"):
        w.upload(q, data=PDF + b"0" * (10 * 1024 * 1024))
    with pytest.raises(InvariantError, match="larger than 5 MB"):
        w.svc.bank.upload_reference_diagram(
            w.a.id,
            w.a.teacher.id,
            q.id,
            file_name="d.png",
            data=PNG + b"0" * (5 * 1024 * 1024),
            declared_type=None,
            no_student_data=True,
        )


def test_reference_diagrams_are_png_only(w: World) -> None:
    q = w.question()
    for data in (PDF, JPEG):
        with pytest.raises(InvariantError, match="Only PNG files"):
            w.svc.bank.upload_reference_diagram(
                w.a.id,
                w.a.teacher.id,
                q.id,
                file_name="d.png",
                data=data,
                declared_type=None,
                no_student_data=True,
            )
    d = w.svc.bank.upload_reference_diagram(
        w.a.id,
        w.a.teacher.id,
        q.id,
        file_name="Flow chart (v2).PNG",
        data=PNG,
        declared_type="image/png",
        no_student_data=True,
    )
    assert d.png.value.endswith("/Flow_chart_v2_.PNG") and d.png.college_id is None
    assert w.svc.bank.read_reference_diagram(q.id, d.id)[1] == PNG


def test_file_names_cannot_escape_their_folder(w: World) -> None:
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name("C:\\keys\\Answer Key 1.pdf") == "Answer_Key_1.pdf"
    assert safe_file_name("..") == "file"
    assert len(safe_file_name("x" * 300)) == 100
    q = w.question()
    kf = w.upload(q, file_name="../../evil name.pdf")
    assert kf.name == "evil_name.pdf" and ".." not in kf.blob.value


def test_keywords_are_cleaned(w: World) -> None:
    q = w.question()
    kf = w.upload(q, keywords=["  Lens ", "lens", "focal   length", "", "Mirror"])
    assert kf.keywords == ("Lens", "focal length", "Mirror")
    with pytest.raises(InvariantError, match="at most 30"):
        w.upload(q, keywords=[f"k{i}" for i in range(31)])


def test_a_failed_save_leaves_no_orphan_blob(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    q = w.question()
    boom = RuntimeError("database down")

    def fail(_item: object) -> None:
        raise boom

    monkeypatch.setattr(w.mem.content, "save", fail)
    with pytest.raises(RuntimeError):
        w.upload(q)
    assert not any(k.value.startswith("global/keys/") for k in w.mem.blobs.keys)


# --- glossary ---------------------------------------------------------------------------------


def test_glossary_is_teacher_terms_plus_reference_diagram_labels(w: World) -> None:
    q = w.question()
    d = w.svc.bank.upload_reference_diagram(
        w.a.id,
        w.a.teacher.id,
        q.id,
        file_name="d.png",
        data=PNG,
        declared_type=None,
        no_student_data=True,
    )
    assert w.svc.bank.detail(q.id).glossary is None  # no terms, and the graph has no labels yet

    # The recognizer / the teacher fills the graph later (P14): a new diagram version.
    graph = DiagramGraph(
        nodes=(
            DiagramNode(id="n1", shape=NodeShape.TERMINAL, label="Start"),
            DiagramNode(id="n2", shape=NodeShape.PROCESS, label="read n"),
        )
    )
    w.mem.content.save(replace(d, meta=replace(d.meta, version=2), graph=graph))
    g = w.svc.bank.set_glossary_terms(
        w.a.id, w.a.teacher.id, q.id, ["Read N", " flowchart ", "flowchart", ""]
    )
    assert isinstance(g, Glossary)
    assert g.teacher_terms == ("Read N", "flowchart") and g.reference_labels == ("Start", "read n")
    assert g.terms == ("Read N", "flowchart", "Start")  # case-insensitive union, first kept
    # Saving the same terms again makes no new version.
    same = w.svc.bank.set_glossary_terms(w.a.id, w.a.teacher.id, q.id, ["Read N", "flowchart"])
    assert same is not None and same.meta.version == 1
    again = w.svc.bank.set_glossary_terms(w.a.id, w.a.teacher.id, q.id, ["Read N"])
    assert again is not None and again.meta.version == 2


# --- search and filters -----------------------------------------------------------------------


def test_filters_topic_keyword_code_subject_difficulty(w: World) -> None:
    w.question(
        "PHY-Q1",
        text="State Lenz's law.",
        category="Electromagnetism",
        difficulty=Difficulty.EASY,
        reference_answer="Induced emf opposes the change.",
    )
    w.question(
        "PHY-Q2", text="Define resonance.", category="AC Circuits", difficulty=Difficulty.HARD
    )
    w.question(
        "MAT-Q1",
        text="Integrate x dx.",
        category="Calculus",
        subject_id=w.maths.id,
        difficulty=Difficulty.EASY,
    )
    w.svc.bank.create_question(
        w.b.id,
        w.b.teacher.id,
        subject_id=w.subject.id,
        code="PHY-B1",
        text="B's question.",
        max_marks=Decimal(1),
        category="Optics",
    )

    def codes(**kw: Any) -> list[str]:
        page = w.svc.bank.search(QuestionQuery(**kw), limit=20)
        assert page.total == len(page.items)
        return [i.question.code for i in page.items]

    assert codes() == ["MAT-Q1", "PHY-B1", "PHY-Q1", "PHY-Q2"]  # ordered by code, all colleges
    assert codes(subject_id=w.subject.id) == ["PHY-B1", "PHY-Q1", "PHY-Q2"]
    assert codes(difficulty=Difficulty.EASY) == ["MAT-Q1", "PHY-Q1"]
    assert codes(difficulty=Difficulty.EASY, subject_id=w.maths.id) == ["MAT-Q1"]
    assert codes(topic="ac circuits") == ["PHY-Q2"]  # exact topic, any case
    assert codes(topic="circuit") == []
    assert codes(code="q1") == ["MAT-Q1", "PHY-Q1"]  # part of the code
    assert codes(code="phy-q1", exact_code=True) == ["PHY-Q1"]
    assert codes(keyword="lenz") == ["PHY-Q1"]  # question text
    assert codes(keyword="opposes") == ["PHY-Q1"]  # reference answer
    assert codes(keyword="electromag") == ["PHY-Q1"]  # topic
    assert codes(keyword="PHY-B") == ["PHY-B1"]  # code
    assert codes(owner_id=w.b.id) == ["PHY-B1"]
    assert codes(keyword="lenz", difficulty=Difficulty.HARD) == []  # filters combine
    assert list(w.svc.bank.topics()) == ["AC Circuits", "Calculus", "Electromagnetism", "Optics"]
    assert list(w.svc.bank.topics(w.maths.id)) == ["Calculus"]


def test_search_pages_names_owners_and_counts_keys(w: World) -> None:
    for i in range(5):
        w.question(f"Q-{i}")
    q = w.question("Q-9", reference_answer="Answer.")
    w.upload(q)
    first = w.svc.bank.search(QuestionQuery(), limit=4)
    rest = w.svc.bank.search(QuestionQuery(), limit=4, offset=4)
    assert (first.total, len(first.items), len(rest.items)) == (6, 4, 2)
    got = {i.question.code: i for i in [*first.items, *rest.items]}
    assert got["Q-9"].key_count == 2 and got["Q-0"].key_count == 0
    assert got["Q-0"].owner_name == "Test College A" and got["Q-0"].subject_name == "Physics"


def test_retired_answers_stop_matching_and_counting(w: World) -> None:
    q = w.question(reference_answer="Unique phrase zebra.")
    assert w.svc.bank.search(QuestionQuery(keyword="zebra"), limit=5).total == 1
    answer = w.svc.bank.detail(q.id).answers[0]
    w.svc.bank.retire_reference_answer(w.a.id, w.a.teacher.id, q.id, answer.id)
    assert w.svc.bank.search(QuestionQuery(keyword="zebra"), limit=5).total == 0
    assert w.svc.bank.search(QuestionQuery(), limit=5).items[0].key_count == 0


def test_scoring_ignores_retired_criteria(w: World) -> None:
    q = w.question(criteria=[spec("A", "2"), spec("B", "2")])
    live = {c.label: c for c in w.mem.content.for_question(RubricCriterion, q.id)}
    w.svc.bank.set_rubric(
        w.a.id,
        w.a.teacher.id,
        q.id,
        [spec("A", "4", id=live["A"].id, type=CriterionType.SEMANTIC, params=live["A"].params)],
    )
    from tarn_core.domain.content import Rubric

    rubric = Rubric(question=q, criteria=tuple(w.mem.content.for_question(RubricCriterion, q.id)))
    assert [c.label for c in rubric.criteria] == ["A"]
