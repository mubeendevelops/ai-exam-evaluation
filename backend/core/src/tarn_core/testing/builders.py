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
from tarn_core.domain.tenancy import College, Role, Student, User
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
    UserId,
)
from tarn_core.ports.engines import Scorer
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    ResultSheetRepository,
    ScoreRepository,
    StudentRepository,
    UserRepository,
)
from tarn_core.ports.runtime import IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.blueprints import BlueprintService
from tarn_core.services.booklets import BookletService
from tarn_core.services.content import ContentService
from tarn_core.services.question_bank import QuestionBankService
from tarn_core.services.scoring import ScoringService
from tarn_core.services.subjects import SubjectService
from tarn_core.services.totals import TotalsService
from tarn_core.testing import FixedCreditScorer


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
    def booklets(self) -> BookletRepository: ...
    @property
    def scores(self) -> ScoreRepository: ...
    @property
    def sheets(self) -> ResultSheetRepository: ...
    @property
    def blobs(self) -> BlobStore: ...
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
    """A college with one teacher, one admin and three students. Pass ``college_id`` when the
    backend must know the college before its row exists (a database session bound to it)."""
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
        role=Role.TEACHER,
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


def meta(owner: CollegeFixture, version: int = 1) -> ContentMeta:
    return ContentMeta(version=version, owning_college_id=owner.id, created_by=owner.teacher.id)


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
    content.save(question)
    return question


def add_rubric(mem: Backend, owner: CollegeFixture, question: Question) -> None:
    """Two criteria splitting the marks: a list criterion and a semantic one."""
    half = question.max_marks / 2
    mem.content.save(
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
        )
    )
    mem.content.save(
        RubricCriterion(
            id=CriterionId(mem.ids.new()),
            meta=meta(owner),
            question_id=question.id,
            label="Explains the idea",
            type=CriterionType.SEMANTIC,
            weight=question.max_marks - half,
            params=SemanticParams(reference_statement="The idea follows from alpha and beta."),
        )
    )


def ci_shaped_blueprint(mem: Backend, owner: CollegeFixture) -> ExamBlueprint:
    """50 marks: A any 5 of 7 x 2, B any 4 of 7 x 5, C any 2 of 3 x 10. Questions numbered 1-17."""
    subject = Subject(
        id=SubjectId(mem.ids.new()), meta=meta(owner), code="SYN101", name="Synthetic Civics"
    )
    mem.content.save(subject)
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
    mem.content.save(blueprint)
    return blueprint


def ipr_shaped_blueprint(mem: Backend, owner: CollegeFixture) -> ExamBlueprint:
    """60 marks: A any 5 of 7 x 3, B any 3 of 4 x 10, C Q12 (a 10 + b 5) OR Q13 (a 10 + b 5)."""
    subject = Subject(
        id=SubjectId(mem.ids.new()), meta=meta(owner), code="SYN201", name="Synthetic IP Law"
    )
    mem.content.save(subject)

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
    mem.content.save(blueprint)
    return blueprint


@dataclass(frozen=True, slots=True)
class Services:
    bank: QuestionBankService
    blueprints: BlueprintService
    booklets: BookletService
    content: ContentService
    scoring: ScoringService
    subjects: SubjectService
    totals: TotalsService


def make_services(mem: Backend, scorers: Sequence[Scorer] | None = None) -> Services:
    """Every core service wired to the in-memory adapters."""
    rt = mem.runtime
    return Services(
        bank=QuestionBankService(
            content=mem.content,
            users=mem.users,
            colleges=mem.colleges,
            blobs=mem.blobs,
            runtime=rt,
        ),
        blueprints=BlueprintService(content=mem.content, users=mem.users, runtime=rt),
        subjects=SubjectService(content=mem.content, users=mem.users, runtime=rt),
        booklets=BookletService(
            booklets=mem.booklets,
            students=mem.students,
            users=mem.users,
            content=mem.content,
            scores=mem.scores,
            sheets=mem.sheets,
            blobs=mem.blobs,
            runtime=rt,
        ),
        content=ContentService(content=mem.content, users=mem.users, runtime=rt),
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
