"""Manage Schema's lists (P33, D219) on one scenario, for every ``ContentRepository``: the
in-memory one and PostgreSQL's query must filter, count uses and list batches alike.

``build`` makes, in one college (through the services, as the API would):

- the CI-shaped paper of subject SYN101 (from the builders);
- schema "Physics Midterm Matrix" (SYN101), saved twice: batch "2024 batch", then "Class 12";
- schema "Board Rubric 100%" (SYN101), batch "class 12";
- schema "Other subject schema" (SYN301, a second subject), batch "2025 batch";
- two papers generated from the matrix (one edited since, still naming it) and one from the
  rubric, edited since to name no schema: the matrix is used in 2 papers, the rubric in 0.

Since P34 ``check`` also covers Manage Question Papers: ``blueprint_page`` (search on the title
or descriptor, descriptor exactly, the creation date, paging and the total) and
``descriptors``.

Test code that lives in the package so adapters can reuse it."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tarn_core.domain.blueprint import BlueprintKind, BlueprintPage, BlueprintQuery, ExamBlueprint
from tarn_core.ids import BlueprintId, CollegeId, SubjectId
from tarn_core.ports.repositories import ContentRepository
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.testing.builders import (
    Backend,
    CollegeFixture,
    add_subject,
    ci_shaped_blueprint,
    make_services,
)


def schema_document(
    subject_id: SubjectId,
    title: str,
    *,
    target_batch: str = "",
    weightage_note: str = "",
    questions: int = 5,
    marks: int = 2,
) -> dict[str, Any]:
    """A 1.2 schema document: one section of ``questions`` unlinked questions."""
    return {
        "schema_version": "1.2",
        "kind": "schema",
        "title": title,
        "course_code": "SYN",
        "subject_id": str(subject_id),
        "total_marks": questions * marks,
        "target_batch": target_batch,
        "weightage_note": weightage_note,
        "sections": [
            {
                "label": "A",
                "method": "semantic_rubric",
                "items": [
                    {"type": "question", "label": str(n), "marks": marks, "question_id": None}
                    for n in range(1, questions + 1)
                ],
            }
        ],
    }


@dataclass
class Scenario:
    college: CollegeFixture
    subject: SubjectId
    other_subject: SubjectId
    paper: ExamBlueprint
    matrix: ExamBlueprint
    rubric: ExamBlueprint
    other: ExamBlueprint
    generated: tuple[BlueprintId, ...]


def build(mem: Backend, college: CollegeFixture) -> Scenario:
    cid, sme = college.id, college.teacher.id
    paper = ci_shaped_blueprint(mem, college)
    other_subject = add_subject(mem, college, "SYN301", "Synthetic Other")
    svc = make_services(mem).blueprints
    matrix = svc.create(
        cid,
        sme,
        schema_document(paper.subject_id, "Physics Midterm Matrix", target_batch="2024 batch"),
    )
    matrix = svc.update(
        cid,
        sme,
        matrix.id,
        schema_document(paper.subject_id, "Physics Midterm Matrix", target_batch="Class 12"),
    )
    rubric = svc.create(
        cid, sme, schema_document(paper.subject_id, "Board Rubric 100%", target_batch="class 12")
    )
    other = svc.create(
        cid,
        sme,
        schema_document(other_subject.id, "Other subject schema", target_batch="2025 batch"),
    )
    first = svc.generate_paper(cid, sme, matrix.id)
    second = svc.generate_paper(cid, sme, matrix.id, title="Second matrix paper")
    edited = blueprint_to_document(second)
    edited["descriptor"] = "Mid-term"
    svc.update(cid, sme, second.id, edited)
    from_rubric = svc.generate_paper(cid, sme, rubric.id)
    unlinked = blueprint_to_document(from_rubric)
    unlinked["from_schema"] = None
    svc.update(cid, sme, from_rubric.id, unlinked)
    return Scenario(
        college=college,
        subject=paper.subject_id,
        other_subject=other_subject.id,
        paper=paper,
        matrix=matrix,
        rubric=rubric,
        other=other,
        generated=(first.id, second.id, from_rubric.id),
    )


def check(content: ContentRepository, s: Scenario) -> None:
    cid = s.college.id

    def titles(query: BlueprintQuery) -> list[str]:
        return sorted(b.title for b in content.blueprint_summaries(cid, query))

    schemas = {
        b.id: b for b in content.blueprint_summaries(cid, BlueprintQuery(kind=BlueprintKind.SCHEMA))
    }
    assert set(schemas) == {s.matrix.id, s.rubric.id, s.other.id}
    matrix = schemas[s.matrix.id]
    assert (matrix.version, matrix.target_batch, matrix.used_in) == (2, "Class 12", 2)
    assert matrix.section_count == 1 and matrix.total_marks == 10 and matrix.from_schema is None
    assert matrix.saved_at is not None and matrix.course_code == "SYN"
    assert schemas[s.rubric.id].used_in == 0 and schemas[s.other.id].used_in == 0

    mine = frozenset({s.subject})
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, subject_ids=mine)) == [
        "Board Rubric 100%",
        "Physics Midterm Matrix",
    ]
    assert titles(BlueprintQuery(subject_ids=frozenset())) == []
    # Name anywhere, ignoring case; wildcards are characters.
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, title="MATRIX")) == [
        "Physics Midterm Matrix"
    ]
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, title="%")) == ["Board Rubric 100%"]
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, title="_")) == []
    # Batch exactly, ignoring case; the matrix's first batch is no longer its own.
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, target_batch="CLASS 12")) == [
        "Board Rubric 100%",
        "Physics Midterm Matrix",
    ]
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, target_batch="2024 batch")) == []
    assert titles(BlueprintQuery(kind=BlueprintKind.SCHEMA, target_batch="class")) == []

    papers = {
        b.id: b for b in content.blueprint_summaries(cid, BlueprintQuery(kind=BlueprintKind.PAPER))
    }
    assert set(papers) == {s.paper.id, *s.generated}
    first, second, from_rubric = (papers[i] for i in s.generated)
    assert first.from_schema == s.matrix.id and first.title == "Physics Midterm Matrix paper"
    assert second.from_schema == s.matrix.id and second.descriptor == "Mid-term"
    assert from_rubric.from_schema is None and from_rubric.version == 2

    assert list(content.target_batches(cid, BlueprintKind.SCHEMA)) == [
        "2025 batch",
        "Class 12",
        "class 12",
    ]
    assert list(content.target_batches(cid, BlueprintKind.SCHEMA, subject_ids=mine)) == [
        "Class 12",
        "class 12",
    ]
    assert list(content.target_batches(cid, BlueprintKind.SCHEMA, subject_ids=frozenset())) == []
    # Generated papers keep the schema's batch.
    assert list(content.target_batches(cid, BlueprintKind.PAPER)) == ["Class 12", "class 12"]
    check_pages(content, s)


def check_pages(content: ContentRepository, s: Scenario) -> None:
    """Manage Question Papers (P34)."""
    cid = s.college.id
    paper_kind = BlueprintQuery(kind=BlueprintKind.PAPER)
    everything = content.blueprint_page(cid, paper_kind, limit=10)
    assert everything.total == 4 and len(everything.items) == 4
    # Most recently saved first, then by title ignoring case.
    assert [b.id for b in everything.items] == [
        b.id
        for b in sorted(
            sorted(everything.items, key=lambda b: (b.title.casefold(), str(b.id))),
            key=lambda b: b.saved_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
    ]
    first_page = content.blueprint_page(cid, paper_kind, limit=3)
    second_page = content.blueprint_page(cid, paper_kind, limit=3, offset=3)
    assert (first_page.total, second_page.total) == (4, 4)
    assert [b.id for b in (*first_page.items, *second_page.items)] == [
        b.id for b in everything.items
    ]
    past = content.blueprint_page(cid, paper_kind, limit=3, offset=9)
    assert (past.items, past.total) == ((), 4)

    def titles(query: BlueprintQuery) -> list[str]:
        return sorted(b.title for b in content.blueprint_page(cid, query, limit=10).items)

    # The title or the descriptor, anywhere, ignoring case.
    assert titles(BlueprintQuery(kind=BlueprintKind.PAPER, search="MID-T")) == [
        "Second matrix paper"
    ]
    assert titles(BlueprintQuery(kind=BlueprintKind.PAPER, search="rubric")) == [
        "Board Rubric 100% paper"
    ]
    assert titles(BlueprintQuery(kind=BlueprintKind.PAPER, search="_")) == []
    # The descriptor exactly, ignoring case.
    assert titles(BlueprintQuery(kind=BlueprintKind.PAPER, descriptor="MID-TERM")) == [
        "Second matrix paper"
    ]
    assert titles(BlueprintQuery(kind=BlueprintKind.PAPER, descriptor="mid")) == []
    # The UTC date version 1 was saved: version 2 of the edited papers does not move it.
    second = next(b for b in everything.items if b.id == s.generated[1])
    assert second.version == 2 and second.created_at is not None and second.saved_at is not None
    assert second.created_at <= second.saved_at
    day = second.created_at.astimezone(UTC).date()
    on_day = content.blueprint_page(
        cid, BlueprintQuery(kind=BlueprintKind.PAPER, created_on=day), limit=10
    )
    assert s.generated[1] in {b.id for b in on_day.items}
    later = BlueprintQuery(kind=BlueprintKind.PAPER, created_on=day + timedelta(days=1))
    assert content.blueprint_page(cid, later, limit=10).total == 0
    mine = frozenset({s.subject})
    none = BlueprintQuery(kind=BlueprintKind.PAPER, subject_ids=frozenset())
    assert content.blueprint_page(cid, none, limit=10) == BlueprintPage(items=(), total=0)
    assert list(content.descriptors(cid, BlueprintKind.PAPER)) == ["Mid-term"]
    assert list(content.descriptors(cid, BlueprintKind.PAPER, subject_ids=mine)) == ["Mid-term"]
    assert list(content.descriptors(cid, BlueprintKind.PAPER, subject_ids=frozenset())) == []
    assert list(content.descriptors(cid, BlueprintKind.SCHEMA)) == []


def check_isolated(content: ContentRepository, own: CollegeId) -> None:
    """``content`` of college ``own`` (which has no blueprints) sees none of another's."""
    assert list(content.blueprint_summaries(own, BlueprintQuery())) == []
    assert list(content.target_batches(own, BlueprintKind.SCHEMA)) == []
    assert content.blueprint_page(own, BlueprintQuery(), limit=10).total == 0
    assert list(content.descriptors(own, BlueprintKind.PAPER)) == []
