"""Units of work (P26, D162): invariants, creation by the admin, the SME's email, who sees
which unit and booklet, exam codes, and the multi-file upload by roll number."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import Unit, normalise_year, unit_code
from tarn_core.errors import (
    AlreadyExistsError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
)
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UnitId, UserId
from tarn_core.services.access import AccessService
from tarn_core.services.blueprint_document import blueprint_to_document, check_document
from tarn_core.services.unit_upload import (
    FileOutcome,
    FileProblem,
    NamedFile,
    UnitUploadService,
    parse_usn,
)
from tarn_core.services.units import UnitService
from tarn_core.services.uploads import UploadLimits, UploadService
from tarn_core.testing import InMemory, MemoryMailer, fake_pdf
from tarn_core.testing.builders import (
    CollegeFixture,
    Services,
    add_college,
    ci_shaped_blueprint,
    make_services,
    unit_for,
)

NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
DUE = date(2026, 11, 1)


class World:
    def __init__(self, max_waiting: int = 5) -> None:
        self.mem = InMemory()
        self.mail = MemoryMailer()
        self.svc: Services = make_services(self.mem, mailer=self.mail)
        self.college: CollegeFixture = add_college(self.mem, "A")
        self.other: CollegeFixture = add_college(self.mem, "B")
        self.paper = coded(self.mem, self.college, ci_shaped_blueprint(self.mem, self.college))
        self.second_sme = self.add_sme("Second SME")
        limits = UploadLimits(max_waiting=max_waiting)
        uploads = UploadService(
            booklets=self.svc.booklets,
            repository=self.mem.booklets,
            blobs=self.mem.blobs,
            jobs=self.mem.jobs,
            runtime=self.mem.runtime,
            limits=limits,
        )
        self.upload = UnitUploadService(
            uploads=uploads,
            booklet_service=self.svc.booklets,
            units=self.mem.units,
            booklets=self.mem.booklets,
            students=self.mem.students,
            users=self.mem.users,
            runtime=self.mem.runtime,
            limits=limits,
        )

    @property
    def units(self) -> UnitService:
        return self.svc.units

    def add_sme(self, name: str, *, subjects: bool = True) -> User:
        sme = User(
            id=UserId(self.mem.ids.new()),
            college_id=self.college.id,
            display_name=name,
            email=f"{name.lower().replace(' ', '.')}@example.test",
            role=Role.SME,
        )
        self.mem.users.save(self.college.id, sme)
        if subjects:
            self.svc.entitlements.grant(
                self.college.id, self.college.admin.id, sme.id, self.paper.subject_id
            )
        return sme

    def create(self, **change: object) -> Unit:
        args: dict[str, object] = {
            "paper_id": self.paper.id,
            "year": "2024",
            "owner_id": self.college.teacher.id,
            "due_date": DUE,
        }
        args.update(change)
        return self.units.create(self.college.id, self.college.admin.id, **args)  # type: ignore[arg-type]

    def usn(self, n: int) -> str:
        return self.college.students[n].usn


def coded(
    mem: InMemory, college: CollegeFixture, paper: ExamBlueprint, code: str = "SYN-CI-1"
) -> ExamBlueprint:
    """The paper's next version, with an exam code."""
    edited = replace(
        paper,
        exam_code=code,
        course_code="SYN",
        meta=replace(paper.meta, version=paper.meta.version + 1),
    )
    mem.content.save(college.id, edited)
    return edited


@pytest.fixture
def w() -> World:
    return World()


# --- the unit itself ------------------------------------------------------------------------------


def _unit(**change: object) -> Unit:
    base: dict[str, object] = {
        "id": UnitId(uuid4()),
        "college_id": CollegeId(uuid4()),
        "code": "U-0001",
        "paper_id": BlueprintId(uuid4()),
        "subject_id": SubjectId(uuid4()),
        "year": "2024",
        "owner_id": UserId(uuid4()),
        "due_date": DUE,
        "created_by": None,
        "created_at": NOW,
    }
    base.update(change)
    return Unit(**base)  # type: ignore[arg-type]


def test_unit_invariants() -> None:
    assert _unit().code == "U-0001"
    assert unit_code(12) == "U-0012" and unit_code(12345) == "U-12345"
    with pytest.raises(InvariantError):
        unit_code(0)
    assert normalise_year("  2024   batch ") == "2024 batch"
    for bad in (
        {"code": "U-1"},
        {"code": "u-0001"},
        {"year": ""},
        {"year": " 2024"},
        {"year": "x" * 21},
        {"created_at": NOW.replace(tzinfo=None)},
        {"version": 0},
    ):
        with pytest.raises(InvariantError):
            _unit(**bad)


# --- creating units ---------------------------------------------------------------------------


def test_the_admin_creates_a_unit_and_the_sme_is_emailed(w: World) -> None:
    unit = w.create(year="  2024 ")
    assert (unit.code, unit.year, unit.subject_id) == ("U-0001", "2024", w.paper.subject_id)
    assert unit.owner_id == w.college.teacher.id and unit.created_by == w.college.admin.id
    assert w.create(year="2025").code == "U-0002"
    [first, _] = w.mail.sent
    assert first.to == w.college.teacher.email
    assert "U-0001" in first.text and w.paper.title in first.text and "SYN-CI-1" in first.text
    assert DUE.isoformat() in first.text
    for student in w.college.students:  # no student data in the email
        assert student.usn not in first.text and student.name not in first.text
    [event, _] = [e for e in w.mem.audit.events if e.action is AuditAction.UNIT_CREATED]
    assert event.actor_id == w.college.admin.id
    assert event.after["code"] == "U-0001"  # type: ignore[index, call-overload]


def test_a_unit_needs_an_entitled_active_sme(w: World) -> None:
    outsider = w.add_sme("No Subject", subjects=False)
    suspended = w.add_sme("Suspended SME")
    w.mem.users.save(w.college.id, replace(suspended, active=False))
    for owner in (outsider.id, suspended.id, w.college.admin.id, w.other.teacher.id):
        with pytest.raises(InvariantError, match="active SME who has"):
            w.create(owner_id=owner)
    assert w.mem.units.count(w.college.id) == 0
    assert w.mail.sent == []
    offered = w.units.entitled_smes(w.college.id, w.college.admin.id, w.paper.subject_id)
    assert {u.id for u in offered} == {w.college.teacher.id, w.second_sme.id}


def test_a_unit_needs_a_usable_paper_a_year_and_a_due_date(w: World) -> None:
    raw = ci_shaped_blueprint(w.mem, w.college)  # no exam code
    with pytest.raises(InvariantError, match="exam code"):
        w.create(paper_id=raw.id)
    unlinked = coded(w.mem, w.college, raw, "RAW")
    first = unlinked.sections[0]
    slot = replace(first.items[0], question_id=None)  # type: ignore[arg-type]
    gapped = replace(
        unlinked,
        sections=(replace(first, items=(slot, *first.items[1:])), *unlinked.sections[1:]),
        meta=replace(unlinked.meta, version=unlinked.meta.version + 1),
    )
    w.mem.content.save(w.college.id, gapped)
    with pytest.raises(InvariantError, match="Link every question"):
        w.create(paper_id=raw.id)
    with pytest.raises(InvariantError, match="student year"):
        w.create(year="   ")
    with pytest.raises(InvariantError, match="past"):
        w.create(due_date=NOW.date() - timedelta(days=1))
    assert w.create(due_date=NOW.date()).due_date == NOW.date()  # today is fine
    with pytest.raises(AlreadyExistsError):
        w.create(year="2024 ".upper())
    with pytest.raises(NotFoundError):
        w.create(paper_id=ci_shaped_blueprint(w.mem, w.other).id)  # another college's paper


def test_only_the_admin_creates_units(w: World) -> None:
    with pytest.raises(PermissionDeniedError):
        w.units.create(
            w.college.id,
            w.college.teacher.id,
            paper_id=w.paper.id,
            year="2024",
            owner_id=w.college.teacher.id,
            due_date=DUE,
        )
    with pytest.raises(PermissionDeniedError):
        w.units.papers(w.college.id, w.college.teacher.id)


def test_papers_offered_are_linked_coded_and_live(w: World) -> None:
    ci_shaped_blueprint(w.mem, w.college)  # no exam code: not offered
    offered = w.units.papers(w.college.id, w.college.admin.id)
    assert [c.paper.id for c in offered] == [w.paper.id]
    assert w.units.papers(w.college.id, w.college.admin.id, SubjectId(w.mem.ids.new())) == []


def test_the_sme_sees_their_own_units_only(w: World) -> None:
    mine = w.create()
    theirs = w.create(year="2025", owner_id=w.second_sme.id)
    admin, sme = w.college.admin.id, w.college.teacher.id
    assert {u.id for u in w.units.list(w.college.id, admin)} == {mine.id, theirs.id}
    assert [u.id for u in w.units.list(w.college.id, admin, owner_id=w.second_sme.id)] == [
        theirs.id
    ]
    assert [u.id for u in w.units.list(w.college.id, sme, owner_id=w.second_sme.id)] == [mine.id]
    assert w.units.get(w.college.id, sme, mine.id) == mine
    with pytest.raises(NotFoundError):
        w.units.get(w.college.id, sme, theirs.id)
    with pytest.raises(NotFoundError):
        w.units.get(w.other.id, w.other.admin.id, mine.id)


# --- exam codes -------------------------------------------------------------------------------


def test_exam_codes_are_unique_per_college_and_documents_round_trip(w: World) -> None:
    document = blueprint_to_document(w.paper)
    assert document["schema_version"] == "1.1" and document["exam_code"] == "SYN-CI-1"
    old = {**document, "schema_version": "1.0"}
    del old["exam_code"]
    report = check_document(old)
    assert report.valid and report.parsed is not None and report.parsed.exam_code == ""
    with pytest.raises(AlreadyExistsError, match="exam_code"):
        w.svc.blueprints.create(
            w.college.id, w.college.teacher.id, {**document, "exam_code": "syn-ci-1"}
        )
    again = w.svc.blueprints.update(w.college.id, w.college.teacher.id, w.paper.id, document)
    assert again.exam_code == "SYN-CI-1"  # its own code is not a clash
    assert check_document({**document, "exam_code": "x" * 51}).issues


def test_a_paper_used_by_a_unit_keeps_its_subject(w: World) -> None:
    w.create()
    other = ci_shaped_blueprint(w.mem, w.college)  # makes and grants another subject
    document = {**blueprint_to_document(w.paper), "subject_id": str(other.subject_id)}
    with pytest.raises(IllegalTransitionError, match="subject cannot change"):
        w.svc.blueprints.update(w.college.id, w.college.teacher.id, w.paper.id, document)


# --- file names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "usn"),
    [
        ("1XX21CS001.pdf", "1XX21CS001"),
        ("1xx21cs001.PDF", "1XX21CS001"),
        ("  1XX21CS001 .pdf ", "1XX21CS001"),
        ("1XX 21CS 001.pdf", "1XX21CS001"),
        ("scans/2024/1XX21CS001.pdf", "1XX21CS001"),
        ("C:\\scans\\1XX21CS001.pdf", "1XX21CS001"),
        ("../../1XX21CS001.pdf", "1XX21CS001"),
        # Full-width forms fold to ASCII.
        ("\uff11\uff38\uff38\uff12\uff11\uff23\uff33\uff10\uff10\uff11.pdf", "1XX21CS001"),
        ("1XX21CS001", None),
        ("1XX21CS001.jpg", None),
        ("1XX21CS001.pdf.pdf", None),
        ("1XX21C\u0405001.pdf", None),  # a Cyrillic letter
        # Letters that upper-case into ASCII must not pass as ASCII (P30, F18).
        ("1xx21cs00\u0131.pdf", None),  # dotless i -> "I"
        ("1xx21c\u00df001.pdf", None),  # sharp s -> "SS"
        ("1xx21cs00\u0131\u0307.pdf", None),
        ("1XX-21CS001.pdf", None),
        ("..pdf", None),
        (".pdf", None),
        ("", None),
        ("ABC.pdf", None),
        ("A" * 300 + ".pdf", None),
    ],
)
def test_file_names_give_the_roll_number(name: str, usn: str | None) -> None:
    assert parse_usn(name) == usn


# --- the upload --------------------------------------------------------------------------------


def _files(*pairs: tuple[str, bytes]) -> list[NamedFile]:
    return [NamedFile(name=n, data=d) for n, d in pairs]


def test_each_file_is_checked_and_only_valid_ones_are_stored(w: World) -> None:
    unit = w.create()
    known = w.usn(0)
    report = w.upload.upload(
        w.college.id,
        w.college.admin.id,
        unit.id,
        _files(
            (f"{known.lower()}.pdf", fake_pdf(2, "a")),
            ("NOBODY0001.pdf", fake_pdf(2, "b")),
            (f"{w.usn(1)}.docx", fake_pdf(2, "c")),
            (f"{w.usn(1)}.pdf", b"PK\x03\x04 not a pdf"),
            (f"{w.usn(2)}.pdf", b""),
            ("not a roll number!.pdf", fake_pdf(2, "d")),
        ),
    )
    by_name = {r.name: r for r in report.results}
    assert by_name[f"{known.lower()}.pdf"].outcome is FileOutcome.QUEUED
    assert by_name[f"{known.lower()}.pdf"].usn == known
    problems = {r.name: r.problem for r in report.results if r.outcome is FileOutcome.REFUSED}
    assert problems == {
        "NOBODY0001.pdf": FileProblem.UNKNOWN_USN,
        f"{w.usn(1)}.docx": FileProblem.NOT_PDF,
        f"{w.usn(1)}.pdf": FileProblem.NOT_PDF,
        f"{w.usn(2)}.pdf": FileProblem.EMPTY,
        "not a roll number!.pdf": FileProblem.BAD_NAME,
    }
    [booklet] = w.mem.booklets.in_unit(w.college.id, unit.id)
    assert booklet.student_id == w.college.students[0].id
    assert booklet.blueprint == w.paper.ref  # pinned to the paper's current version
    assert len(w.mem.jobs.jobs) == 1
    [event] = [e for e in w.mem.audit.events if e.action is AuditAction.UNIT_UPLOADED]
    assert event.after == {"unit_id": str(unit.id), "queued": 1, "refused": 5, "not_queued": 0}
    assert known not in repr(event)  # no roll number or file name in the audit log


def test_repeats_in_one_upload_and_in_the_unit_are_refused(w: World) -> None:
    unit = w.create()
    first = w.upload.upload(
        w.college.id, w.college.admin.id, unit.id, _files((f"{w.usn(0)}.pdf", fake_pdf(1, "x")))
    )
    assert first.count(FileOutcome.QUEUED) == 1
    report = w.upload.upload(
        w.college.id,
        w.college.admin.id,
        unit.id,
        _files(
            (f"{w.usn(0)}.pdf", fake_pdf(1, "y")),  # already in the unit
            (f"{w.usn(1)}.pdf", fake_pdf(1, "p")),
            (f"{w.usn(1).lower()}.PDF", fake_pdf(1, "q")),  # the same roll number twice
            (f"{w.usn(2)}.pdf", fake_pdf(1, "x")),  # the file already uploaded
        ),
    )
    assert [r.problem for r in report.results] == [
        FileProblem.ALREADY_IN_UNIT,
        FileProblem.USN_TWICE,
        FileProblem.USN_TWICE,
        FileProblem.UPLOADED_BEFORE,
    ]
    other = w.create(year="2025")
    same = w.upload.upload(
        w.college.id,
        w.college.admin.id,
        other.id,
        _files((f"{w.usn(1)}.pdf", fake_pdf(1, "z")), (f"{w.usn(2)}.pdf", fake_pdf(1, "z"))),
    )
    assert [r.problem for r in same.results] == [FileProblem.SAME_FILE_TWICE] * 2


def test_files_beyond_the_free_queue_slots_are_not_queued() -> None:
    w = World(max_waiting=2)
    unit = w.create()
    report = w.upload.upload(
        w.college.id,
        w.college.admin.id,
        unit.id,
        _files(*((f"{w.usn(n)}.pdf", fake_pdf(1, str(n))) for n in range(3))),
    )
    assert [r.outcome for r in report.results] == [
        FileOutcome.QUEUED,
        FileOutcome.QUEUED,
        FileOutcome.NOT_QUEUED,
    ]
    assert report.results[2].problem is FileProblem.QUEUE_FULL
    assert len(w.mem.booklets.in_unit(w.college.id, unit.id)) == 2


def test_only_the_admin_uploads_and_only_into_their_college(w: World) -> None:
    unit = w.create()
    files = _files((f"{w.usn(0)}.pdf", fake_pdf(1)))
    with pytest.raises(PermissionDeniedError):
        w.upload.upload(w.college.id, w.college.teacher.id, unit.id, files)
    with pytest.raises(NotFoundError):
        w.upload.upload(w.other.id, w.other.admin.id, unit.id, files)


def test_a_retake_replaces_the_booklet_in_one_step(w: World) -> None:
    unit = w.create()
    w.upload.upload(
        w.college.id, w.college.admin.id, unit.id, _files((f"{w.usn(0)}.pdf", fake_pdf(1, "o")))
    )
    [old] = w.mem.booklets.in_unit(w.college.id, unit.id)
    with pytest.raises(IllegalTransitionError):  # still uploaded: nothing to retake
        w.upload.replace(w.college.id, w.college.admin.id, old.id, fake_pdf(1, "n"))
    w.mem.booklets.save(
        w.college.id,
        replace(old, status=BookletStatus.NEEDS_RETAKE, version=old.version + 1),
    )
    with pytest.raises(InvariantError, match="Not a PDF"):
        w.upload.replace(w.college.id, w.college.admin.id, old.id, b"GIF89a")
    assert w.mem.booklets.get(w.college.id, old.id)  # nothing deleted
    new_id = w.upload.replace(w.college.id, w.college.admin.id, old.id, fake_pdf(1, "n"))
    [new] = w.mem.booklets.in_unit(w.college.id, unit.id)
    assert new.id == new_id and new.student_id == old.student_id
    with pytest.raises(NotFoundError):
        w.mem.booklets.get(w.college.id, old.id)
    actions = [e.action for e in w.mem.audit.events]
    assert AuditAction.BOOKLET_DELETED in actions and AuditAction.BOOKLET_REPLACED in actions


# --- who sees which booklet ---------------------------------------------------------------------


def test_an_sme_sees_only_the_booklets_of_their_units(w: World) -> None:
    mine = w.create()
    theirs = w.create(year="2025", owner_id=w.second_sme.id)
    for unit, n in ((mine, 0), (theirs, 1)):
        w.upload.upload(
            w.college.id,
            w.college.admin.id,
            unit.id,
            _files((f"{w.usn(n)}.pdf", fake_pdf(1, str(n)))),
        )
    access = AccessService(
        users=w.mem.users,
        entitlements=w.mem.entitlements,
        content=w.mem.content,
        booklets=w.mem.booklets,
        units=w.mem.units,
    )
    every = w.mem.booklets.list(w.college.id)
    sme_reach = access.reach(w.college.id, w.college.teacher.id)
    seen = access.visible(w.college.id, sme_reach, every)
    assert [b.unit_id for b in seen] == [mine.id]
    [their_booklet] = w.mem.booklets.in_unit(w.college.id, theirs.id)
    with pytest.raises(NotFoundError):  # same subject, another SME's unit
        access.booklet(w.college.id, sme_reach, their_booklet.id)
    admin_reach = access.reach(w.college.id, w.college.admin.id)
    assert len(access.visible(w.college.id, admin_reach, every)) == 2
    assert [b.unit_id for b in w.units.booklets(w.college.id, w.college.admin.id, theirs.id)] == [
        theirs.id
    ]
    with pytest.raises(NotFoundError):
        w.units.booklets(w.college.id, w.college.teacher.id, theirs.id)


def test_the_unit_builder_reuses_a_unit(w: World) -> None:
    first = unit_for(w.mem, w.college.id, w.paper.id)
    assert unit_for(w.mem, w.college.id, w.paper.id) == first
