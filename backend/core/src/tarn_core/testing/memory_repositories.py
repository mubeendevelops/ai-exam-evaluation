"""In-memory repositories for tests. They filter by college the way PostgreSQL RLS will,
and log every college id they are called with, so tests can prove isolation."""

from collections.abc import Callable, Sequence
from typing import Protocol
from uuid import UUID

from tarn_core.domain.booklet import Answer, Booklet, Page, Region, Segment
from tarn_core.domain.diagram import StudentDiagram
from tarn_core.domain.review import ResultSheet, Review
from tarn_core.domain.scoring import AnswerScore
from tarn_core.domain.tenancy import College, Student, User
from tarn_core.errors import InvariantError, NotFoundError, TenantViolationError
from tarn_core.ids import (
    AnswerId,
    BookletId,
    CollegeId,
    PageId,
    QuestionId,
    StudentId,
    UserId,
)
from tarn_core.ports.repositories import GlobalItem, QuestionPart


class TenantLog:
    """Every college id passed to a college repository, in call order."""

    def __init__(self) -> None:
        self.calls: list[CollegeId] = []

    def touch(self, college_id: CollegeId) -> None:
        self.calls.append(college_id)

    @property
    def colleges(self) -> set[CollegeId]:
        return set(self.calls)


class _CollegeRow(Protocol):
    @property
    def college_id(self) -> CollegeId: ...


class _Table[K, V: _CollegeRow]:
    """Rows partitioned by college. A lookup in college A cannot reach college B's rows."""

    def __init__(self, log: TenantLog, name: str) -> None:
        self._rows: dict[CollegeId, dict[K, V]] = {}
        self._log = log
        self._name = name

    def rows(self, college_id: CollegeId) -> dict[K, V]:
        self._log.touch(college_id)
        return self._rows.setdefault(college_id, {})

    def get(self, college_id: CollegeId, key: K) -> V:
        try:
            return self.rows(college_id)[key]
        except KeyError:
            raise NotFoundError(f"{self._name} {key}") from None

    def put(self, college_id: CollegeId, key: K, row: V) -> None:
        if row.college_id != college_id:
            raise TenantViolationError(
                f"{self._name} of another college written through {college_id}"
            )
        self.rows(college_id)[key] = row

    def values(self, college_id: CollegeId) -> list[V]:
        return list(self.rows(college_id).values())

    def drop_where(self, college_id: CollegeId, doomed: Callable[[V], bool]) -> None:
        rows = self.rows(college_id)
        for key in [k for k, v in rows.items() if doomed(v)]:
            del rows[key]


class MemoryContentRepository:
    def __init__(self) -> None:
        # keyed by (type name, id); each list holds versions 1..n in order
        self._items: dict[tuple[str, UUID], list[GlobalItem]] = {}

    def get[T: GlobalItem](self, kind: type[T], item_id: UUID, version: int | None = None) -> T:
        history = self._items.get((kind.__name__, item_id), [])
        if not history or (version is not None and not 1 <= version <= len(history)):
            raise NotFoundError(f"{kind.__name__} {item_id} v{version}")
        item = history[-1] if version is None else history[version - 1]
        if not isinstance(item, kind):  # cannot happen: stored under its own type
            raise NotFoundError(f"{kind.__name__} {item_id}")
        return item

    def versions[T: GlobalItem](self, kind: type[T], item_id: UUID) -> Sequence[T]:
        return [i for i in self._items.get((kind.__name__, item_id), []) if isinstance(i, kind)]

    def for_question[T: QuestionPart](self, kind: type[T], question_id: QuestionId) -> Sequence[T]:
        found: list[T] = []
        for (kind_name, _), history in self._items.items():
            latest = history[-1]
            if (
                kind_name == kind.__name__
                and isinstance(latest, kind)
                and latest.question_id == question_id
            ):
                found.append(latest)
        return found

    def save(self, item: GlobalItem) -> None:
        history = self._items.setdefault((type(item).__name__, item.id), [])
        if item.meta.version != len(history) + 1:
            raise InvariantError(
                f"{type(item).__name__} {item.id}: expected version {len(history) + 1}, "
                f"got {item.meta.version}"
            )
        history.append(item)


class MemoryCollegeRepository:
    def __init__(self, log: TenantLog) -> None:
        self._colleges: dict[CollegeId, College] = {}
        self._log = log

    def get(self, college_id: CollegeId) -> College:
        self._log.touch(college_id)
        try:
            return self._colleges[college_id]
        except KeyError:
            raise NotFoundError(f"college {college_id}") from None

    def save(self, college: College) -> None:
        self._log.touch(college.id)
        self._colleges[college.id] = college


class MemoryUserRepository:
    def __init__(self, log: TenantLog) -> None:
        self._t: _Table[UserId, User] = _Table(log, "user")

    def get(self, college_id: CollegeId, user_id: UserId) -> User:
        return self._t.get(college_id, user_id)

    def list(self, college_id: CollegeId) -> Sequence[User]:
        return self._t.values(college_id)

    def save(self, college_id: CollegeId, user: User) -> None:
        self._t.put(college_id, user.id, user)


class MemoryStudentRepository:
    def __init__(self, log: TenantLog) -> None:
        self._t: _Table[StudentId, Student] = _Table(log, "student")

    def get(self, college_id: CollegeId, student_id: StudentId) -> Student:
        return self._t.get(college_id, student_id)

    def list(self, college_id: CollegeId) -> Sequence[Student]:
        return self._t.values(college_id)

    def find_by_usn(self, college_id: CollegeId, usn: str) -> Student | None:
        return next((s for s in self._t.values(college_id) if s.usn == usn), None)

    def save(self, college_id: CollegeId, student: Student) -> None:
        self._t.put(college_id, student.id, student)


class MemoryBookletRepository:
    def __init__(self, log: TenantLog) -> None:
        self._booklets: _Table[BookletId, Booklet] = _Table(log, "booklet")
        self._pages: _Table[PageId, Page] = _Table(log, "page")
        self._regions: _Table[UUID, Region] = _Table(log, "region")
        self._segments: _Table[UUID, Segment] = _Table(log, "segment")
        self._answers: _Table[AnswerId, Answer] = _Table(log, "answer")
        self._diagrams: _Table[UUID, StudentDiagram] = _Table(log, "diagram")

    def get(self, college_id: CollegeId, booklet_id: BookletId) -> Booklet:
        return self._booklets.get(college_id, booklet_id)

    def list(self, college_id: CollegeId) -> Sequence[Booklet]:
        return self._booklets.values(college_id)

    def find_by_hash(self, college_id: CollegeId, file_sha256: str) -> Sequence[Booklet]:
        return [b for b in self._booklets.values(college_id) if b.file_sha256 == file_sha256]

    def save(self, college_id: CollegeId, booklet: Booklet) -> None:
        self._booklets.put(college_id, booklet.id, booklet)

    def delete(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        self._booklets.get(college_id, booklet_id)
        page_ids = {p.id for p in self.pages(college_id, booklet_id)}
        del self._booklets.rows(college_id)[booklet_id]
        self._regions.drop_where(college_id, lambda r: r.page_id in page_ids)
        self._pages.drop_where(college_id, lambda p: p.booklet_id == booklet_id)
        self._segments.drop_where(college_id, lambda s: s.booklet_id == booklet_id)
        self._answers.drop_where(college_id, lambda a: a.booklet_id == booklet_id)
        self._diagrams.drop_where(college_id, lambda d: d.booklet_id == booklet_id)

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Page]:
        found = [p for p in self._pages.values(college_id) if p.booklet_id == booklet_id]
        return sorted(found, key=lambda p: p.index)

    def save_page(self, college_id: CollegeId, page: Page) -> None:
        self._pages.put(college_id, page.id, page)

    def regions(self, college_id: CollegeId, page_id: PageId) -> Sequence[Region]:
        return [r for r in self._regions.values(college_id) if r.page_id == page_id]

    def save_region(self, college_id: CollegeId, region: Region) -> None:
        self._regions.put(college_id, region.id, region)

    def segments(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Segment]:
        return [s for s in self._segments.values(college_id) if s.booklet_id == booklet_id]

    def save_segment(self, college_id: CollegeId, segment: Segment) -> None:
        self._segments.put(college_id, segment.id, segment)

    def answers(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Answer]:
        return [a for a in self._answers.values(college_id) if a.booklet_id == booklet_id]

    def get_answer(self, college_id: CollegeId, answer_id: AnswerId) -> Answer:
        return self._answers.get(college_id, answer_id)

    def save_answer(self, college_id: CollegeId, answer: Answer) -> None:
        self._answers.put(college_id, answer.id, answer)

    def diagrams(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[StudentDiagram]:
        return [d for d in self._diagrams.values(college_id) if d.booklet_id == booklet_id]

    def save_diagram(self, college_id: CollegeId, diagram: StudentDiagram) -> None:
        self._diagrams.put(college_id, diagram.id, diagram)


class MemoryScoreRepository:
    def __init__(self, log: TenantLog) -> None:
        self._scores: _Table[UUID, AnswerScore] = _Table(log, "answer score")
        self._reviews: _Table[UUID, Review] = _Table(log, "review")

    def save_score(self, college_id: CollegeId, score: AnswerScore) -> None:
        self._scores.put(college_id, score.id, score)

    def scores(self, college_id: CollegeId, answer_id: AnswerId) -> Sequence[AnswerScore]:
        return [s for s in self._scores.values(college_id) if s.answer_id == answer_id]

    def save_review(self, college_id: CollegeId, review: Review) -> None:
        self._reviews.put(college_id, review.id, review)

    def reviews(self, college_id: CollegeId, answer_id: AnswerId) -> Sequence[Review]:
        return [r for r in self._reviews.values(college_id) if r.answer_id == answer_id]

    def delete_for_answers(self, college_id: CollegeId, answer_ids: Sequence[AnswerId]) -> None:
        doomed = set(answer_ids)
        self._scores.drop_where(college_id, lambda s: s.answer_id in doomed)
        self._reviews.drop_where(college_id, lambda r: r.answer_id in doomed)


class MemoryResultSheetRepository:
    def __init__(self, log: TenantLog) -> None:
        self._t: _Table[tuple[BookletId, int], ResultSheet] = _Table(log, "result sheet")

    def save(self, college_id: CollegeId, sheet: ResultSheet) -> None:
        expected = len(self.versions(college_id, sheet.booklet_id)) + 1
        if sheet.version != expected:
            raise InvariantError(f"next result sheet version is {expected}, got {sheet.version}")
        self._t.put(college_id, (sheet.booklet_id, sheet.version), sheet)

    def versions(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[ResultSheet]:
        found = [s for s in self._t.values(college_id) if s.booklet_id == booklet_id]
        return sorted(found, key=lambda s: s.version)

    def delete_for_booklet(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        self._t.drop_where(college_id, lambda s: s.booklet_id == booklet_id)
