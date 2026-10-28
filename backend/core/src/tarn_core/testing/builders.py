"""Synthetic test data. Names and USNs are invented; nothing here comes from samples/.

The two paper shapes mirror the structure (not the content) of the sample papers:
QP-CI (50 marks: any 5 of 7 x 2, any 4 of 7 x 5, any 2 of 3 x 10) and
QP-IPR (60 marks: any 5 of 7 x 3, any 3 of 4 x 10, Q12 (a 10 + b 5) OR Q13 (a 10 + b 5))."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from tarn_core.domain.blueprint import AnyN, ExamBlueprint, OrGroup, QuestionSlot, Section, SubPart
from tarn_core.domain.booklet import (
    Answer,
    Booklet,
    Page,
    Segment,
    SegmentSource,
    SegmentSpan,
)
from tarn_core.domain.common import Box, college_blob_key
from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    ListItem,
    ListParams,
    Question,
    RubricCriterion,
    SemanticParams,
    Subject,
)
from tarn_core.domain.tenancy import College, Role, Student, SubjectGrant, User
from tarn_core.domain.unit import Unit, unit_code
from tarn_core.ids import (
    AnswerId,
    BlueprintId,
    CollegeId,
    CriterionId,
    PageId,
    QuestionId,
    SegmentId,
    StudentId,
    SubjectId,
    UnitId,
    UserId,
)
from tarn_core.ports.engines import Scorer
from tarn_core.ports.identity import Mailer
from tarn_core.ports.jobs import JobQueue
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    EntitlementRepository,
    ResultSheetRepository,
    ScoreRepository,
    StudentRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.ports.runtime import IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.blueprints import BlueprintService
from tarn_core.services.booklets import BookletService
from tarn_core.services.content import ContentService
from tarn_core.services.delegation import DelegationService
from tarn_core.services.entitlements import EntitlementService
from tarn_core.services.question_bank import QuestionBankService
from tarn_core.services.scoring import ScoringService
from tarn_core.services.subjects import SubjectService
from tarn_core.services.totals import TotalsService
from tarn_core.services.transfers import TransferService
from tarn_core.services.units import UnitService
from tarn_core.testing import FixedCreditScorer, MemoryMailer


class Backend(Protocol):
    """What the builders need: ``InMemory``, or a database session from an adapter."""

    @property
    def ids(self) -> IdGenerator: ...
    @property
    def colleges(self) -> CollegeRepository: ...
    @property
    def users(self) -> UserRepository: ...
    @property
    def students(self) -> StudentRepository: ...
    @property
    def content(self) -> ContentRepository: ...
    @property
    def entitlements(self) -> EntitlementRepository: ...
    @property
    def units(self) -> UnitRepository: ...
    @property
    def booklets(self) -> BookletRepository: ...
    @property
    def scores(self) -> ScoreRepository: ...
    @property
    def sheets(self) -> ResultSheetRepository: ...
    @property
    def blobs(self) -> BlobStore: ...
    @property
    def jobs(self) -> JobQueue: ...
    @property
    def runtime(self) -> Runtime: ...


@dataclass(frozen=True, slots=True)
class CollegeFixture:
    college: College
    teacher: User
    admin: User
    students: tuple[Student, ...]

    @property
    def id(self) -> CollegeId:
        return self.college.id


def add_college(mem: Backend, code: str, college_id: CollegeId | None = None) -> CollegeFixture:
    """A college with one SME (``teacher``), one admin and three students. Pass ``college_id``
    when the backend must know the college before its row exists (a session bound to it)."""
    college = College(
        id=CollegeId(mem.ids.new()) if college_id is None else college_id,
        name=f"Test College {code}",
        code=code,
    )
    mem.colleges.save(college)
    teacher = User(
        id=UserId(mem.ids.new()),
        college_id=college.id,
        display_name=f"Teacher {code}",
        email=f"teacher.{code.lower()}@example.test",
        role=Role.SME,
    )
    admin = User(
        id=UserId(mem.ids.new()),
        college_id=college.id,
        display_name=f"Admin {code}",
        email=f"admin.{code.lower()}@example.test",
        role=Role.ADMIN,
    )
    for user in (teacher, admin):
        mem.users.save(college.id, user)
    students = tuple(
        Student(
            id=StudentId(mem.ids.new()),
            college_id=college.id,
            name=f"Student {code}{n}",
            usn=f"TST26{code}{n:04d}",
        )
        for n in range(1, 4)
    )
    for student in students:
        mem.students.save(college.id, student)
    return CollegeFixture(college=college, teacher=teacher, admin=admin, students=students)


def grant(mem: Backend, owner: CollegeFixture, subject_id: SubjectId) -> None:
    """The college's SME may work on the subject (D160)."""
    mem.entitlements.grant(
        owner.id,
        SubjectGrant(
            college_id=owner.id,
            user_id=owner.teacher.id,
            subject_id=subject_id,
            granted_by=owner.admin.id,
            granted_at=mem.runtime.clock.now(),
        ),
    )


def add_subject(mem: Backend, owner: CollegeFixture, code: str, name: str) -> Subject:
    """A subject of the college, granted to its SME."""
    subject = Subject(id=SubjectId(mem.ids.new()), meta=meta(owner), code=code, name=name)
    mem.content.save(owner.id, subject)
    grant(mem, owner, subject.id)
    return subject


def unit_for(
    mem: Backend,
    college_id: CollegeId,
    paper_id: BlueprintId,
    *,
    owner_id: UserId | None = None,
    year: str = "2024",
) -> UnitId:
    """The college's unit of ``paper_id`` and ``year``, made on first use (a builder: no
    service checks). Its owner is ``owner_id`` or the college's first SME."""
    found = mem.units.find(college_id, paper_id, year)
    if found is not None:
        return found.id
    paper = mem.content.get(college_id, ExamBlueprint, paper_id)
    if owner_id is None:
        owner_id = next(u.id for u in mem.users.list(college_id) if u.role is Role.SME)
    unit = Unit(
        id=UnitId(mem.ids.new()),
        college_id=college_id,
        code=unit_code(mem.units.count(college_id) + 1),
        paper_id=paper.id,
        subject_id=paper.subject_id,
        year=year,
        owner_id=owner_id,
        due_date=mem.runtime.clock.now().date(),
        created_by=None,
        created_at=mem.runtime.clock.now(),
    )
    mem.units.save(college_id, unit)
    return unit.id


def make_subject(svc: "Services", owner: CollegeFixture, *, code: str, name: str) -> Subject:
    """A subject made the way the app makes it: by the admin, then granted to the SME."""
    subject = svc.subjects.create(owner.id, owner.admin.id, code=code, name=name)
    svc.entitlements.grant(owner.id, owner.admin.id, owner.teacher.id, subject.id)
    return subject


def meta(owner: CollegeFixture, version: int = 1) -> ContentMeta:
    return ContentMeta(version=version, college_id=owner.id, created_by=owner.teacher.id)


def add_question(
    content: ContentRepository,
    owner: CollegeFixture,
    new_id: UUID,
    subject_id: SubjectId,
    marks: Decimal,
    text: str = "Explain the synthetic concept.",
) -> Question:
    question = Question(
        id=QuestionId(new_id),
        meta=meta(owner),
        subject_id=subject_id,
        code=f"SYN-{new_id.hex[:8]}",
        text=text,
        max_marks=marks,
    )
    content.save(owner.id, question)
    return question


def add_rubric(mem: Backend, owner: CollegeFixture, question: Question) -> None:
    """Two criteria splitting the marks: a list criterion and a semantic one."""
    half = question.max_marks / 2
    mem.content.save(
        owner.id,
        RubricCriterion(
            id=CriterionId(mem.ids.new()),
            meta=meta(owner),
            question_id=question.id,
            label="Names the key items",
            type=CriterionType.LIST,
            weight=half,
            params=ListParams(
                items=(ListItem(term="alpha"), ListItem(term="beta", synonyms=("b",))),
                required_count=2,
            ),
        ),
    )
    mem.content.save(
        owner.id,
        RubricCriterion(
            id=CriterionId(mem.ids.new()),
            meta=meta(owner),
            question_id=question.id,
            label="Explains the idea",
            type=CriterionType.SEMANTIC,
            weight=question.max_marks - half,
            params=SemanticParams(reference_statement="The idea follows from alpha and beta."),
        ),
    )


def ci_shaped_blueprint(mem: Backend, owner: CollegeFixture) -> ExamBlueprint:
    """50 marks: A any 5 of 7 x 2, B any 4 of 7 x 5, C any 2 of 3 x 10. Questions numbered 1-17."""
    subject = add_subject(mem, owner, "SYN101", "Synthetic Civics")
    sections = []
    number = 1
    for label, n, m, marks in (("A", 5, 7, "2"), ("B", 4, 7, "5"), ("C", 2, 3, "10")):
        slots = []
        for _ in range(m):
            q = add_question(mem.content, owner, mem.ids.new(), subject.id, Decimal(marks))
            add_rubric(mem, owner, q)
            slots.append(QuestionSlot(label=str(number), marks=Decimal(marks), question_id=q.id))
            number += 1
        sections.append(Section(label=label, rule=AnyN(n), items=tuple(slots)))
    blueprint = ExamBlueprint(
        id=BlueprintId(mem.ids.new()),
        meta=meta(owner),
        subject_id=subject.id,
        title="Synthetic paper, CI shape",
        total_marks=Decimal(50),
        sections=tuple(sections),
    )
    mem.content.save(owner.id, blueprint)
    return blueprint


def ipr_shaped_blueprint(mem: Backend, owner: CollegeFixture) -> ExamBlueprint:
    """60 marks: A any 5 of 7 x 3, B any 3 of 4 x 10, C Q12 (a 10 + b 5) OR Q13 (a 10 + b 5)."""
    subject = add_subject(mem, owner, "SYN201", "Synthetic IP Law")

    def plain(number: int, marks: str) -> QuestionSlot:
        q = add_question(mem.content, owner, mem.ids.new(), subject.id, Decimal(marks))
        add_rubric(mem, owner, q)
        return QuestionSlot(label=str(number), marks=Decimal(marks), question_id=q.id)

    def split(number: int) -> QuestionSlot:
        parts = []
        for part, marks in (("a", "10"), ("b", "5")):
            q = add_question(mem.content, owner, mem.ids.new(), subject.id, Decimal(marks))
            add_rubric(mem, owner, q)
            parts.append(SubPart(label=part, marks=Decimal(marks), question_id=q.id))
        return QuestionSlot(label=str(number), marks=Decimal(15), parts=tuple(parts))

    blueprint = ExamBlueprint(
        id=BlueprintId(mem.ids.new()),
        meta=meta(owner),
        subject_id=subject.id,
        title="Synthetic paper, IPR shape",
        total_marks=Decimal(60),
        sections=(
            Section(label="A", rule=AnyN(5), items=tuple(plain(n, "3") for n in range(1, 8))),
            Section(label="B", rule=AnyN(3), items=tuple(plain(n, "10") for n in range(8, 12))),
            Section(label="C", items=(OrGroup(alternatives=(split(12), split(13))),)),
        ),
    )
    mem.content.save(owner.id, blueprint)
    return blueprint


@dataclass(frozen=True, slots=True)
class Services:
    bank: QuestionBankService
    blueprints: BlueprintService
    booklets: BookletService
    content: ContentService
    entitlements: EntitlementService
    scoring: ScoringService
    subjects: SubjectService
    totals: TotalsService
    units: UnitService
    transfers: TransferService
    delegation: DelegationService


def make_services(
    mem: Backend, scorers: Sequence[Scorer] | None = None, mailer: Mailer | None = None
) -> Services:
    """Every core service wired to the in-memory adapters. ``mailer`` catches the units'
    emails (default: a fresh ``MemoryMailer``)."""
    rt = mem.runtime
    mailer = mailer or MemoryMailer()
    entitlements = EntitlementService(
        entitlements=mem.entitlements,
        users=mem.users,
        content=mem.content,
        units=mem.units,
        runtime=rt,
    )
    return Services(
        units=UnitService(
            units=mem.units,
            users=mem.users,
            entitlements=mem.entitlements,
            content=mem.content,
            booklets=mem.booklets,
            mailer=mailer,
            runtime=rt,
        ),
        transfers=TransferService(
            units=mem.units,
            users=mem.users,
            entitlements=mem.entitlements,
            entitlement_service=entitlements,
            content=mem.content,
            booklets=mem.booklets,
            mailer=mailer,
            runtime=rt,
        ),
        delegation=DelegationService(units=mem.units, users=mem.users, runtime=rt),
        bank=QuestionBankService(
            content=mem.content,
            users=mem.users,
            entitlements=mem.entitlements,
            blobs=mem.blobs,
            runtime=rt,
        ),
        blueprints=BlueprintService(
            content=mem.content,
            users=mem.users,
            entitlements=mem.entitlements,
            runtime=rt,
            units=mem.units,
        ),
        subjects=SubjectService(
            content=mem.content, users=mem.users, entitlements=mem.entitlements, runtime=rt
        ),
        entitlements=entitlements,
        booklets=BookletService(
            booklets=mem.booklets,
            students=mem.students,
            users=mem.users,
            content=mem.content,
            scores=mem.scores,
            sheets=mem.sheets,
            blobs=mem.blobs,
            runtime=rt,
            units=mem.units,
        ),
        content=ContentService(
            content=mem.content, users=mem.users, entitlements=mem.entitlements, runtime=rt
        ),
        scoring=ScoringService(
            booklets=mem.booklets,
            scores=mem.scores,
            content=mem.content,
            scorers=[FixedCreditScorer()] if scorers is None else scorers,
            runtime=rt,
        ),
        totals=TotalsService(booklets=mem.booklets, scores=mem.scores, content=mem.content),
    )


def add_answer(mem: Backend, booklet: Booklet, slot_label: str) -> Answer:
    """An answer with one segment on a fresh page of the booklet."""
    college_id = booklet.college_id
    page = Page(
        id=PageId(mem.ids.new()),
        college_id=college_id,
        booklet_id=booklet.id,
        index=len(mem.booklets.pages(college_id, booklet.id)),
        image=college_blob_key(college_id, "booklet", str(booklet.id), f"{slot_label}.png"),
        width=1240,
        height=1754,
    )
    mem.booklets.save_page(college_id, page)
    mem.blobs.put(page.image, b"synthetic page", "image/png")
    segment = Segment(
        id=SegmentId(mem.ids.new()),
        college_id=college_id,
        booklet_id=booklet.id,
        slot_label=slot_label,
        spans=(SegmentSpan(page_id=page.id, box=Box(x0=0, y0=0, x1=1240, y1=800)),),
        source=SegmentSource.RULE,
    )
    mem.booklets.save_segment(college_id, segment)
    answer = Answer(
        id=AnswerId(mem.ids.new()),
        college_id=college_id,
        booklet_id=booklet.id,
        slot_label=slot_label,
        segment_ids=(segment.id,),
    )
    mem.booklets.save_answer(college_id, answer)
    return answer
