"""Global content: the owning college edits, other colleges copy (design decision 3)."""

from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import Glossary, Question, RubricCriterion
from tarn_core.errors import NotFoundError, NotOwnerError
from tarn_core.ids import GlossaryId
from tarn_core.testing import InMemory
from tarn_core.testing.builders import add_college, ci_shaped_blueprint, make_services, meta


def test_owner_edits_others_copy() -> None:
    mem = InMemory()
    owner = add_college(mem, "A")
    other = add_college(mem, "B")
    blueprint = ci_shaped_blueprint(mem, owner)
    question_id = blueprint.leaf("16")[1]
    mem.content.save(
        Glossary(
            id=GlossaryId(mem.ids.new()),
            meta=meta(owner),
            question_id=question_id,
            teacher_terms=("alpha",),
        )
    )
    svc = make_services(mem)

    # Visible to everyone.
    assert svc.content.get_question(question_id).meta.owning_college_id == owner.id

    edited = svc.content.edit_question(
        owner.id, owner.teacher.id, question_id, max_marks=Decimal(10), text="New wording."
    )
    assert edited.meta.version == 2
    assert edited.meta.owning_college_id == owner.id

    with pytest.raises(NotOwnerError):
        svc.content.edit_question(other.id, other.teacher.id, question_id, text="Hijack.")
    assert mem.content.get(Question, question_id).text == "New wording."

    copy = svc.content.copy_question(other.id, other.teacher.id, question_id)
    assert copy.id != question_id
    assert copy.meta.owning_college_id == other.id
    assert copy.meta.version == 1
    assert copy.meta.copied_from == edited.ref
    copied_criteria = mem.content.for_question(RubricCriterion, copy.id)
    assert len(copied_criteria) == 2
    assert all(c.meta.owning_college_id == other.id for c in copied_criteria)
    assert [g.teacher_terms for g in mem.content.for_question(Glossary, copy.id)] == [("alpha",)]

    # The copy is now B's to edit, and A cannot touch it.
    svc.content.edit_question(other.id, other.teacher.id, copy.id, text="B's wording.")
    with pytest.raises(NotOwnerError):
        svc.content.edit_question(owner.id, owner.teacher.id, copy.id, text="A's wording.")

    actions = [(e.college_id, e.action) for e in mem.audit.events]
    assert actions == [
        (owner.id, AuditAction.CONTENT_EDITED),
        (other.id, AuditAction.CONTENT_COPIED),
        (other.id, AuditAction.CONTENT_EDITED),
    ]


def test_actor_must_belong_to_the_calling_college() -> None:
    mem = InMemory()
    owner = add_college(mem, "A")
    other = add_college(mem, "B")
    question_id = ci_shaped_blueprint(mem, owner).leaf("1")[1]
    svc = make_services(mem)
    with pytest.raises(NotFoundError):
        svc.content.edit_question(owner.id, other.teacher.id, question_id, text="x")
