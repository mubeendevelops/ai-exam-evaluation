"""Result sheet PDFs and the evaluated booklets list (P18): the document the sheet shows, one
stored PDF per version (v1 untouched by an amendment), search, tenancy and deletion. The renderer
is a recording fake; the real PDF is read back in the adapter tests."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tarn_core.domain.booklet import AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.common import Box
from tarn_core.domain.diagram import DiagramGraph, StudentDiagram
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import AnswerId, StudentDiagramId
from tarn_core.services.evaluated import EvaluatedBooklets
from tarn_core.services.workflow import LockPolicy, sheet_key
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    ci_shaped_blueprint,
    make_services,
)
from tarn_core.testing.scoring import Line
from tarn_core.testing.workflow import GOOD, HALF, Workflow, scored_booklet, workflow


def approve_all(mem: InMemory, college: CollegeFixture, booklet: Booklet, wf: Workflow) -> None:
    for a in mem.booklets.answers(college.id, booklet.id):
        if a.status is not AnswerStatus.APPROVED:
            wf.review.approve_answer(
                college.id, college.teacher.id, booklet.id, a.id, expected_version=a.version
            )


def approve(mem: InMemory, college: CollegeFixture, booklet: Booklet, wf: Workflow) -> Booklet:
    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    approve_all(mem, college, booklet, wf)
    approved, _ = wf.review.approve_booklet(
        college.id, college.teacher.id, booklet.id, expected_version=opened.booklet.version
    )
    return approved


def setup(
    answers: Mapping[str, Sequence[str | Line]] | None = None,
) -> tuple[InMemory, CollegeFixture, Booklet, Workflow]:
    mem = InMemory()
    college = add_college(mem, "A")
    booklet = scored_booklet(
        mem,
        college,
        ci_shaped_blueprint(mem, college),
        answers or {"1": GOOD, "2": HALF},
    )
    wf = workflow(mem, policy=LockPolicy(timeout=timedelta(minutes=15)))
    return mem, college, booklet, wf


def evaluated(mem: InMemory) -> EvaluatedBooklets:
    return EvaluatedBooklets(
        booklets=mem.booklets,
        students=mem.students,
        content=mem.content,
        sheets=mem.sheets,
        blobs=mem.blobs,
    )


def answer_id(mem: InMemory, booklet: Booklet, label: str) -> AnswerId:
    return next(
        a.id for a in mem.booklets.answers(booklet.college_id, booklet.id) if a.slot_label == label
    )


# --- what the sheet shows ---------------------------------------------------------------------


def test_approval_stores_the_pdf_and_the_sheet_shows_every_field() -> None:
    mem, college, booklet, wf = setup(
        {"1": [*GOOD, Line("struck words", struck_out=True)], "2": HALF}
    )
    opened = wf.review.open(college.id, college.teacher.id, booklet.id)
    two = mem.booklets.get_answer(college.id, answer_id(mem, booklet, "2"))
    wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        two.id,
        expected_version=two.version,
        teacher_mark=Decimal("1.5"),
        tags=["partial"],
        remarks="One item missing.",
    )
    approve_all(mem, college, booklet, wf)
    _, sheet = wf.review.approve_booklet(
        college.id, college.teacher.id, booklet.id, expected_version=opened.booklet.version
    )

    assert sheet.pdf == sheet_key(college.id, booklet.id, 1)
    assert mem.blobs.get(sheet.pdf).startswith(b"%PDF")
    doc = wf.renderer.documents[-1]
    student = college.students[0]
    assert (doc.college_name, doc.exam) == ("Test College A", "Synthetic paper, CI shape")
    assert doc.course == "SYN101 · Synthetic Civics"
    assert (doc.student_name, doc.usn) == (student.name, student.usn)
    assert doc.version == 1 and doc.amendment_note == ""
    assert (doc.total, doc.max_marks) == (sheet.total, Decimal(50))
    assert doc.issued_by == college.teacher.display_name and doc.generated_at == sheet.issued_at
    assert [a.label for a in doc.answers] == ["1", "2"]
    one, second = doc.answers
    assert "alpha and beta" in one.excerpt and "struck words" not in one.excerpt
    assert one.question == "Explain the synthetic concept."
    assert one.counted and one.outcome == "counted" and one.section_label == "A"
    assert one.criteria and sum(c.marks for c in one.criteria) == one.teacher_mark
    assert second.teacher_mark == Decimal("1.5") and second.ai_mark is not None
    assert second.tags == ("partial",) and second.remarks == "One item missing."


def test_answers_not_counted_stay_on_the_sheet() -> None:
    mem, college, booklet, wf = setup(
        {"1": GOOD, "2": GOOD, "3": GOOD, "4": GOOD, "5": GOOD, "6": HALF}
    )
    approve(mem, college, booklet, wf)
    doc = wf.renderer.documents[-1]
    assert [ln.slot_label for ln in doc.not_counted] == ["6"]
    six = next(a for a in doc.answers if a.label == "6")
    assert not six.counted and six.outcome == "not counted: best N"
    assert six.teacher_mark is not None  # marked and shown, just outside the total
    assert doc.total == sum(
        (a.teacher_mark for a in doc.answers if a.counted and a.teacher_mark), Decimal(0)
    )
    unattempted = [ln for ln in doc.lines if ln.reason == "not attempted"]
    assert unattempted and all(ln.mark is None for ln in unattempted)


def test_a_drawing_is_cut_from_its_page() -> None:
    mem, college, booklet, wf = setup()
    one = mem.booklets.get_answer(college.id, answer_id(mem, booklet, "1"))
    segment = next(
        s for s in mem.booklets.segments(college.id, booklet.id) if s.id == one.segment_ids[0]
    )
    mem.booklets.save_diagram(
        college.id,
        StudentDiagram(
            id=StudentDiagramId(mem.ids.new()),
            college_id=college.id,
            booklet_id=booklet.id,
            segment_id=segment.id,
            box=Box(x0=10, y0=20, x1=300, y1=200),
            graph=DiagramGraph(nodes=()),
            region_id=segment.region_ids[0],
        ),
    )
    approve(mem, college, booklet, wf)
    (diagram,) = next(a for a in wf.renderer.documents[-1].answers if a.label == "1").diagrams
    assert diagram.image == b"synthetic page" and diagram.box == Box(x0=10, y0=20, x1=300, y1=200)
    assert diagram.caption.startswith("Drawing 1")
    two = next(a for a in wf.renderer.documents[-1].answers if a.label == "2")
    assert two.diagrams == ()


# --- versions ---------------------------------------------------------------------------------


def test_an_amendment_issues_a_new_pdf_and_leaves_the_first_alone() -> None:
    mem, college, booklet, wf = setup()
    approved = approve(mem, college, booklet, wf)
    v1_key = sheet_key(college.id, booklet.id, 1)
    v1_bytes = mem.blobs.get(v1_key)

    two = mem.booklets.get_answer(college.id, answer_id(mem, booklet, "2"))
    wf.review.reopen(
        college.id,
        college.teacher.id,
        booklet.id,
        two.id,
        expected_version=two.version,
        reason="Recounted",
    )
    wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        two.id,
        expected_version=two.version + 1,
        teacher_mark=Decimal(2),
    )
    sheets = mem.sheets.versions(college.id, booklet.id)
    assert [s.version for s in sheets] == [1, 2]
    assert [s.pdf for s in sheets] == [v1_key, sheet_key(college.id, booklet.id, 2)]
    assert mem.blobs.get(v1_key) == v1_bytes  # v1 is what was issued
    assert mem.blobs.get(sheets[1].pdf) != v1_bytes  # type: ignore[arg-type]
    first, second = wf.renderer.documents[-2:]
    assert (first.version, second.version) == (1, 2)
    assert second.amendment_note == "2: Recounted" and first.amendment_note == ""
    assert {a.label: a.teacher_mark for a in first.answers}["2"] != Decimal(2)
    assert {a.label: a.teacher_mark for a in second.answers}["2"] == Decimal(2)
    assert second.total != first.total
    assert approved.status is BookletStatus.APPROVED

    page = evaluated(mem).search(college.id)
    (item,) = page.items
    assert item.latest.version == 2 and [s.version for s in item.sheets] == [1, 2]
    _, pdf1 = evaluated(mem).pdf(college.id, booklet.id, 1)
    _, pdf2 = evaluated(mem).pdf(college.id, booklet.id, 2)
    assert pdf1 == v1_bytes and pdf2 != pdf1


def test_the_audit_log_names_the_stored_pdf_but_holds_no_student_text() -> None:
    mem, college, booklet, wf = setup()
    approve(mem, college, booklet, wf)
    issued = next(e for e in mem.audit.events if e.action.value == "result_sheet.issued")
    assert isinstance(issued.after, dict) and issued.after["pdf_stored"] is True
    dump = repr([(e.before, e.after) for e in mem.audit.events])
    student = college.students[0]
    assert student.name not in dump and student.usn not in dump and "alpha" not in dump


# --- the list ---------------------------------------------------------------------------------


def two_evaluated() -> tuple[InMemory, CollegeFixture, Booklet, Booklet, Workflow]:
    mem, college, first, wf = setup()
    approve(mem, college, first, wf)
    second = scored_booklet(mem, college, ci_shaped_blueprint(mem, college), {"1": GOOD})
    second = replace(second, student_id=college.students[1].id)
    mem.booklets.save(college.id, second)
    mem.clock.advance(hours=1)
    approve(mem, college, second, wf)
    return mem, college, first, mem.booklets.get(college.id, second.id), wf


def test_search_by_exam_student_usn_and_status() -> None:
    mem, college, first, second, _ = two_evaluated()
    found = evaluated(mem)
    everything = found.search(college.id)
    assert [e.booklet.id for e in everything.items] == [second.id, first.id]  # newest first
    assert everything.total == 2 and everything.items[0].exam == "Synthetic paper, CI shape"
    names = {s.id: s.name for s in college.students}
    assert [e.student.name for e in everything.items] == [
        names[college.students[1].id],
        names[college.students[0].id],
    ]

    def ids(**kw: object) -> list[object]:
        return [e.booklet.id for e in found.search(college.id, **kw).items]  # type: ignore[arg-type]

    assert ids(exam="ci shape") == [second.id, first.id] and ids(exam="physics") == []
    assert ids(exam="  Synthetic ") == [second.id, first.id]
    assert ids(student="a2") == [second.id] and ids(student="STUDENT A1") == [first.id]
    assert ids(usn="tst26a0001") == [first.id]
    assert ids(usn="26 a000") == [second.id, first.id]
    assert ids(usn="0002") == [second.id]
    assert ids(status=BookletStatus.APPROVED) == [second.id, first.id]
    assert ids(status=BookletStatus.APPROVED_AMENDED) == []
    assert ids(student="a1", usn="0002") == []  # every filter must match
    assert [e.booklet.id for e in found.search(college.id, limit=1, offset=1).items] == [first.id]
    with pytest.raises(InvariantError):
        found.search(college.id, status=BookletStatus.SCORED)


def test_booklets_not_yet_approved_are_not_listed() -> None:
    mem, college, booklet, wf = setup()
    assert evaluated(mem).search(college.id).total == 0
    wf.review.open(college.id, college.teacher.id, booklet.id)
    assert evaluated(mem).search(college.id).total == 0


def test_another_college_sees_nothing_and_cannot_download() -> None:
    mem, college, booklet, wf = setup()
    approve(mem, college, booklet, wf)
    other = add_college(mem, "B")
    assert evaluated(mem).search(other.id).total == 0
    with pytest.raises(NotFoundError):
        evaluated(mem).pdf(other.id, booklet.id, 1)


def test_unknown_versions_and_sheets_without_a_pdf_are_not_found() -> None:
    mem, college, booklet, wf = setup()
    approve(mem, college, booklet, wf)
    with pytest.raises(NotFoundError, match="v2"):
        evaluated(mem).pdf(college.id, booklet.id, 2)
    (sheet,) = mem.sheets.versions(college.id, booklet.id)
    mem.blobs.delete(sheet.pdf)  # type: ignore[arg-type]
    with pytest.raises(NotFoundError):
        evaluated(mem).pdf(college.id, booklet.id, 1)
    # A sheet issued before PDFs were stored.
    mem.sheets.delete_for_booklet(college.id, booklet.id)
    mem.sheets.save(college.id, replace(sheet, pdf=None))
    with pytest.raises(NotFoundError, match="no PDF"):
        evaluated(mem).pdf(college.id, booklet.id, 1)


# --- deletion ---------------------------------------------------------------------------------


def test_deleting_a_booklet_removes_every_version_of_its_sheet() -> None:
    mem, college, booklet, wf = setup()
    approve(mem, college, booklet, wf)
    two = mem.booklets.get_answer(college.id, answer_id(mem, booklet, "2"))
    wf.review.reopen(
        college.id, college.teacher.id, booklet.id, two.id, expected_version=two.version
    )
    wf.review.approve_answer(
        college.id,
        college.teacher.id,
        booklet.id,
        two.id,
        expected_version=two.version + 1,
        teacher_mark=Decimal(2),
    )
    keys = [sheet_key(college.id, booklet.id, v) for v in (1, 2)]
    assert all(mem.blobs.exists(k) for k in keys)

    make_services(mem).booklets.delete(college.id, college.teacher.id, booklet.id)

    assert not any(mem.blobs.exists(k) for k in keys)
    assert not [k for k in mem.blobs.keys if "sheets" in k.value]
    assert evaluated(mem).search(college.id).total == 0
    with pytest.raises(NotFoundError):
        evaluated(mem).pdf(college.id, booklet.id, 1)
    deleted = mem.audit.events[-1]
    assert deleted.action.value == "booklet.deleted" and deleted.after is None
