"""The blueprint document: validation with the real papers' shapes, clear messages, choice
rules in the totals, the way back, and the service (create, edit by the owner, copy)."""

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import AnyN, EvaluationMethod, OrGroup
from tarn_core.domain.content import ContentMeta
from tarn_core.errors import InvariantError, NotOwnerError
from tarn_core.ids import BlueprintId, CollegeId, UserId
from tarn_core.services.blueprint_document import (
    DocumentReport,
    blueprint_to_document,
    check_document,
)
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, make_services

EXAMPLES = Path(__file__).resolve().parents[3] / "docs" / "api"
SUBJECT = "00000000-0000-4000-8000-000000000001"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / f"blueprint.example-{name}.json").read_text())
    return loaded


def q(label: str, marks: float, **extra: Any) -> dict[str, Any]:
    return {"type": "question", "label": label, "marks": marks, **extra}


def paper(total: float, *sections: dict[str, Any], **header: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "title": "T",
        "course_code": "C-1",
        "subject_id": SUBJECT,
        "total_marks": total,
        "sections": list(sections),
        **header,
    }


def section(
    label: str, items: list[dict[str, Any]], n: int | None = None, **kw: Any
) -> dict[str, Any]:
    out: dict[str, Any] = {"label": label, "method": "semantic_rubric", "items": items, **kw}
    if n is not None:
        out["choice"] = {"rule": "any", "n": n}
    return out


def numbered(first: int, count: int, marks: float) -> list[dict[str, Any]]:
    return [q(str(first + i), marks) for i in range(count)]


def messages(report: DocumentReport) -> str:
    return " | ".join(f"{i.path}: {i.message}" for i in report.issues)


# --- the real papers (requirements.md, sample facts) ------------------------------------------


def test_qp_ci_5_of_7_x_2_4_of_7_x_5_2_of_3_x_10_is_50() -> None:
    report = check_document(
        paper(
            50,
            section("A", numbered(1, 7, 2), 5),
            section("B", numbered(8, 7, 5), 4),
            section("C", numbered(15, 3, 10), 2),
        )
    )
    assert report.valid, messages(report)
    assert [s.max_marks for s in report.sections] == [10, 20, 20]
    assert [(s.items, s.counted) for s in report.sections] == [(7, 5), (7, 4), (3, 2)]
    assert report.computed_total == 50
    assert report.question_count == 17


def test_qp_ipr_with_or_pair_and_split_sub_parts_is_60() -> None:
    def split(label: str) -> dict[str, Any]:
        return {
            "label": label,
            "marks": 15,
            "parts": [{"label": "a", "marks": 10}, {"label": "b", "marks": 5}],
        }

    report = check_document(
        paper(
            60,
            section("A", numbered(1, 7, 3), 5),
            section("B", numbered(8, 4, 10), 3),
            section("C", [{"type": "or", "alternatives": [split("12"), split("13")]}]),
        )
    )
    assert report.valid, messages(report)
    assert [s.max_marks for s in report.sections] == [15, 30, 15]
    assert report.computed_total == 60
    assert report.question_count == 13  # both alternatives are slots
    assert report.parsed is not None
    blueprint = report.parsed.build(id=_bid(), meta=_meta())
    assert blueprint.sections[2].counted_items == 1
    assert isinstance(blueprint.sections[2].items[0], OrGroup)
    assert blueprint.unlinked_leaves()[-4:] == ("12.a", "12.b", "13.a", "13.b")


def test_the_example_documents_are_valid_and_round_trip() -> None:
    for name, total in (("ci", 50), ("ipr", 60), ("objective", 30)):
        document = example(name)
        report = check_document(document)
        assert report.valid, f"{name}: {messages(report)}"
        assert report.computed_total == total
        assert report.parsed is not None
        blueprint = report.parsed.build(id=_bid(), meta=_meta())
        assert blueprint_to_document(blueprint) == document, name


def test_wrong_totals_are_explained_with_the_choice_rules() -> None:
    document = example("ci")
    document["total_marks"] = 55
    report = check_document(document)
    assert [i.path for i in report.issues] == ["total_marks"]
    message = report.issues[0].message
    assert "55" in message and "add up to 50" in message
    assert "A: 10 (best 5 of 7)" in message and "C: 20 (best 2 of 3)" in message

    # Without the choice rule the sections would add up to 14 + 35 + 30 = 79: it must not count.
    document["total_marks"] = 79
    assert not check_document(document).valid


def test_best_n_uses_the_largest_items_when_marks_differ() -> None:
    items = [q("1", 2), q("2", 8), q("3", 5)]
    assert check_document(paper(13, section("A", items, 2))).valid  # 8 + 5
    assert not check_document(paper(7, section("A", items, 2))).valid  # not 2 + 5


# --- clear messages ---------------------------------------------------------------------------


def test_any_n_of_m_must_be_possible() -> None:
    report = check_document(paper(10, section("A", numbered(1, 3, 5), 4)))
    assert "answering any 4 of 3 is impossible" in messages(report)
    assert "sections[0].choice.n" in messages(report)


def test_or_alternatives_must_carry_equal_marks() -> None:
    items = [
        {"type": "or", "alternatives": [{"label": "12", "marks": 10}, {"label": "13", "marks": 12}]}
    ]
    report = check_document(paper(10, section("C", items)))
    assert "OR pair 12 (10) or 13 (12): alternatives must carry equal marks" in messages(report)


def test_sub_part_marks_must_add_up() -> None:
    parts = [{"label": "a", "marks": 10}, {"label": "b", "marks": 4}]
    report = check_document(paper(15, section("C", [q("12", 15, parts=parts)])))
    assert (
        "Question 12: the sub-parts add up to 14 (a 10 + b 4) but the question carries 15"
        in messages(report)
    )


def test_step_marks_must_add_up_and_stay_off_split_questions() -> None:
    steps = [{"label": "Formula", "marks": 1}, {"label": "Answer", "marks": 2}]
    report = check_document(paper(5, section("B", [q("1", 5, steps=steps)])))
    assert "Question 1: the step marks add up to 3 but it carries 5" in messages(report)
    parts = [{"label": "a", "marks": 3}, {"label": "b", "marks": 2}]
    both = check_document(paper(5, section("B", [q("1", 5, parts=parts, steps=steps)])))
    assert "put step marks on its sub-parts" in messages(both)


def test_duplicate_numbers_and_labels() -> None:
    report = check_document(
        paper(8, section("A", [q("1", 2), q("1", 2)]), section("A", [q("1", 4)]))
    )
    text = messages(report)
    assert "Question number 1 is used twice" in text
    assert "Section labels must be different" in text


def test_every_problem_is_reported_not_just_the_first() -> None:
    document = paper(
        10,
        section("A", [q("1", 0), {"type": "question", "marks": 2}], 3, method="essay"),
        negative_marking=0.3,
        subject_id="not-a-uuid",
        extra="x",
    )
    paths = {i.path for i in check_document(document).issues}
    assert {
        "extra",
        "subject_id",
        "negative_marking",
        "sections[0].method",
        "sections[0].items[0].marks",
        "sections[0].items[1].label",
    } <= paths


@pytest.mark.parametrize(
    ("change", "path", "text"),
    [
        ({"schema_version": "2.0"}, "schema_version", '"1.0"'),
        ({"title": "  "}, "title", "must not be empty"),
        ({"course_code": 5}, "course_code", "must be text"),
        ({"total_marks": "50"}, "total_marks", "must be a number"),
        ({"total_marks": True}, "total_marks", "must be a number"),
        ({"total_marks": -1}, "total_marks", "greater than 0"),
        ({"duration_minutes": 0}, "duration_minutes", "between 1 and 1440"),
        ({"duration_minutes": 90.5}, "duration_minutes", "whole number"),
        ({"negative_marking": 1}, "negative_marking", "0, 0.25 or 0.5"),
        ({"mark_step": 0.3}, "mark_step", "0.25, 0.5 or 1"),
        ({"sections": []}, "sections", "at least 1"),
        ({"sections": "A"}, "sections", "must be a list"),
    ],
)
def test_header_problems(change: dict[str, Any], path: str, text: str) -> None:
    document = paper(2, section("A", [q("1", 2)]))
    document.update(change)
    report = check_document(document)
    assert any(i.path == path and text in i.message for i in report.issues), messages(report)


def test_marks_use_two_decimals_at_most_and_a_document_must_be_an_object() -> None:
    assert "2 decimal places" in messages(check_document(paper(1, section("A", [q("1", 0.125)]))))
    assert not check_document([]).valid
    assert not check_document(None).valid


def test_negative_marking_without_an_objective_section_is_a_warning_only() -> None:
    report = check_document(paper(2, section("A", [q("1", 2)]), negative_marking=0.5))
    assert report.valid
    assert [w.path for w in report.warnings] == ["negative_marking"]


# --- negative marking and the way back --------------------------------------------------------


def test_negative_marking_lands_on_objective_sections_only() -> None:
    report = check_document(example("objective"))
    assert report.parsed is not None
    blueprint = report.parsed.build(id=_bid(), meta=_meta())
    by_method = {s.method: {slot.negative_marks for slot in s.slots()} for s in blueprint.sections}
    assert by_method[EvaluationMethod.OMR_BUBBLE_SCAN] == {Decimal("0.25")}
    assert by_method[EvaluationMethod.KEYWORD_FORMULA] == {Decimal(0)}
    assert by_method[EvaluationMethod.DIAGRAM] == {Decimal(0)}
    assert isinstance(blueprint.sections[1].rule, AnyN)
    assert blueprint.sections[1].items[0].steps[0].label == "Formula"  # type: ignore[union-attr]


def test_unlinked_leaves_are_listed_and_links_are_kept() -> None:
    document = example("ci")
    document["sections"][0]["items"][0]["question_id"] = "00000000-0000-4000-8000-0000000000aa"
    report = check_document(document)
    assert report.valid
    assert len(report.unlinked) == 16
    assert "1" not in report.unlinked


# --- the service ------------------------------------------------------------------------------


def _world() -> tuple[InMemory, Any, Any, Any]:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    return mem, a, b, make_services(mem)


def _document(subject_id: object) -> dict[str, Any]:
    document = copy.deepcopy(example("ci"))
    document["subject_id"] = str(subject_id)
    return document


def test_create_edit_copy_and_listing() -> None:
    mem, a, b, svc = _world()
    subject = svc.subjects.create(a.id, a.teacher.id, code=" SYN1 ", name=" Synthetic Civics ")
    assert (subject.code, subject.name) == ("SYN1", "Synthetic Civics")

    created = svc.blueprints.create(a.id, a.teacher.id, _document(subject.id))
    assert created.meta.owning_college_id == a.id and created.meta.version == 1
    assert created.total_marks == 50 and created.course_code == "SYN-CI"
    assert _last_action(mem) is AuditAction.CONTENT_CREATED

    # Another college sees it (global) but cannot edit it.
    assert [x.id for x in svc.blueprints.list()] == [created.id]
    edited_doc = _document(subject.id)
    edited_doc["title"] = "Renamed"
    with pytest.raises(NotOwnerError):
        svc.blueprints.update(b.id, b.teacher.id, created.id, edited_doc)

    edited = svc.blueprints.update(a.id, a.teacher.id, created.id, edited_doc)
    assert edited.meta.version == 2 and edited.title == "Renamed"
    assert svc.blueprints.get(created.id, 1).title == "Synthetic paper, CI shape"
    assert [x.title for x in svc.blueprints.list()] == ["Renamed"]
    assert _last_action(mem) is AuditAction.CONTENT_EDITED

    mine = svc.blueprints.copy(b.id, b.teacher.id, created.id)
    assert mine.id != created.id and mine.meta.owning_college_id == b.id
    assert mine.meta.version == 1 and mine.meta.copied_from == edited.ref
    assert mine.sections == edited.sections
    assert _last_action(mem) is AuditAction.CONTENT_COPIED
    svc.blueprints.update(b.id, b.teacher.id, mine.id, edited_doc)  # now b may edit its copy
    assert len(svc.blueprints.list()) == 2


def test_create_refuses_an_invalid_document_and_an_unknown_subject() -> None:
    _, a, _, svc = _world()
    subject = svc.subjects.create(a.id, a.teacher.id, code="S", name="Subject")
    bad = _document(subject.id)
    bad["total_marks"] = 49
    with pytest.raises(InvariantError, match="total_marks: The exam total is 49"):
        svc.blueprints.create(a.id, a.teacher.id, bad)
    with pytest.raises(InvariantError, match="no such subject"):
        svc.blueprints.create(a.id, a.teacher.id, _document("00000000-0000-4000-8000-0000000000ff"))
    assert svc.blueprints.list() == []


def test_a_blueprint_with_unlinked_questions_cannot_register_booklets() -> None:
    _, a, _, svc = _world()
    subject = svc.subjects.create(a.id, a.teacher.id, code="S", name="Subject")
    blueprint = svc.blueprints.create(a.id, a.teacher.id, _document(subject.id))
    with pytest.raises(InvariantError, match="no question linked for 1, 2, 3"):
        svc.booklets.register(
            a.id,
            a.teacher.id,
            student_id=a.students[0].id,
            blueprint_id=blueprint.id,
            file_sha256="0" * 64,
        )


# --- helpers ----------------------------------------------------------------------------------


def _last_action(mem: InMemory) -> AuditAction:
    return mem.audit.events[-1].action


def _bid() -> BlueprintId:
    return BlueprintId(UUID(int=9))


def _meta() -> ContentMeta:
    return ContentMeta(owning_college_id=CollegeId(UUID(int=1)), created_by=UserId(UUID(int=2)))
