"""Deleting a question or a paper for good (P28, D166): refused while a unit that is not frozen
uses it (or, for a question, while a paper links it); allowed after the freeze. The frozen
record, the issued result sheet PDFs and the score rows stay; the audit holds no content."""

from dataclasses import replace

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, QuestionSlot, Section
from tarn_core.domain.booklet import Booklet
from tarn_core.domain.content import KeyFile, Question
from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import (
    ContentDeletedError,
    IllegalTransitionError,
    NotFoundError,
    PermissionDeniedError,
)
from tarn_core.ids import CollegeId, QuestionId, UnitId, UserId
from tarn_core.services.access import WHOLE_COLLEGE
from tarn_core.services.evaluated import EvaluatedBooklets
from tarn_core.testing import InMemory, MemoryMailer
from tarn_core.testing.builders import (
    CollegeFixture,
    Services,
    add_college,
    ci_shaped_blueprint,
    make_services,
    unit_for,
)
from tarn_core.testing.workflow import GOOD, HALF, Workflow, scored_booklet, workflow

PDF = b"%PDF-1.7\nsynthetic key"


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.svc: Services = make_services(self.mem, mailer=MemoryMailer())
        self.college: CollegeFixture = add_college(self.mem, "A")
        self.paper = ci_shaped_blueprint(self.mem, self.college)
        self.wf: Workflow = workflow(self.mem)
        self.governor = self._user("Governor", Role.GOVERNOR)
        self.q1 = QuestionId(self.paper.leaf("1")[1])
        self.key = self.svc.bank.upload_key_file(
            self.cid,
            self.sme,
            self.q1,
            file_name="key.pdf",
            data=PDF,
            declared_type="application/pdf",
            no_student_data=True,
        )
        self.booklet = self._approved()

    @property
    def cid(self) -> CollegeId:
        return self.college.id

    @property
    def sme(self) -> UserId:
        return self.college.teacher.id

    def _user(self, name: str, role: Role) -> User:
        user = User(
            id=UserId(self.mem.ids.new()),
            college_id=self.cid,
            display_name=name,
            email=f"{name.lower()}@example.test",
            role=role,
        )
        self.mem.users.save(self.cid, user)
        return user

    def _approved(self) -> Booklet:
        booklet = scored_booklet(self.mem, self.college, self.paper, {"1": GOOD, "2": HALF})
        self.wf.review.open(self.cid, self.sme, booklet.id)
        for a in self.mem.booklets.answers(self.cid, booklet.id):
            self.wf.review.approve_answer(
                self.cid, self.sme, booklet.id, a.id, expected_version=a.version
            )
        current = self.mem.booklets.get(self.cid, booklet.id)
        self.wf.review.approve_booklet(
            self.cid, self.sme, booklet.id, expected_version=current.version
        )
        self.wf.review.close(self.cid, self.sme, booklet.id)
        return self.mem.booklets.get(self.cid, booklet.id)

    def unit_id(self) -> UnitId:
        return unit_for(self.mem, self.cid, self.paper.id)

    def submit(self) -> None:
        self.svc.unit_flow.submit(self.cid, self.sme, self.unit_id(), unmarked=[])

    def freeze(self) -> None:
        self.svc.unit_flow.freeze(self.cid, self.governor.id, self.unit_id())

    def evaluated(self) -> EvaluatedBooklets:
        m = self.mem
        return EvaluatedBooklets(
            booklets=m.booklets,
            students=m.students,
            content=m.content,
            sheets=m.sheets,
            blobs=m.blobs,
            units=m.units,
        )


@pytest.fixture
def w() -> World:
    return World()


def test_a_paper_is_deleted_only_after_the_freeze(w: World) -> None:
    deletion = w.svc.deletion
    check = deletion.check_paper(w.cid, w.sme, w.paper.id)
    assert check.blockers == ("Unit U-0001 (open) uses it.",)
    with pytest.raises(IllegalTransitionError, match="U-0001"):
        deletion.delete_paper(w.cid, w.sme, w.paper.id)
    w.submit()
    check = deletion.check_paper(w.cid, w.sme, w.paper.id)
    assert check.blockers == ("Unit U-0001 (submitted, not yet frozen) uses it.",)
    w.freeze()
    assert deletion.check_paper(w.cid, w.sme, w.paper.id).allowed
    assert deletion.delete_paper(w.cid, w.sme, w.paper.id) == 1
    with pytest.raises(NotFoundError):
        w.mem.content.get(w.cid, ExamBlueprint, w.paper.id)
    event = w.mem.audit.events[-1]
    assert event.action is AuditAction.CONTENT_DELETED
    assert event.before == {"kind": "blueprint", "id": str(w.paper.id), "versions": 1}
    assert w.paper.title not in str(event.before)


def test_the_frozen_record_and_sheets_outlive_the_paper(w: World) -> None:
    w.submit()
    w.freeze()
    sheet = w.mem.sheets.versions(w.cid, w.booklet.id)[-1]
    w.svc.deletion.delete_paper(w.cid, w.sme, w.paper.id)
    record = w.svc.unit_flow.frozen_record(w.cid, w.governor.id, w.unit_id())
    assert record.papers[0]["title"] == w.paper.title
    assert record.booklets[0].total == sheet.total
    # The evaluated list names the paper from the frozen record; the PDF still downloads.
    found = w.evaluated().search(w.cid, reach=WHOLE_COLLEGE)
    assert [(e.booklet.id, e.exam) for e in found.items] == [(w.booklet.id, w.paper.title)]
    stored, pdf = w.evaluated().pdf(w.cid, w.booklet.id, sheet.version, reach=WHOLE_COLLEGE)
    assert stored.version == sheet.version and pdf
    # The units list still shows the paper; the review says the content is gone.
    assert w.svc.units.get(w.cid, w.sme, w.unit_id()).paper_id == w.paper.id
    with pytest.raises(ContentDeletedError):
        w.wf.review.view(w.cid, w.booklet.id)
    # Score rows keep naming the deleted versions (no foreign key, D171).
    answer = w.mem.booklets.answers(w.cid, w.booklet.id)[0]
    assert w.mem.scores.scores(w.cid, answer.id)


def test_a_question_is_deleted_once_no_paper_links_it(w: World) -> None:
    deletion = w.svc.deletion
    blockers = deletion.check_question(w.cid, w.sme, w.q1).blockers
    assert any("links it" in b for b in blockers) and any("U-0001" in b for b in blockers)
    w.submit()
    w.freeze()
    # Frozen, but the paper still links it: unlink it there or delete the paper first.
    (blocker,) = deletion.check_question(w.cid, w.sme, w.q1).blockers
    assert "links it" in blocker
    with pytest.raises(IllegalTransitionError):
        deletion.delete_question(w.cid, w.sme, w.q1)
    deletion.delete_paper(w.cid, w.sme, w.paper.id)
    assert deletion.check_question(w.cid, w.sme, w.q1).allowed
    rows = deletion.delete_question(w.cid, w.sme, w.q1)
    assert rows >= 3  # the question, its rubric criterion, its key file
    with pytest.raises(NotFoundError):
        w.mem.content.get(w.cid, Question, w.q1)
    assert w.mem.content.question_parts(w.cid, w.q1) == []
    with pytest.raises(NotFoundError):
        w.mem.content.get(w.cid, KeyFile, w.key.id)
    assert not w.mem.blobs.exists(w.key.blob)
    event = w.mem.audit.events[-1]
    assert event.action is AuditAction.CONTENT_DELETED
    assert event.before == {"kind": "question", "id": str(w.q1), "rows": rows, "files": 1}
    record = w.mem.units.frozen(w.cid, w.unit_id())
    assert record is not None and any(a.question_id == w.q1 for a in record.booklets[0].answers)


def test_a_question_an_unfrozen_booklet_used_stays(w: World) -> None:
    """The latest paper no longer links it, but a booklet of an open unit is pinned to the
    version that did and was scored on it."""
    other = w.paper.leaf("2")[1]
    sections = list(w.paper.sections)
    first = sections[0]
    items = tuple(
        replace(i, question_id=other) if isinstance(i, QuestionSlot) and i.label == "1" else i
        for i in first.items
    )
    sections[0] = Section(label=first.label, rule=first.rule, items=items)
    unlinked = replace(
        w.paper,
        meta=replace(w.paper.meta, version=2),
        sections=tuple(sections),
    )
    w.mem.content.save(w.cid, unlinked)
    (blocker,) = w.svc.deletion.check_question(w.cid, w.sme, w.q1).blockers
    assert blocker == "Unit U-0001 (open) uses it."


def test_only_an_entitled_sme_deletes(w: World) -> None:
    admin = w.college.admin.id
    with pytest.raises(PermissionDeniedError):
        w.svc.deletion.check_paper(w.cid, admin, w.paper.id)
    with pytest.raises(PermissionDeniedError):
        w.svc.deletion.delete_question(w.cid, admin, w.q1)
    stranger = w._user("Stranger", Role.SME)
    with pytest.raises(NotFoundError):
        w.svc.deletion.delete_paper(w.cid, stranger.id, w.paper.id)
    other = add_college(w.mem, "B")
    with pytest.raises(NotFoundError):
        w.svc.deletion.delete_question(other.id, other.teacher.id, w.q1)
