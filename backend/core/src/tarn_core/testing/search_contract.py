"""The SME's global search (P37, D228, D243) on one scenario, for every backend: what the first
SME finds, and what they never find (another SME's unit and its students, another subject's
paper, schema and question, another college's anything).

``build`` gives the college:

- subject SYN101 (the first SME's): paper "Synthetic paper, CI shape" with exam code
  ``QP-ORBIT``, a schema "Orbit schema", questions whose text holds "orbit";
- subject SYN909 (nobody's grant): paper "Orbit hidden paper", schema "Orbit hidden schema",
  a question "Orbit hidden question";
- unit ``year`` "Orbit 2024" owned by the first SME with booklets of "Mira Visible"
  (``MV26A0001``) and the fixture's students; unit "Orbit 2025" of the same paper owned by a
  second SME, with a booklet of "Mira Hidden" (``MH26A0001``).

Test code that lives in the package so adapters can reuse it."""

from dataclasses import dataclass, replace
from decimal import Decimal

from tarn_core.domain.blueprint import BlueprintKind
from tarn_core.domain.content import Subject
from tarn_core.domain.tenancy import Role, Student, User
from tarn_core.errors import InvariantError, PermissionDeniedError
from tarn_core.ids import BlueprintId, CollegeId, StudentId, SubjectId, UnitId, UserId
from tarn_core.testing.blueprint_list_contract import schema_document
from tarn_core.testing.builders import (
    Backend,
    CollegeFixture,
    Services,
    add_question,
    ci_shaped_blueprint,
    make_services,
    meta,
    unit_for,
)


@dataclass
class Scenario:
    college: CollegeFixture
    services: Services
    second: User
    paper: BlueprintId
    schema: BlueprintId
    hidden_paper: BlueprintId
    hidden_schema: BlueprintId
    hidden_subject: SubjectId
    mine: UnitId
    theirs: UnitId

    @property
    def cid(self) -> CollegeId:
        return self.college.id

    @property
    def sme(self) -> UserId:
        return self.college.teacher.id


def build(mem: Backend, college: CollegeFixture) -> Scenario:
    cid, sme = college.id, college.teacher.id
    services = make_services(mem)
    paper = ci_shaped_blueprint(mem, college)
    mem.content.save(cid, replace(paper, exam_code="QP-ORBIT", meta=meta(college, 2)))
    for n in range(7):
        add_question(
            mem.content,
            college,
            mem.ids.new(),
            paper.subject_id,
            Decimal(2),
            text=f"Explain orbit number {n}.",
        )
    schema = services.blueprints.create(cid, sme, schema_document(paper.subject_id, "Orbit schema"))

    hidden = Subject(id=SubjectId(mem.ids.new()), meta=meta(college), code="SYN909", name="Hidden")
    mem.content.save(cid, hidden)  # not granted to anyone
    add_question(
        mem.content, college, mem.ids.new(), hidden.id, Decimal(2), "Orbit hidden question."
    )
    hidden_paper = replace(
        schema,
        id=BlueprintId(mem.ids.new()),
        meta=meta(college),
        kind=BlueprintKind.PAPER,
        subject_id=hidden.id,
        title="Orbit hidden paper",
        exam_code="QP-ORBIT-H",
    )
    mem.content.save(cid, hidden_paper)
    # Saved straight to the store: the service would refuse a subject the SME is not granted.
    hidden_schema = replace(
        schema,
        id=BlueprintId(mem.ids.new()),
        meta=meta(college),
        subject_id=hidden.id,
        title="Orbit hidden schema",
    )
    mem.content.save(cid, hidden_schema)

    second = User(
        id=UserId(mem.ids.new()),
        college_id=cid,
        display_name="Second SME",
        email=f"second.search.{str(cid)[:8]}@example.test",
        role=Role.SME,
    )
    mem.users.save(cid, second)
    services.entitlements.grant(cid, college.admin.id, second.id, paper.subject_id)
    mine = unit_for(mem, cid, paper.id, owner_id=sme, year="Orbit 2024")
    theirs = unit_for(mem, cid, paper.id, owner_id=second.id, year="Orbit 2025")

    def booklet(student: Student, unit: UnitId) -> None:
        services.booklets.register(
            cid,
            college.teacher.id,
            student_id=student.id,
            unit_id=unit,
            file_sha256=f"{mem.ids.new().int:064x}"[-64:],
        )

    visible = Student(
        id=StudentId(mem.ids.new()), college_id=cid, name="Mira Visible", usn="MV26A0001"
    )
    hidden_student = Student(
        id=StudentId(mem.ids.new()), college_id=cid, name="Mira Hidden", usn="MH26A0001"
    )
    for student in (visible, hidden_student):
        mem.students.save(cid, student)
    booklet(visible, mine)
    for student in college.students:
        booklet(student, mine)
    booklet(hidden_student, theirs)
    return Scenario(
        college=college,
        services=services,
        second=second,
        paper=paper.id,
        schema=schema.id,
        hidden_paper=hidden_paper.id,
        hidden_schema=hidden_schema.id,
        hidden_subject=hidden.id,
        mine=mine,
        theirs=theirs,
    )


def check(s: Scenario) -> None:
    search = s.services.search.search

    found = search(s.cid, s.sme, "mira")
    assert [h.name for h in found.students] == ["Mira Visible"]
    assert found.students[0].unit_id == s.mine
    assert [h.usn for h in search(s.cid, s.sme, "mv26 a").students] == ["MV26A0001"]
    assert search(s.cid, s.sme, "MH26").students == ()
    assert search(s.cid, s.second.id, "mira").students[0].name == "Mira Hidden"

    orbit = search(s.cid, s.sme, "  ORBIT ")
    assert [u.unit_id for u in orbit.units] == [s.mine]
    assert [p.blueprint_id for p in orbit.papers] == [s.paper]
    assert [p.blueprint_id for p in orbit.schemas] == [s.schema]
    assert len(orbit.questions) == 5  # seven match: at most five
    assert all("hidden" not in q.text.lower() for q in orbit.questions)
    assert all(q.text.lower().startswith("explain orbit") for q in orbit.questions)

    # The second SME owns the other unit: theirs, never the first SME's.
    assert [u.unit_id for u in search(s.cid, s.second.id, "orbit").units] == [s.theirs]
    # Units match their code and year; students their roll number from the start only.
    code = search(s.cid, s.sme, "orbit 2024").units
    assert [u.unit_id for u in code] == [s.mine]
    assert search(s.cid, s.sme, "26A0001").students == ()
    # LIKE wildcards are text.
    assert search(s.cid, s.sme, "%%").students == ()
    assert search(s.cid, s.sme, "__").units == ()

    for bad in ("q", " q ", "Z" * 81, "", "  "):
        try:
            search(s.cid, s.sme, bad)
        except InvariantError as error:
            assert bad.strip() == "" or bad.strip() not in str(error)
        else:
            raise AssertionError("a query of the wrong length was run")
    for persona in (s.college.admin.id,):
        try:
            search(s.cid, persona, "orbit")
        except PermissionDeniedError:
            pass
        else:
            raise AssertionError("only SMEs search")


def check_isolated(other: Scenario, s: Scenario) -> None:
    """``other`` is the same scenario built in another college: nothing of ``s`` is found."""
    found = other.services.search.search(other.cid, other.sme, "orbit")
    assert s.mine not in {u.unit_id for u in found.units}
    assert s.paper not in {p.blueprint_id for p in found.papers}
    assert s.schema not in {p.blueprint_id for p in found.schemas}
    mira = other.services.search.search(other.cid, other.sme, "mira").students
    assert [h.unit_id for h in mira] == [other.mine]


__all__ = ["Scenario", "build", "check", "check_isolated"]
