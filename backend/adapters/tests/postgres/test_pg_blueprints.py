"""Blueprints in PostgreSQL (P6): create from the public document, versions, copy, listing,
unlinked slots, and the new header columns."""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from tarn_adapters.postgres.testing import Opener, new_college
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, OrGroup
from tarn_core.domain.content import Subject
from tarn_core.errors import InvariantError, NotOwnerError
from tarn_core.services.blueprint_document import blueprint_to_document
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[4] / "docs" / "api"


def _example(name: str, subject_id: object) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((EXAMPLES / f"blueprint.example-{name}.json").read_text())
    document["subject_id"] = str(subject_id)
    return document


def test_documents_survive_the_database_unchanged(session: Opener) -> None:
    college = new_college(session, "P6")
    with session(college.id) as s:
        svc = make_services(s)
        subject = svc.subjects.create(college.id, college.teacher.id, code="S6", name="Subject six")
        ids = []
        for name in ("ci", "ipr", "objective"):
            created = svc.blueprints.create(
                college.id, college.teacher.id, _example(name, subject.id)
            )
            ids.append(created.id)
    with session(college.id) as s:
        for blueprint_id, name in zip(ids, ("ci", "ipr", "objective"), strict=True):
            stored = s.content.get(ExamBlueprint, blueprint_id)
            assert blueprint_to_document(stored) == _example(name, subject.id)
        ipr = s.content.get(ExamBlueprint, ids[1])
        assert isinstance(ipr.sections[2].items[0], OrGroup)
        assert ipr.course_code == "SYN-IPR" and ipr.duration_minutes == 180
        assert len(ipr.unlinked_leaves()) == 7 + 4 + 4


def test_edit_copy_list_and_audit(session: Opener) -> None:
    a = new_college(session, "PA")
    b = new_college(session, "PB")
    with session(a.id) as s:
        svc = make_services(s)
        subject = svc.subjects.create(a.id, a.teacher.id, code="SA", name="Subject A")
        created = svc.blueprints.create(a.id, a.teacher.id, _example("ci", subject.id))
    edited_doc = _example("ci", subject.id)
    edited_doc["title"] = "Second version"

    with session(b.id) as s:
        svc = make_services(s)
        assert created.id in {x.id for x in svc.blueprints.list()}  # global: B sees A's
        with pytest.raises(NotOwnerError):
            svc.blueprints.update(b.id, b.teacher.id, created.id, edited_doc)
        mine = svc.blueprints.copy(b.id, b.teacher.id, created.id)
        assert mine.meta.owning_college_id == b.id and mine.meta.copied_from == created.ref

    with session(a.id) as s:
        svc = make_services(s)
        v2 = svc.blueprints.update(a.id, a.teacher.id, created.id, edited_doc)
        assert v2.meta.version == 2
        assert [v.title for v in svc.blueprints.versions(created.id)] == [
            "Synthetic paper, CI shape",
            "Second version",
        ]
        latest = {x.id: x for x in svc.blueprints.list()}
        assert latest[created.id].meta.version == 2 and latest[mine.id].meta.version == 1
        # A cannot edit B's copy.
        with pytest.raises(NotOwnerError):
            svc.blueprints.update(a.id, a.teacher.id, mine.id, edited_doc)

    for college, expected in (
        (a, {AuditAction.CONTENT_CREATED, AuditAction.CONTENT_EDITED}),
        (b, {AuditAction.CONTENT_COPIED}),
    ):
        with session(college.id) as s:
            actions = set(s.conn.execute(text("SELECT action FROM audit_events")).scalars())
        assert {x.value for x in expected} <= actions


def test_subjects_list_and_validation(session: Opener) -> None:
    college = new_college(session, "PS")
    with session(college.id) as s:
        svc = make_services(s)
        made = svc.subjects.create(college.id, college.teacher.id, code="ZZ1", name="Zoology test")
        assert made.id in {x.id for x in svc.subjects.list()}
        assert isinstance(s.content.get(Subject, made.id), Subject)
        with pytest.raises(InvariantError):
            svc.subjects.create(college.id, college.teacher.id, code=" ", name="x")
        with pytest.raises(InvariantError, match="no such subject"):
            svc.blueprints.create(
                college.id,
                college.teacher.id,
                _example("ci", "00000000-0000-4000-8000-0000000000ee"),
            )
