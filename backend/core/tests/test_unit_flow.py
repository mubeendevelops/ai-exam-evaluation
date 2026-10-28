"""Submit, return and freeze (P28, D164): the SME submits the whole unit, every booklet approved
or with a reason; nothing in it changes afterwards; the admin may return it with a note until
the Governor freezes it; the freeze writes the frozen record and closes the unit for good."""

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import AnswerStatus, Booklet, BookletStatus
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import (
    FrozenOutcome,
    Unit,
    UnitState,
    UnmarkedBooklet,
    UnmarkedReason,
)
from tarn_core.errors import (
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    PermissionDeniedError,
    StaleWriteError,
    UnitClosedError,
)
from tarn_core.ids import CollegeId, UserId
from tarn_core.services.scoring import BookletScorer
from tarn_core.services.unit_guard import FROZEN, SUBMITTED
from tarn_core.services.unit_upload import NamedFile, UnitUploadService
from tarn_core.services.uploads import UploadService
from tarn_core.testing import InMemory, MemoryMailer, fake_pdf
from tarn_core.testing.builders import (
    CollegeFixture,
    Services,
    add_college,
    ci_shaped_blueprint,
    make_services,
    unit_for,
)
from tarn_core.testing.workflow import (
    GOOD,
    HALF,
    Workflow,
    booklet_scorer,
    scored_booklet,
    workflow,
)

NOTE = "Q3 of 1XX21CS001 needs a second look"


class World:
    def __init__(self) -> None:
        self.mem = InMemory()
        self.mail = MemoryMailer()
        self.svc: Services = make_services(self.mem, mailer=self.mail)
        self.college: CollegeFixture = add_college(self.mem, "A")
        self.paper = ci_shaped_blueprint(self.mem, self.college)
        self.wf: Workflow = workflow(self.mem)
        self.governor = User(
            id=UserId(self.mem.ids.new()),
            college_id=self.cid,
            display_name="Governor",
            email="governor@example.test",
            role=Role.GOVERNOR,
        )
        self.mem.users.save(self.cid, self.governor)

    @property
    def cid(self) -> CollegeId:
        return self.college.id

    @property
    def sme(self) -> UserId:
        return self.college.teacher.id

    @property
    def admin(self) -> UserId:
        return self.college.admin.id

    def unit(self) -> Unit:
        return self.mem.units.get(self.cid, unit_for(self.mem, self.cid, self.paper.id))

    def approved(self) -> Booklet:
        booklet = scored_booklet(self.mem, self.college, self.paper, {"1": GOOD, "2": HALF})
        self.wf.review.open(self.cid, self.sme, booklet.id)
        for a in self.mem.booklets.answers(self.cid, booklet.id):
            self.wf.review.approve_answer(
                self.cid, self.sme, booklet.id, a.id, expected_version=a.version
            )
        self.wf.review.approve_booklet(
            self.cid,
            self.sme,
            booklet.id,
            expected_version=self.mem.booklets.get(self.cid, booklet.id).version,
        )
        return self.mem.booklets.get(self.cid, booklet.id)

    def in_review(self) -> Booklet:
        booklet = scored_booklet(self.mem, self.college, self.paper, {"1": GOOD})
        self.wf.review.open(self.cid, self.sme, booklet.id)
        return self.mem.booklets.get(self.cid, booklet.id)

    def submit(self, *unmarked: UnmarkedBooklet, version: int | None = None) -> Unit:
        return self.svc.unit_flow.submit(
            self.cid, self.sme, self.unit().id, unmarked=list(unmarked), expected_version=version
        )

    def actions(self) -> list[AuditAction]:
        return [e.action for e in self.mem.audit.events if e.college_id == self.cid]


@pytest.fixture
def w() -> World:
    return World()


def _absent(booklet: Booklet, note: str = "") -> UnmarkedBooklet:
    return UnmarkedBooklet(booklet_id=booklet.id, reason=UnmarkedReason.ABSENT, note=note)


# --- the state machine ---------------------------------------------


def test_the_unit_state_machine(w: World) -> None:
    unit = w.unit()
    submitted = unit.moved_to(UnitState.SUBMITTED)
    assert submitted.version == unit.version + 1
    assert submitted.moved_to(UnitState.OPEN).state is UnitState.OPEN  # a return
    frozen = submitted.moved_to(UnitState.FROZEN)
    for illegal in (
        (unit, UnitState.FROZEN),
        (unit, UnitState.OPEN),
        (submitted, UnitState.SUBMITTED),
        (frozen, UnitState.OPEN),
        (frozen, UnitState.SUBMITTED),
        (frozen, UnitState.FROZEN),
    ):
        with pytest.raises(IllegalTransitionError):
            illegal[0].moved_to(illegal[1])


def test_unmarked_reasons_need_a_note_for_other() -> None:
    mem = InMemory()
    booklet_id = mem.ids.new()
    with pytest.raises(InvariantError):
        UnmarkedBooklet(booklet_id=booklet_id, reason=UnmarkedReason.OTHER)  # type: ignore[arg-type]
    with pytest.raises(InvariantError):
        UnmarkedBooklet(booklet_id=booklet_id, reason=UnmarkedReason.OTHER, note="x" * 501)  # type: ignore[arg-type]
    assert UnmarkedBooklet(booklet_id=booklet_id, reason=UnmarkedReason.OTHER, note="Torn")  # type: ignore[arg-type]


# --- submit ---------------------------------------------


def test_submit_is_refused_until_every_booklet_is_marked_or_has_a_reason(w: World) -> None:
    with pytest.raises(IllegalTransitionError, match="no booklets"):
        w.submit()
    done = w.approved()
    busy = w.in_review()
    with pytest.raises(IllegalTransitionError, match="Give a reason"):
        w.submit()
    with pytest.raises(InvariantError, match="needs no reason"):
        w.submit(_absent(done), _absent(busy))
    # A booklet still in the machine stages: wait for it.
    waiting = w.svc.booklets.register(
        w.cid,
        w.admin,
        student_id=w.college.students[1].id,
        unit_id=w.unit().id,
        file_sha256="a" * 64,
    ).booklet
    with pytest.raises(IllegalTransitionError, match="still being processed"):
        w.submit(_absent(busy), _absent(waiting))
    assert w.unit().state is UnitState.OPEN
    assert AuditAction.UNIT_SUBMITTED not in w.actions()


def test_submit_is_refused_with_an_amendment_in_progress(w: World) -> None:
    done = w.approved()
    answer = w.mem.booklets.answers(w.cid, done.id)[0]
    w.wf.review.reopen(w.cid, w.sme, done.id, answer.id, expected_version=answer.version)
    with pytest.raises(IllegalTransitionError, match="amendment in progress"):
        w.submit()


def test_only_the_owner_submits_and_with_the_loaded_version(w: World) -> None:
    w.approved()
    unit = w.unit()
    with pytest.raises(PermissionDeniedError):
        w.svc.unit_flow.submit(w.cid, w.admin, unit.id, unmarked=[])
    other = User(
        id=UserId(w.mem.ids.new()),
        college_id=w.cid,
        display_name="Other SME",
        email="other@example.test",
        role=Role.SME,
    )
    w.mem.users.save(w.cid, other)
    with pytest.raises(NotFoundError):
        w.svc.unit_flow.submit(w.cid, other.id, unit.id, unmarked=[])
    with pytest.raises(StaleWriteError):
        w.submit(version=unit.version - 1 if unit.version > 1 else 99)


def test_submit_hands_the_unit_over_and_releases_the_locks(w: World) -> None:
    w.approved()
    busy = w.in_review()
    assert w.mem.booklets.lock(w.cid, busy.id) is not None
    unit = w.unit()
    submitted = w.submit(_absent(busy, "Left after ten minutes"), version=unit.version)
    assert submitted.state is UnitState.SUBMITTED and submitted.version == unit.version + 1
    assert w.mem.booklets.lock(w.cid, busy.id) is None
    (submission,) = w.mem.units.submissions(w.cid, unit.id)
    assert submission.unmarked == (_absent(busy, "Left after ten minutes"),)
    event = next(e for e in w.mem.audit.events if e.action is AuditAction.UNIT_SUBMITTED)
    assert event.after is not None and "Left after" not in str(event.after)  # notes stay out
    assert isinstance(event.after, dict) and event.after["unmarked"] == {
        "absent": 1,
        "unreadable": 0,
        "other": 0,
    }


# --- nothing changes after submission ---------------------------------------------


def _every_write(w: World, booklet: Booklet) -> list[object]:
    """Call each kind of write on a booklet of the unit; return what each raised."""
    cid, sme = w.cid, w.sme
    answer = w.mem.booklets.answers(cid, booklet.id)[0]
    region = next(
        r for p in w.mem.booklets.pages(cid, booklet.id) for r in w.mem.booklets.regions(cid, p.id)
    )
    segment = w.mem.booklets.segments(cid, booklet.id)[0]
    calls: list[Callable[[], object]] = [
        lambda: w.wf.review.open(cid, sme, booklet.id),
        lambda: w.wf.review.approve_answer(
            cid, sme, booklet.id, answer.id, expected_version=answer.version
        ),
        lambda: w.wf.review.skip(cid, sme, booklet.id, answer.id, expected_version=answer.version),
        lambda: w.wf.review.reopen(
            cid, sme, booklet.id, answer.id, expected_version=answer.version
        ),
        lambda: w.wf.review.approve_booklet(cid, sme, booklet.id, expected_version=booklet.version),
        lambda: w.wf.text.correct(
            cid, sme, booklet.id, region.id, expected_version=booklet.version, text="changed"
        ),
        lambda: w.wf.segments.reassign(
            cid,
            sme,
            booklet.id,
            expected_version=booklet.version,
            segment_id=segment.id,
            label=None,
        ),
        lambda: w.wf.resegment.request(cid, sme, booklet.id, expected_version=booklet.version),
        lambda: w.svc.booklets.delete(cid, sme, booklet.id),
        lambda: w.svc.booklets.register(
            cid,
            w.admin,
            student_id=w.college.students[1].id,
            unit_id=booklet.unit_id,
            file_sha256="b" * 64,
        ),
        lambda: booklet_scorer_with_units(w).rescore(cid, sme, [answer.id]),
    ]
    raised: list[object] = []
    for call in calls:
        try:
            call()
        except Exception as error:
            raised.append(error)
        else:
            raised.append(None)
    return raised


def booklet_scorer_with_units(w: World) -> BookletScorer:
    plain = booklet_scorer(w.mem)
    return BookletScorer(
        booklets=w.mem.booklets, scoring=plain._scoring, runtime=w.mem.runtime, units=w.mem.units
    )


def test_every_write_is_refused_after_submission(w: World) -> None:
    w.approved()
    busy = w.in_review()
    w.wf.review.close(w.cid, w.sme, busy.id)
    w.submit(_absent(busy))
    busy = w.mem.booklets.get(w.cid, busy.id)
    before = (busy, w.mem.booklets.answers(w.cid, busy.id), len(w.mem.audit.events))
    raised = _every_write(w, busy)
    assert all(isinstance(e, UnitClosedError) and str(e) == SUBMITTED for e in raised), raised
    after = (
        w.mem.booklets.get(w.cid, busy.id),
        w.mem.booklets.answers(w.cid, busy.id),
        len(w.mem.audit.events),
    )
    assert after == before


def test_admin_writes_are_refused_after_submission(w: World) -> None:
    w.approved()
    unit = w.unit()
    w.submit()
    with pytest.raises(UnitClosedError):
        _unit_uploads(w).upload(
            w.cid, w.admin, unit.id, [NamedFile(name="X.pdf", data=fake_pdf(1))]
        )
    other = add_sme(w)
    with pytest.raises(UnitClosedError):
        w.svc.transfers.transfer(w.cid, w.admin, unit.id, owner_id=other.id, reason="Leave")
    with pytest.raises(UnitClosedError):
        w.svc.delegation.request(w.cid, w.sme, unit.id, reason="Too many")


def _unit_uploads(w: World) -> UnitUploadService:
    uploads = UploadService(
        booklets=w.svc.booklets,
        repository=w.mem.booklets,
        blobs=w.mem.blobs,
        jobs=w.mem.jobs,
        runtime=w.mem.runtime,
    )
    return UnitUploadService(
        uploads=uploads,
        booklet_service=w.svc.booklets,
        units=w.mem.units,
        booklets=w.mem.booklets,
        students=w.mem.students,
        users=w.mem.users,
        runtime=w.mem.runtime,
    )


def add_sme(w: World) -> User:
    sme = User(
        id=UserId(w.mem.ids.new()),
        college_id=w.cid,
        display_name="Second SME",
        email="second@example.test",
        role=Role.SME,
    )
    w.mem.users.save(w.cid, sme)
    w.svc.entitlements.grant(w.cid, w.admin, sme.id, w.paper.subject_id)
    return sme


# --- return and resubmit ---------------------------------------------


def test_the_admin_returns_with_a_note_and_the_sme_resubmits(w: World) -> None:
    w.approved()
    busy = w.in_review()
    w.submit(_absent(busy))
    unit = w.unit()
    with pytest.raises(PermissionDeniedError):
        w.svc.unit_flow.return_(w.cid, w.sme, unit.id, note=NOTE)
    with pytest.raises(InvariantError):
        w.svc.unit_flow.return_(w.cid, w.admin, unit.id, note="  ")
    returned = w.svc.unit_flow.return_(
        w.cid, w.admin, unit.id, note=NOTE, expected_version=unit.version
    )
    assert returned.state is UnitState.OPEN
    (submission,) = w.mem.units.submissions(w.cid, unit.id)
    assert submission.returned and submission.return_note == NOTE
    mail = w.mail.sent[-1]
    assert mail.to == w.college.teacher.email and NOTE in mail.text and unit.code in mail.subject
    event = next(e for e in w.mem.audit.events if e.action is AuditAction.UNIT_RETURNED)
    assert isinstance(event.after, dict) and event.after["note_given"] is True
    assert NOTE not in str(event.after)
    # Open again: the SME finishes the booklet and submits once more.
    for a in w.mem.booklets.answers(w.cid, busy.id):
        if a.status is not AnswerStatus.APPROVED:
            w.wf.review.open(w.cid, w.sme, busy.id)
            w.wf.review.approve_answer(w.cid, w.sme, busy.id, a.id, expected_version=a.version)
    current = w.mem.booklets.get(w.cid, busy.id)
    w.wf.review.approve_booklet(w.cid, w.sme, busy.id, expected_version=current.version)
    again = w.submit()
    assert again.state is UnitState.SUBMITTED
    assert len(w.mem.units.submissions(w.cid, unit.id)) == 2
    # A return needs a submitted unit.
    w.svc.unit_flow.return_(w.cid, w.admin, unit.id, note=NOTE)
    with pytest.raises(IllegalTransitionError):
        w.svc.unit_flow.return_(w.cid, w.admin, unit.id, note=NOTE)


# --- freeze ---------------------------------------------


def test_the_governor_freezes_and_the_record_holds_the_marks(w: World) -> None:
    done = w.approved()
    busy = w.in_review()
    w.wf.review.close(w.cid, w.sme, busy.id)
    unit = w.unit()
    with pytest.raises(IllegalTransitionError):  # not submitted yet
        w.svc.unit_flow.freeze(w.cid, w.governor.id, unit.id)
    w.submit(_absent(busy))
    for nobody in (w.admin, w.sme):
        with pytest.raises(PermissionDeniedError):
            w.svc.unit_flow.freeze(w.cid, nobody, unit.id)
    frozen = w.svc.unit_flow.freeze(w.cid, w.governor.id, unit.id)
    assert frozen.state is UnitState.FROZEN
    record = w.svc.unit_flow.frozen_record(w.cid, w.governor.id, unit.id)
    assert record.frozen_by == w.governor.id and record.unit_code == unit.code
    (paper,) = record.papers
    assert paper["paper_id"] == str(w.paper.id) and paper["title"] == w.paper.title
    assert isinstance(paper["document"], dict) and paper["document"]["sections"]
    assert isinstance(paper["questions"], list) and paper["questions"]
    by_id = {b.booklet_id: b for b in record.booklets}
    marked, unmarked = by_id[done.id], by_id[busy.id]
    sheets = w.mem.sheets.versions(w.cid, done.id)
    assert marked.outcome is FrozenOutcome.APPROVED and marked.total == sheets[-1].total
    assert [s.version for s in marked.sheets] == [1]
    assert marked.sheets[0].pdf_key == sheets[0].pdf.value  # type: ignore[union-attr]
    for answer in marked.answers:
        review = w.mem.scores.reviews(w.cid, answer.answer_id)[-1]
        assert answer.sme_mark == review.teacher_mark and answer.ai_mark == review.ai_mark
        assert answer.question_code
    assert unmarked.outcome is FrozenOutcome.UNMARKED and unmarked.reason is UnmarkedReason.ABSENT
    assert unmarked.total is None and unmarked.sheets == ()
    assert all(a.sme_mark is None and a.ai_mark is not None for a in unmarked.answers)
    event = next(e for e in w.mem.audit.events if e.action is AuditAction.UNIT_FROZEN)
    assert isinstance(event.after, dict) and event.after["booklets"] == 2


def test_everything_is_refused_after_the_freeze(w: World) -> None:
    done = w.approved()
    w.wf.review.close(w.cid, w.sme, done.id)
    unit = w.unit()
    w.submit()
    w.svc.unit_flow.freeze(w.cid, w.governor.id, unit.id)
    with pytest.raises(IllegalTransitionError):
        w.svc.unit_flow.return_(w.cid, w.admin, unit.id, note=NOTE)
    with pytest.raises(IllegalTransitionError):
        w.svc.unit_flow.freeze(w.cid, w.governor.id, unit.id)
    with pytest.raises(UnitClosedError):
        w.submit()
    raised = _every_write(w, w.mem.booklets.get(w.cid, done.id))
    assert all(isinstance(e, UnitClosedError) and str(e) == FROZEN for e in raised), raised
    # The frozen record is written once.
    record = w.mem.units.frozen(w.cid, unit.id)
    assert record is not None
    with pytest.raises(Exception, match="frozen already"):
        w.mem.units.save_frozen(w.cid, replace(record, frozen_at=record.frozen_at))


def test_the_governor_sees_only_submitted_and_frozen_units(w: World) -> None:
    w.approved()
    unit = w.unit()
    assert w.svc.units.list(w.cid, w.governor.id) == []
    with pytest.raises(NotFoundError):
        w.svc.units.get(w.cid, w.governor.id, unit.id)
    w.submit()
    assert [u.id for u in w.svc.units.list(w.cid, w.governor.id)] == [unit.id]
    with pytest.raises(NotFoundError):  # nothing frozen yet
        w.svc.unit_flow.frozen_record(w.cid, w.governor.id, unit.id)


def test_a_marked_booklet_keeps_its_total(w: World) -> None:
    done = w.approved()
    sheet = w.mem.sheets.versions(w.cid, done.id)[-1]
    assert sheet.total >= Decimal(0)
    w.submit()
    w.svc.unit_flow.freeze(w.cid, w.governor.id, w.unit().id)
    record = w.mem.units.frozen(w.cid, w.unit().id)
    assert record is not None and record.booklets[0].total == sheet.total
    assert w.mem.booklets.get(w.cid, done.id).status is BookletStatus.APPROVED
