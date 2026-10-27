"""Content is private to the college (D165): the college's active teachers edit it, other
colleges never see it, and the repository refuses another college's items."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.content import Difficulty, Question, Subject
from tarn_core.errors import (
    AlreadyExistsError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
    TenantViolationError,
)
from tarn_core.ids import SubjectId
from tarn_core.ports.repositories import QuestionQuery
from tarn_core.services.access import WHOLE_COLLEGE
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.testing import InMemory
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    ci_shaped_blueprint,
    make_services,
    make_subject,
    meta,
)


def test_teachers_edit_their_colleges_content_others_never_see_it() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    blueprint = ci_shaped_blueprint(mem, a)
    question_id = blueprint.leaf("16")[1]
    svc = make_services(mem)

    assert svc.content.get_question(a.id, question_id).meta.college_id == a.id
    edited = svc.content.edit_question(
        a.id, a.teacher.id, question_id, max_marks=Decimal(10), text="New wording."
    )
    assert edited.meta.version == 2 and edited.meta.college_id == a.id

    # B finds nothing: reading, editing, listing and searching all miss A's content.
    with pytest.raises(NotFoundError):
        svc.content.get_question(b.id, question_id)
    with pytest.raises(NotFoundError):
        svc.content.edit_question(b.id, b.teacher.id, question_id, text="Hijack.")
    with pytest.raises(NotFoundError):
        svc.blueprints.get(b.id, blueprint.id, reach=WHOLE_COLLEGE)
    assert svc.blueprints.versions(b.id, blueprint.id, reach=WHOLE_COLLEGE) == []
    assert svc.blueprints.list(b.id, reach=WHOLE_COLLEGE) == []
    assert svc.subjects.list(b.id, reach=WHOLE_COLLEGE) == []
    assert svc.bank.search(b.id, QuestionQuery(), limit=100, reach=WHOLE_COLLEGE).total == 0
    assert svc.bank.topics(b.id, reach=WHOLE_COLLEGE) == []
    assert mem.content.get(a.id, Question, question_id).text == "New wording."

    actions = [(e.college_id, e.action) for e in mem.audit.events]
    assert actions == [(a.id, AuditAction.CONTENT_EDITED)]


def test_the_repository_refuses_another_colleges_items() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    subject = Subject(id=SubjectId(mem.ids.new()), meta=meta(a), code="S1", name="Synthetic")
    with pytest.raises(TenantViolationError):
        mem.content.save(b.id, subject)
    mem.content.save(a.id, subject)
    with pytest.raises(NotFoundError):
        mem.content.get(b.id, Subject, subject.id)
    assert mem.content.versions(b.id, Subject, subject.id) == []
    # The same id cannot start over in another college either.
    with pytest.raises(InvariantError):
        mem.content.save(b.id, replace(subject, meta=meta(b)))


def test_the_admin_and_disabled_teachers_do_not_author() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    blueprint = ci_shaped_blueprint(mem, a)
    question_id = blueprint.leaf("1")[1]
    svc = make_services(mem)
    with pytest.raises(PermissionDeniedError):
        svc.content.edit_question(a.id, a.admin.id, question_id, text="Admin's wording.")
    with pytest.raises(PermissionDeniedError):
        svc.bank.add_reference_answer(a.id, a.admin.id, question_id, text="Admin's key.")
    mem.users.save(a.id, replace(a.teacher, active=False))
    with pytest.raises(PermissionDeniedError):
        svc.content.edit_question(a.id, a.teacher.id, question_id, text="Disabled.")
    # Reading stays open to the admin (P24 decides what an admin sees).
    assert svc.bank.detail(a.id, question_id, reach=WHOLE_COLLEGE).question.id == question_id


def test_actor_must_belong_to_the_calling_college() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    question_id = ci_shaped_blueprint(mem, a).leaf("1")[1]
    svc = make_services(mem)
    with pytest.raises(NotFoundError):
        svc.content.edit_question(a.id, b.teacher.id, question_id, text="x")


def test_codes_are_unique_per_college() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    svc = make_services(mem)
    subjects = {c.id: make_subject(svc, c, code="SYN", name="Synthetic") for c in (a, b)}

    def create(college: CollegeFixture) -> Question:
        return svc.bank.create_question(
            college.id,
            college.teacher.id,
            subject_id=SubjectId(subjects[college.id].id),
            code="SYN-Q1",
            text="Name the synthetic concept.",
            max_marks=Decimal(2),
            difficulty=Difficulty.EASY,
        )

    first = create(a)
    second = create(b)  # the same code in another college is fine
    assert first.id != second.id
    with pytest.raises(AlreadyExistsError):
        create(a)


def test_a_paper_links_only_its_own_colleges_questions() -> None:
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    svc = make_services(mem)
    a_paper = ci_shaped_blueprint(mem, a)
    b_subject = make_subject(svc, b, code="SYN", name="Synthetic")
    copy = replace(a_paper, subject_id=SubjectId(b_subject.id), course_code="SYN101")
    document = blueprint_to_document(copy)
    with pytest.raises(InvariantError, match="not in your college"):
        svc.blueprints.create(b.id, b.teacher.id, document)
    assert mem.content.latest(b.id, ExamBlueprint) == []


def test_another_colleges_admin_gets_not_found_not_forbidden() -> None:
    """The lookup comes before the role check (D174): a 403 would confirm the id exists."""
    mem = InMemory()
    a = add_college(mem, "A")
    b = add_college(mem, "B")
    blueprint = ci_shaped_blueprint(mem, a)
    question_id = blueprint.leaf("1")[1]
    svc = make_services(mem)
    with pytest.raises(NotFoundError):
        svc.content.edit_question(b.id, b.admin.id, question_id, text="x")
    with pytest.raises(NotFoundError):
        svc.bank.add_reference_answer(b.id, b.admin.id, question_id, text="x")
    with pytest.raises(NotFoundError):
        svc.blueprints.update(b.id, b.admin.id, blueprint.id, {})
