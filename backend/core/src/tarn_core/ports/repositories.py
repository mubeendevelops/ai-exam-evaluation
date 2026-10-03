"""Repository ports, one per aggregate.

College repositories take ``college_id`` as the first argument of every method and never
return another college's rows; writes of an item from another college raise
``TenantViolationError`` (PostgreSQL RLS enforces the same in P3). Missing rows raise
``NotFoundError``.

The content repository is global: reads need no college, and every item records its
owning college. Edit rights are checked by the content service, not here."""

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Answer, Booklet, Page, Region, Segment
from tarn_core.domain.content import (
    Glossary,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
    Subject,
)
from tarn_core.domain.diagram import StudentDiagram
from tarn_core.domain.review import ResultSheet, Review
from tarn_core.domain.scoring import AnswerScore
from tarn_core.domain.tenancy import College, Student, User
from tarn_core.ids import (
    AnswerId,
    BookletId,
    CollegeId,
    PageId,
    QuestionId,
    StudentId,
    UserId,
)

type GlobalItem = (
    Subject
    | Question
    | ReferenceAnswer
    | RubricCriterion
    | Glossary
    | ReferenceDiagram
    | ExamBlueprint
)
type QuestionPart = ReferenceAnswer | RubricCriterion | Glossary | ReferenceDiagram


class ContentRepository(Protocol):
    """Versioned global content. Saving never overwrites: (id, version) is written once."""

    def get[T: GlobalItem](self, kind: type[T], item_id: UUID, version: int | None = None) -> T:
        """The given version, or the latest when ``version`` is None."""
        ...

    def versions[T: GlobalItem](self, kind: type[T], item_id: UUID) -> Sequence[T]:
        """Every version, oldest first."""
        ...

    def latest[T: GlobalItem](self, kind: type[T]) -> Sequence[T]:
        """The newest version of every item of ``kind``, in no particular order."""
        ...

    def for_question[T: QuestionPart](self, kind: type[T], question_id: QuestionId) -> Sequence[T]:
        """Latest version of each item of ``kind`` that belongs to the question."""
        ...

    def save(self, item: GlobalItem) -> None:
        """Raises InvariantError if this (id, version) exists or skips a version."""
        ...


class CollegeRepository(Protocol):
    def get(self, college_id: CollegeId) -> College: ...

    def save(self, college: College) -> None: ...


class UserRepository(Protocol):
    def get(self, college_id: CollegeId, user_id: UserId) -> User: ...

    def list(self, college_id: CollegeId) -> Sequence[User]: ...

    def save(self, college_id: CollegeId, user: User) -> None: ...


class StudentRepository(Protocol):
    """The roster."""

    def get(self, college_id: CollegeId, student_id: StudentId) -> Student: ...

    def list(self, college_id: CollegeId) -> Sequence[Student]: ...

    def find_by_usn(self, college_id: CollegeId, usn: str) -> Student | None: ...

    def search(self, college_id: CollegeId, query: str, limit: int) -> Sequence[Student]:
        """Students whose USN starts with, or whose name contains, ``query`` (any case);
        ordered by name, then USN. An empty query lists the first ``limit``."""
        ...

    def save(self, college_id: CollegeId, student: Student) -> None: ...


class BookletRepository(Protocol):
    """A booklet and what was extracted from it: pages, regions, segments, answers, diagrams."""

    def get(self, college_id: CollegeId, booklet_id: BookletId) -> Booklet: ...

    def list(self, college_id: CollegeId) -> Sequence[Booklet]: ...

    def find_by_hash(self, college_id: CollegeId, file_sha256: str) -> Sequence[Booklet]: ...

    def save(self, college_id: CollegeId, booklet: Booklet) -> None: ...

    def delete(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """Removes the booklet and everything extracted from it (D14)."""
        ...

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Page]: ...

    def save_page(self, college_id: CollegeId, page: Page) -> None: ...

    def regions(self, college_id: CollegeId, page_id: PageId) -> Sequence[Region]: ...

    def save_region(self, college_id: CollegeId, region: Region) -> None: ...

    def segments(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Segment]: ...

    def save_segment(self, college_id: CollegeId, segment: Segment) -> None: ...

    def answers(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Answer]: ...

    def get_answer(self, college_id: CollegeId, answer_id: AnswerId) -> Answer: ...

    def save_answer(self, college_id: CollegeId, answer: Answer) -> None: ...

    def diagrams(
        self, college_id: CollegeId, booklet_id: BookletId
    ) -> Sequence[StudentDiagram]: ...

    def save_diagram(self, college_id: CollegeId, diagram: StudentDiagram) -> None: ...


class ScoreRepository(Protocol):
    """AI suggestions and teacher reviews; both kept in full history."""

    def save_score(self, college_id: CollegeId, score: AnswerScore) -> None: ...

    def scores(self, college_id: CollegeId, answer_id: AnswerId) -> Sequence[AnswerScore]:
        """Oldest first."""
        ...

    def save_review(self, college_id: CollegeId, review: Review) -> None: ...

    def reviews(self, college_id: CollegeId, answer_id: AnswerId) -> Sequence[Review]:
        """Oldest first."""
        ...

    def delete_for_answers(self, college_id: CollegeId, answer_ids: Sequence[AnswerId]) -> None:
        """Booklet deletion only (D14)."""
        ...


class ResultSheetRepository(Protocol):
    """All versions are kept; earlier versions stay valid (rule 7)."""

    def save(self, college_id: CollegeId, sheet: ResultSheet) -> None:
        """Raises InvariantError unless ``sheet.version`` is the next version for its booklet."""
        ...

    def versions(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[ResultSheet]: ...

    def delete_for_booklet(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """Booklet deletion only (D14)."""
        ...
