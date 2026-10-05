"""Repository ports, one per aggregate.

College repositories take ``college_id`` as the first argument of every method and never
return another college's rows; writes of an item from another college raise
``TenantViolationError`` (PostgreSQL RLS enforces the same in P3). Missing rows raise
``NotFoundError``.

The content repository is global: reads need no college, and every item records its
owning college. Edit rights are checked by the content service, not here."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Answer, Booklet, Page, Region, Segment
from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import (
    Difficulty,
    Glossary,
    KeyFile,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
    Subject,
)
from tarn_core.domain.diagram import StudentDiagram
from tarn_core.domain.review import ResultSheet, Review
from tarn_core.domain.scoring import AnswerScore, SentenceVector
from tarn_core.domain.tenancy import College, Student, User
from tarn_core.ids import (
    AnswerId,
    BookletId,
    CollegeId,
    PageId,
    QuestionId,
    SegmentId,
    StudentId,
    SubjectId,
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
    | KeyFile
)
type QuestionPart = ReferenceAnswer | RubricCriterion | Glossary | ReferenceDiagram | KeyFile


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionQuery:
    """Filters of the question bank; every filter given must match (case-insensitive).
    ``keyword`` looks in the question text, code, topic and the live reference answers;
    ``code`` is a part of the code (the whole code with ``exact_code``); ``topic`` is the
    topic exactly."""

    keyword: str = ""
    code: str = ""
    exact_code: bool = False
    topic: str = ""
    subject_id: SubjectId | None = None
    difficulty: Difficulty | None = None
    owner_id: CollegeId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionPage:
    items: tuple[Question, ...]
    total: int
    """All matches, not just this page."""


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
        """Latest version of each item of ``kind`` that belongs to the question, leaving out
        items whose latest version is retired."""
        ...

    def search_questions(
        self, query: QuestionQuery, *, limit: int, offset: int = 0
    ) -> QuestionPage:
        """Latest version of each matching question, ordered by code (then id)."""
        ...

    def topics(self, subject_id: SubjectId | None = None) -> Sequence[str]:
        """Distinct non-empty topics of the latest question versions, sorted."""
        ...

    def key_counts(self, question_ids: Sequence[QuestionId]) -> Mapping[QuestionId, int]:
        """Live reference answers plus key files, per question (0 when none)."""
        ...

    def save(self, item: GlobalItem) -> None:
        """Raises InvariantError if this (id, version) exists or skips a version."""
        ...


class CollegeRepository(Protocol):
    def get(self, college_id: CollegeId) -> College: ...

    def names(self, college_ids: Sequence[CollegeId]) -> Mapping[CollegeId, str]:
        """Names of colleges, any college's: the owner of global content is public (R7).
        Unknown ids are left out."""
        ...

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

    def count_waiting(self, college_id: CollegeId, user_id: UserId) -> int:
        """Booklets this user uploaded that are still queued or being processed."""
        ...

    def save(self, college_id: CollegeId, booklet: Booklet) -> None: ...

    def delete(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """Removes the booklet and everything extracted from it (D14)."""
        ...

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Page]: ...

    def save_page(self, college_id: CollegeId, page: Page) -> None: ...

    def regions(self, college_id: CollegeId, page_id: PageId) -> Sequence[Region]: ...

    def save_region(self, college_id: CollegeId, region: Region) -> None: ...

    def replace_regions(
        self, college_id: CollegeId, page_id: PageId, regions: Sequence[Region]
    ) -> None:
        """Removes the page's regions (with their readings) and stores ``regions`` in this
        order (parents before the cells that point at them)."""
        ...

    def segments(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Segment]:
        """In written order (``position``)."""
        ...

    def save_segment(self, college_id: CollegeId, segment: Segment) -> None: ...

    def delete_segment(self, college_id: CollegeId, segment_id: SegmentId) -> None:
        """Removes one segment (a re-segmentation or a teacher's merge); its diagrams go with
        it. No error when it is already gone."""
        ...

    def answers(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Answer]: ...

    def get_answer(self, college_id: CollegeId, answer_id: AnswerId) -> Answer: ...

    def save_answer(self, college_id: CollegeId, answer: Answer) -> None: ...

    def delete_answer(self, college_id: CollegeId, answer_id: AnswerId) -> None:
        """Removes an answer that lost all its segments; its scores and reviews must be
        removed first (``ScoreRepository.delete_for_answers``)."""
        ...

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

    def vectors(
        self, college_id: CollegeId, answer_id: AnswerId, embedder: EngineRef
    ) -> Sequence[SentenceVector]:
        """The answer's stored sentence vectors from this embedder version, by index."""
        ...

    def replace_vectors(
        self,
        college_id: CollegeId,
        answer_id: AnswerId,
        embedder: EngineRef,
        vectors: Sequence[SentenceVector],
    ) -> None:
        """Replace the answer's vectors from this embedder version (the text changed). A store
        whose vector column has another dimension keeps nothing (the vectors are a cache)."""
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
