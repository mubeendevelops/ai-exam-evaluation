"""The question bank on PostgreSQL (P7): the same behaviour as the in-memory adapters, plus
what only the database can enforce."""

from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from tarn_adapters.postgres.testing import Opener, new_college
from tarn_core.domain.content import (
    CriterionType,
    Difficulty,
    KeyFile,
    ReferenceAnswer,
    RubricCriterion,
    SemanticParams,
    Subject,
)
from tarn_core.errors import InvariantError, NotOwnerError
from tarn_core.ids import CriterionId
from tarn_core.ports.repositories import QuestionQuery
from tarn_core.services.question_bank import CriterionSpec
from tarn_core.testing.builders import make_services

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\nsynthetic key"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _spec(label: str, weight: str, id: CriterionId | None = None) -> CriterionSpec:
    return CriterionSpec(
        label=label,
        type=CriterionType.SEMANTIC,
        weight=Decimal(weight),
        params=SemanticParams(reference_statement=f"{label} statement"),
        id=id,
    )


def test_a_question_with_its_key_and_rubric_round_trips(session: Opener) -> None:
    a = new_college(session, "QA")
    with session(a.id) as s:
        svc = make_services(s)
        subject = svc.subjects.create(a.id, a.teacher.id, code="S", name="Subject")
        q = svc.bank.create_question(
            a.id,
            a.teacher.id,
            subject_id=subject.id,
            code="Q-1",
            text="Why?",
            max_marks=Decimal("4.5"),
            difficulty=Difficulty.HARD,
            category="Optics",
            reference_answer="Because.",
            criteria=[_spec("One", "2.5"), _spec("Two", "2")],
        )
        key = svc.bank.upload_key_file(
            a.id,
            a.teacher.id,
            q.id,
            file_name="k.pdf",
            data=PDF,
            declared_type="application/pdf",
            keywords=["lens"],
            no_student_data=True,
        )
    with session(a.id) as s:
        got = s.content.get(type(q), q.id)
        assert got == q and got.code == "Q-1" and got.difficulty is Difficulty.HARD
        assert [c.weight for c in s.content.for_question(RubricCriterion, q.id)] == [
            Decimal("2.5"),
            Decimal(2),
        ]
        stored = s.content.for_question(KeyFile, q.id)
        assert stored == [key]
        detail = make_services(s).bank.detail(q.id)
        assert detail.owner_name == "Test College QA" and detail.subject is not None
        assert detail.rubric_total == Decimal("4.5")


def test_retired_items_leave_the_listing_but_keep_their_history(session: Opener) -> None:
    a = new_college(session, "QR")
    with session(a.id) as s:
        svc = make_services(s)
        subject = svc.subjects.create(a.id, a.teacher.id, code="S", name="Subject")
        q = svc.bank.create_question(
            a.id,
            a.teacher.id,
            subject_id=subject.id,
            code="R-1",
            text="t",
            max_marks=Decimal(2),
            reference_answer="zebra answer",
            criteria=[_spec("A", "1"), _spec("B", "1")],
        )
        answer = svc.bank.detail(q.id).answers[0]
        crit = {c.label: c for c in s.content.for_question(RubricCriterion, q.id)}
        svc.bank.set_rubric(a.id, a.teacher.id, q.id, [_spec("A", "2", crit["A"].id)])
        assert svc.bank.search(QuestionQuery(keyword="zebra", owner_id=a.id), limit=5).total == 1
        svc.bank.retire_reference_answer(a.id, a.teacher.id, q.id, answer.id)
    with session(a.id) as s:
        assert [c.label for c in s.content.for_question(RubricCriterion, q.id)] == ["A"]
        assert s.content.for_question(ReferenceAnswer, q.id) == []
        assert [c.retired for c in s.content.versions(RubricCriterion, crit["B"].id)] == [
            False,
            True,
        ]
        svc = make_services(s)
        assert svc.bank.search(QuestionQuery(keyword="zebra", owner_id=a.id), limit=5).total == 0
        assert svc.bank.search(QuestionQuery(owner_id=a.id), limit=5).items[0].key_count == 0


def test_filters_match_the_in_memory_behaviour(session: Opener) -> None:
    a = new_college(session, "QF")
    b = new_college(session, "QG")
    tag = uuid4().hex[:6].upper()
    with session(a.id) as s:
        svc = make_services(s)
        phy = svc.subjects.create(a.id, a.teacher.id, code="P", name="Physics")
        mat = svc.subjects.create(a.id, a.teacher.id, code="M", name="Maths")

        def add(
            code: str,
            text: str,
            topic: str,
            level: Difficulty,
            subject: Subject = phy,
            answer: str | None = None,
        ) -> None:
            svc.bank.create_question(
                a.id,
                a.teacher.id,
                subject_id=subject.id,
                code=f"{tag}-{code}",
                text=text,
                max_marks=Decimal(2),
                category=topic,
                difficulty=level,
                reference_answer=answer,
            )

        add("Q1", "State Lenz's law.", "Electromagnetism", Difficulty.EASY, answer="emf opposes")
        add("Q2", "Define resonance.", "AC Circuits", Difficulty.HARD)
        add("M1", "Integrate x dx.", "Calculus", Difficulty.EASY, subject=mat)
        add("P%", "Percent sign 100% literal", "Edge", Difficulty.MEDIUM)
    with session(b.id) as s:
        svc = make_services(s)
        svc.bank.create_question(
            b.id,
            b.teacher.id,
            subject_id=phy.id,
            code=f"{tag}-B1",
            text="B's.",
            max_marks=Decimal(1),
            category="Optics",
        )

    def codes(**kw: Any) -> list[str]:
        with session(b.id) as s:  # another college reads: global visibility
            page = make_services(s).bank.search(
                QuestionQuery(code=tag, **kw),
                limit=20,
            )
        assert page.total == len(page.items)
        return [i.question.code.removeprefix(f"{tag}-") for i in page.items]

    assert codes() == ["B1", "M1", "P%", "Q1", "Q2"]
    assert codes(subject_id=phy.id) == ["B1", "P%", "Q1", "Q2"]
    assert codes(difficulty=Difficulty.EASY) == ["M1", "Q1"]
    assert codes(topic="ac circuits") == ["Q2"]
    assert codes(topic="circuit") == []
    assert codes(keyword="lenz") == ["Q1"]
    assert codes(keyword="OPPOSES") == ["Q1"]  # reference answer
    assert codes(keyword="electromag") == ["Q1"]  # topic
    assert codes(keyword="%") == ["P%"]  # literal, not a wildcard
    assert codes(keyword="_") == []
    assert codes(owner_id=b.id) == ["B1"]
    assert codes(keyword="lenz", difficulty=Difficulty.HARD) == []
    with session(a.id) as s:
        svc = make_services(s)
        assert {"Calculus", "AC Circuits"} <= set(svc.bank.topics())
        assert list(svc.bank.topics(mat.id)) == ["Calculus"]
        first = svc.bank.search(QuestionQuery(code=tag), limit=2)
        rest = svc.bank.search(QuestionQuery(code=tag), limit=2, offset=2)
        assert (first.total, len(first.items), len(rest.items)) == (5, 2, 2)
        assert first.items[1].owner_name is not None
        assert (
            svc.bank.search(QuestionQuery(code=f"{tag}-q1".lower(), exact_code=True), limit=3).total
            == 1
        )


def test_database_refuses_what_the_service_forbids(session: Opener) -> None:
    a = new_college(session, "QD")
    b = new_college(session, "QE")
    with session(a.id) as s:
        svc = make_services(s)
        subject = svc.subjects.create(a.id, a.teacher.id, code="S", name="S")
        q = svc.bank.create_question(
            a.id, a.teacher.id, subject_id=subject.id, code="D-1", text="t", max_marks=Decimal(1)
        )
        ok = svc.bank.upload_key_file(
            a.id,
            a.teacher.id,
            q.id,
            file_name="k.pdf",
            data=PDF,
            declared_type=None,
            no_student_data=True,
        )
        # Even a record built around the service cannot say "student data may be inside".
        with pytest.raises(InvariantError):
            s.content.save(
                replace(ok, meta=replace(ok.meta, version=2), no_student_data_confirmed=False)
            )
        with (
            pytest.raises(Exception, match=r"no_student_data_confirmed|check"),
            s.conn.begin_nested(),
        ):
            s.conn.execute(
                text(
                    "INSERT INTO key_files (id, version, question_id, name, media_type, "
                    "size_bytes, sha256, blob_key, keywords, no_student_data_confirmed, "
                    "owning_college_id, "
                    "created_by) VALUES (gen_random_uuid(), 1, :q, 'k', 'application/pdf', 1, "
                    "repeat('a', 64), 'global/x', '{}', false, :c, :u)"
                ),
                {"q": q.id, "c": a.id, "u": a.teacher.id},
            )
    with session(b.id) as s:
        svc = make_services(s)
        # B cannot attach a key to A's question, in the service or in the database.
        with pytest.raises(NotOwnerError):
            svc.bank.upload_key_file(
                b.id,
                b.teacher.id,
                q.id,
                file_name="k.pdf",
                data=PDF,
                declared_type=None,
                no_student_data=True,
            )
        with pytest.raises(NotOwnerError):
            s.content.save(replace(ok, meta=replace(ok.meta, version=2)))


def test_the_college_directory_names_owners_for_everyone(session: Opener) -> None:
    a = new_college(session, "QN")
    b = new_college(session, "QM")
    with session(b.id) as s:
        names = s.colleges.names([a.id, b.id])
        assert names == {a.id: "Test College QN", b.id: "Test College QM"}
        # ... but not the colleges table itself, and the directory cannot be rewritten.
        assert (
            s.conn.execute(
                text("SELECT count(*) FROM colleges WHERE id = :i"), {"i": a.id}
            ).scalar()
            == 0
        )
        renamed = s.conn.execute(
            text("UPDATE college_directory SET name = 'x' WHERE id = :i"), {"i": a.id}
        )
        assert renamed.rowcount == 0
        s.colleges.save(replace(s.colleges.get(b.id), name="Renamed M"))
    with session(a.id) as s:
        assert s.colleges.names([b.id]) == {b.id: "Renamed M"}
