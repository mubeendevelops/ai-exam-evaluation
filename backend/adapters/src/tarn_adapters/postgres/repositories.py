"""PostgreSQL implementations of the repository ports (``tarn_core.ports.repositories``).

Every repository works on the connection of one ``PostgresSession``: one transaction in which
``app.college_id`` is set, so row-level security scopes every statement to that college.
Queries also filter by the ``college_id`` argument, so asking for another college returns
nothing (``NotFoundError`` / empty list) and writing another college's row is refused
(``TenantViolationError``), exactly like the in-memory adapters.

Each write runs in a savepoint: a refused write raises a domain error and leaves the
session's transaction usable."""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy import Connection, Row, Table, delete, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.exc import DBAPIError

from tarn_adapters.postgres import codec
from tarn_adapters.postgres import metadata as m
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import (
    Answer,
    AnswerStatus,
    Booklet,
    BookletStatus,
    LineReading,
    Page,
    Region,
    RegionKind,
    Segment,
    SegmentSource,
)
from tarn_core.domain.common import (
    BlobKey,
    ContentKind,
    ContentRef,
    EngineRef,
)
from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    Glossary,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
    Subject,
)
from tarn_core.domain.diagram import StudentDiagram
from tarn_core.domain.review import ResultSheet, Review
from tarn_core.domain.scoring import AnswerScore, CriterionScore
from tarn_core.domain.tenancy import College, Role, Student, User
from tarn_core.errors import (
    DomainError,
    InvariantError,
    NotFoundError,
    NotOwnerError,
    TenantViolationError,
)
from tarn_core.ids import (
    AnswerId,
    AnswerScoreId,
    BlueprintId,
    BookletId,
    CollegeId,
    CriterionId,
    GlossaryId,
    PageId,
    QuestionId,
    ReferenceAnswerId,
    ReferenceDiagramId,
    RegionId,
    ResultSheetId,
    ReviewId,
    SegmentId,
    StudentDiagramId,
    StudentId,
    SubjectId,
    UserId,
)
from tarn_core.ports.repositories import GlobalItem, QuestionPart

# SQLSTATEs translated into domain errors.
INSUFFICIENT_PRIVILEGE = "42501"  # RLS refusal or missing grant
FOREIGN_KEY_VIOLATION = "23503"
INVARIANT_STATES = frozenset({"23505", "23514", "23502", "22P02"})  # unique, check, not null


def translate(exc: DBAPIError, what: str, refused: type[DomainError]) -> NoReturn:
    """Raise the domain error for a database error. Messages never repeat the database's
    text, which can contain row values (student data)."""
    state = getattr(exc.orig, "sqlstate", None)
    if state == INSUFFICIENT_PRIVILEGE:
        raise refused(f"{what}: refused by the database") from exc
    if state == FOREIGN_KEY_VIOLATION:
        raise NotFoundError(f"{what}: refers to a row that does not exist") from exc
    if state in INVARIANT_STATES:
        raise InvariantError(f"{what}: violates a database constraint ({state})") from exc
    raise exc


@contextmanager
def writing(
    conn: Connection, what: str, refused: type[DomainError] = TenantViolationError
) -> Iterator[None]:
    try:
        with conn.begin_nested():
            yield
    except DBAPIError as exc:
        translate(exc, what, refused)


def _check_tenant(college_id: CollegeId, row_college: CollegeId, what: str) -> None:
    if row_college != college_id:
        raise TenantViolationError(f"{what} of another college written through {college_id}")


def _upsert(conn: Connection, table: Table, values: Mapping[str, object]) -> None:
    """Insert, or update every column except the key and ``college_id``. Updating a row that
    belongs to another college fails row-level security."""
    stmt = insert(table).values(**values)
    updates = {k: stmt.excluded[k] for k in values if k not in {"id", "college_id"}}
    conn.execute(stmt.on_conflict_do_update(index_elements=["id"], set_=updates))


def _like_escape(value: str) -> str:
    """A LIKE pattern that matches ``value`` literally (escape character ``\\``)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _one(row: Row[Any] | None, what: str) -> Row[Any]:
    if row is None:
        raise NotFoundError(what)
    return row


# --- global content ---------------------------------------------------------------------------


def _meta_values(meta: ContentMeta, *, owner: bool = True) -> dict[str, object]:
    values: dict[str, object] = {
        "version": meta.version,
        "created_by": meta.created_by,
        "copied_from_kind": None if meta.copied_from is None else meta.copied_from.kind.value,
        "copied_from_id": None if meta.copied_from is None else meta.copied_from.id,
        "copied_from_version": None if meta.copied_from is None else meta.copied_from.version,
    }
    if owner:
        values["owning_college_id"] = meta.owning_college_id
    return values


def _meta(row: Row[Any]) -> ContentMeta:
    copied = None
    if row.copied_from_kind is not None:
        copied = ContentRef(
            kind=ContentKind(row.copied_from_kind),
            id=row.copied_from_id,
            version=row.copied_from_version,
        )
    return ContentMeta(
        version=row.version,
        owning_college_id=CollegeId(row.owning_college_id),
        created_by=UserId(row.created_by),
        copied_from=copied,
    )


@dataclass(frozen=True, slots=True)
class _Kind:
    """How one kind of versioned content (other than questions) maps onto its table."""

    table: Table
    encode: Callable[[Any], dict[str, object]]
    decode: Callable[[Row[Any]], GlobalItem]


def _subject_values(s: Subject) -> dict[str, object]:
    return {"id": s.id, "code": s.code, "name": s.name}


def _subject(r: Row[Any]) -> Subject:
    return Subject(id=SubjectId(r.id), meta=_meta(r), code=r.code, name=r.name)


def _answer_key_values(a: ReferenceAnswer) -> dict[str, object]:
    return {
        "id": a.id,
        "question_id": a.question_id,
        "text": a.text,
        "synthetic": a.synthetic,
        "guidance_only": a.guidance_only,
    }


def _answer_key(r: Row[Any]) -> ReferenceAnswer:
    return ReferenceAnswer(
        id=ReferenceAnswerId(r.id),
        meta=_meta(r),
        question_id=QuestionId(r.question_id),
        text=r.text,
        synthetic=r.synthetic,
        guidance_only=r.guidance_only,
    )


def _criterion_values(c: RubricCriterion) -> dict[str, object]:
    return {
        "id": c.id,
        "question_id": c.question_id,
        "label": c.label,
        "type": c.type.value,
        "weight": c.weight,
        "params": codec.params_to_json(c.params),
    }


def _criterion(r: Row[Any]) -> RubricCriterion:
    kind = CriterionType(r.type)
    return RubricCriterion(
        id=CriterionId(r.id),
        meta=_meta(r),
        question_id=QuestionId(r.question_id),
        label=r.label,
        type=kind,
        weight=r.weight,
        params=codec.params_from_json(kind, r.params),
    )


def _glossary_values(g: Glossary) -> dict[str, object]:
    return {
        "id": g.id,
        "question_id": g.question_id,
        "teacher_terms": list(g.teacher_terms),
        "reference_labels": list(g.reference_labels),
    }


def _glossary(r: Row[Any]) -> Glossary:
    return Glossary(
        id=GlossaryId(r.id),
        meta=_meta(r),
        question_id=QuestionId(r.question_id),
        teacher_terms=tuple(r.teacher_terms),
        reference_labels=tuple(r.reference_labels),
    )


def _diagram_values(d: ReferenceDiagram) -> dict[str, object]:
    return {
        "id": d.id,
        "question_id": d.question_id,
        "png_key": d.png.value,
        "graph": codec.graph_to_json(d.graph),
    }


def _diagram(r: Row[Any]) -> ReferenceDiagram:
    return ReferenceDiagram(
        id=ReferenceDiagramId(r.id),
        meta=_meta(r),
        question_id=QuestionId(r.question_id),
        png=BlobKey(r.png_key),
        graph=codec.graph_from_json(r.graph),
    )


def _blueprint_values(b: ExamBlueprint) -> dict[str, object]:
    return {
        "id": b.id,
        "subject_id": b.subject_id,
        "title": b.title,
        "course_code": b.course_code,
        "duration_minutes": b.duration_minutes,
        "total_marks": b.total_marks,
        "mark_step": b.mark_step,
        "sections": codec.sections_to_json(b.sections),
    }


def _blueprint(r: Row[Any]) -> ExamBlueprint:
    return ExamBlueprint(
        id=BlueprintId(r.id),
        meta=_meta(r),
        subject_id=SubjectId(r.subject_id),
        title=r.title,
        course_code=r.course_code,
        duration_minutes=r.duration_minutes,
        total_marks=r.total_marks,
        mark_step=r.mark_step,
        sections=codec.sections_from_json(r.sections),
    )


_KINDS: dict[type, _Kind] = {
    Subject: _Kind(m.subjects, _subject_values, _subject),
    ReferenceAnswer: _Kind(m.reference_answers, _answer_key_values, _answer_key),
    RubricCriterion: _Kind(m.rubric_criteria, _criterion_values, _criterion),
    Glossary: _Kind(m.glossaries, _glossary_values, _glossary),
    ReferenceDiagram: _Kind(m.reference_diagrams, _diagram_values, _diagram),
    ExamBlueprint: _Kind(m.exam_blueprints, _blueprint_values, _blueprint),
}

_QUESTION_FROM = m.question_versions.join(
    m.questions, m.questions.c.id == m.question_versions.c.question_id
)
_QUESTION_COLUMNS = (
    m.question_versions,
    m.questions.c.owning_college_id,
    m.questions.c.subject_id,
)


def _question(r: Row[Any]) -> Question:
    return Question(
        id=QuestionId(r.question_id),
        meta=_meta(r),
        subject_id=SubjectId(r.subject_id),
        text=r.text,
        max_marks=r.max_marks,
        category=r.category,
    )


class PgContentRepository:
    """Global, versioned content. Reads see every college's items; a save is accepted only
    for the college the session is bound to, and only if that college owns the item
    (row-level security). Refusals raise ``NotOwnerError``."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get[T: GlobalItem](self, kind: type[T], item_id: UUID, version: int | None = None) -> T:
        if kind is Question:
            stmt = (
                select(*_QUESTION_COLUMNS)
                .select_from(_QUESTION_FROM)
                .where(m.question_versions.c.question_id == item_id)
            )
            vcol = m.question_versions.c.version
            decode: Callable[[Row[Any]], GlobalItem] = _question
        else:
            spec = _KINDS[kind]
            stmt = select(spec.table).where(spec.table.c.id == item_id)
            vcol = spec.table.c.version
            decode = spec.decode
        stmt = stmt.where(vcol == version) if version is not None else stmt
        row = self._conn.execute(stmt.order_by(vcol.desc()).limit(1)).first()
        item = decode(_one(row, f"{kind.__name__} {item_id} v{version}"))
        if not isinstance(item, kind):  # cannot happen: decoded from its own table
            raise NotFoundError(f"{kind.__name__} {item_id}")
        return item

    def versions[T: GlobalItem](self, kind: type[T], item_id: UUID) -> Sequence[T]:
        if kind is Question:
            stmt = (
                select(*_QUESTION_COLUMNS)
                .select_from(_QUESTION_FROM)
                .where(m.question_versions.c.question_id == item_id)
                .order_by(m.question_versions.c.version)
            )
            decode: Callable[[Row[Any]], GlobalItem] = _question
        else:
            spec = _KINDS[kind]
            stmt = (
                select(spec.table).where(spec.table.c.id == item_id).order_by(spec.table.c.version)
            )
            decode = spec.decode
        items = [decode(r) for r in self._conn.execute(stmt)]
        return [i for i in items if isinstance(i, kind)]

    def latest[T: GlobalItem](self, kind: type[T]) -> Sequence[T]:
        if kind is Question:
            stmt = (
                select(*_QUESTION_COLUMNS)
                .select_from(_QUESTION_FROM)
                .ext(distinct_on(m.question_versions.c.question_id))
                .order_by(m.question_versions.c.question_id, m.question_versions.c.version.desc())
            )
            decode: Callable[[Row[Any]], GlobalItem] = _question
        else:
            spec = _KINDS[kind]
            stmt = (
                select(spec.table)
                .ext(distinct_on(spec.table.c.id))
                .order_by(spec.table.c.id, spec.table.c.version.desc())
            )
            decode = spec.decode
        items = [decode(r) for r in self._conn.execute(stmt)]
        return [i for i in items if isinstance(i, kind)]

    def for_question[T: QuestionPart](self, kind: type[T], question_id: QuestionId) -> Sequence[T]:
        spec = _KINDS[kind]
        rows = self._conn.execute(
            select(spec.table)
            .where(spec.table.c.question_id == question_id)
            .order_by(spec.table.c.seq)
        )
        latest: dict[UUID, Row[Any]] = {}
        for row in rows:  # oldest first: a later version replaces, first appearance keeps order
            latest[row.id] = row
        items = [spec.decode(r) for r in latest.values()]
        return [i for i in items if isinstance(i, kind)]

    def save(self, item: GlobalItem) -> None:
        what = f"{type(item).__name__} {item.id} v{item.meta.version}"
        with writing(self._conn, what, NotOwnerError):
            if isinstance(item, Question):
                if item.meta.version == 1:  # the identity row others reference by id (D30)
                    identity = _meta_values(item.meta)
                    del identity["version"]
                    self._conn.execute(
                        insert(m.questions).values(
                            id=item.id, subject_id=item.subject_id, **identity
                        )
                    )
                self._conn.execute(
                    insert(m.question_versions).values(
                        question_id=item.id,
                        text=item.text,
                        max_marks=item.max_marks,
                        category=item.category,
                        **_meta_values(item.meta, owner=False),
                    )
                )
                return
            spec = _KINDS[type(item)]
            self._conn.execute(
                insert(spec.table).values(**spec.encode(item), **_meta_values(item.meta))
            )


# --- tenants ----------------------------------------------------------------------------------


class PgCollegeRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, college_id: CollegeId) -> College:
        row = self._conn.execute(select(m.colleges).where(m.colleges.c.id == college_id)).first()
        r = _one(row, f"college {college_id}")
        return College(id=CollegeId(r.id), name=r.name, code=r.code)

    def save(self, college: College) -> None:
        with writing(self._conn, f"college {college.id}"):
            _upsert(
                self._conn,
                m.colleges,
                {"id": college.id, "name": college.name, "code": college.code},
            )


def _user(r: Row[Any]) -> User:
    return User(
        id=UserId(r.id),
        college_id=CollegeId(r.college_id),
        display_name=r.display_name,
        email=r.email,
        role=Role(r.role),
        active=r.active,
    )


class PgUserRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, college_id: CollegeId, user_id: UserId) -> User:
        t = m.users
        row = self._conn.execute(
            select(t).where(t.c.id == user_id, t.c.college_id == college_id)
        ).first()
        return _user(_one(row, f"user {user_id}"))

    def list(self, college_id: CollegeId) -> Sequence[User]:
        t = m.users
        rows = self._conn.execute(select(t).where(t.c.college_id == college_id).order_by(t.c.seq))
        return [_user(r) for r in rows]

    def save(self, college_id: CollegeId, user: User) -> None:
        _check_tenant(college_id, user.college_id, "user")
        with writing(self._conn, f"user {user.id}"):
            _upsert(
                self._conn,
                m.users,
                {
                    "id": user.id,
                    "college_id": user.college_id,
                    "display_name": user.display_name,
                    "email": user.email,
                    "role": user.role.value,
                    "active": user.active,
                },
            )


def _student(r: Row[Any]) -> Student:
    return Student(
        id=StudentId(r.id),
        college_id=CollegeId(r.college_id),
        name=r.name,
        usn=r.usn,
        class_section=r.class_section,
    )


class PgStudentRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def get(self, college_id: CollegeId, student_id: StudentId) -> Student:
        t = m.students
        row = self._conn.execute(
            select(t).where(t.c.id == student_id, t.c.college_id == college_id)
        ).first()
        return _student(_one(row, f"student {student_id}"))

    def list(self, college_id: CollegeId) -> Sequence[Student]:
        t = m.students
        rows = self._conn.execute(select(t).where(t.c.college_id == college_id).order_by(t.c.seq))
        return [_student(r) for r in rows]

    def find_by_usn(self, college_id: CollegeId, usn: str) -> Student | None:
        t = m.students
        row = self._conn.execute(
            select(t).where(t.c.college_id == college_id, t.c.usn == usn)
        ).first()
        return None if row is None else _student(row)

    def search(self, college_id: CollegeId, query: str, limit: int) -> Sequence[Student]:
        t = m.students
        q = query.strip().lower()
        stmt = select(t).where(t.c.college_id == college_id)
        if q:
            usn_prefix = _like_escape("".join(q.split()).upper()) + "%"
            name_part = "%" + _like_escape(q) + "%"
            stmt = stmt.where(
                or_(
                    t.c.usn.like(usn_prefix, escape="\\"),
                    func.lower(t.c.name).like(name_part, escape="\\"),
                )
            )
        rows = self._conn.execute(stmt.order_by(func.lower(t.c.name), t.c.usn).limit(limit))
        return [_student(r) for r in rows]

    def save(self, college_id: CollegeId, student: Student) -> None:
        _check_tenant(college_id, student.college_id, "student")
        with writing(self._conn, f"student {student.id}"):
            _upsert(
                self._conn,
                m.students,
                {
                    "id": student.id,
                    "college_id": student.college_id,
                    "name": student.name,
                    "usn": student.usn,
                    "class_section": student.class_section,
                },
            )


# --- booklets ---------------------------------------------------------------------------------


def _booklet(r: Row[Any]) -> Booklet:
    return Booklet(
        id=BookletId(r.id),
        college_id=CollegeId(r.college_id),
        student_id=StudentId(r.student_id),
        blueprint=ContentRef(
            kind=ContentKind.BLUEPRINT, id=r.blueprint_id, version=r.blueprint_version
        ),
        file_sha256=r.file_sha256,
        uploaded_by=UserId(r.uploaded_by),
        uploaded_at=r.uploaded_at,
        status=BookletStatus(r.status),
        version=r.version,
    )


def _page(r: Row[Any]) -> Page:
    return Page(
        id=PageId(r.id),
        college_id=CollegeId(r.college_id),
        booklet_id=BookletId(r.booklet_id),
        index=r.page_index,
        image=BlobKey(r.image_key),
        width=r.width,
        height=r.height,
    )


def _reading(r: Row[Any]) -> LineReading:
    return LineReading(
        engine=EngineRef(name=r.engine_name, version=r.engine_version),
        text=r.text,
        box=codec.box_from_list(r.box),
        confidence=r.confidence,
        char_confidences=None if r.char_confidences is None else tuple(r.char_confidences),
    )


def _segment(r: Row[Any]) -> Segment:
    return Segment(
        id=SegmentId(r.id),
        college_id=CollegeId(r.college_id),
        booklet_id=BookletId(r.booklet_id),
        slot_label=r.slot_label,
        spans=codec.spans_from_json(r.spans),
        source=SegmentSource(r.source),
        match_score=r.match_score,
    )


def _answer(r: Row[Any]) -> Answer:
    return Answer(
        id=AnswerId(r.id),
        college_id=CollegeId(r.college_id),
        booklet_id=BookletId(r.booklet_id),
        slot_label=r.slot_label,
        segment_ids=tuple(SegmentId(s) for s in r.segment_ids),
        status=AnswerStatus(r.status),
        version=r.version,
    )


def _student_diagram(r: Row[Any]) -> StudentDiagram:
    return StudentDiagram(
        id=StudentDiagramId(r.id),
        college_id=CollegeId(r.college_id),
        booklet_id=BookletId(r.booklet_id),
        segment_id=SegmentId(r.segment_id),
        box=codec.box_from_list(r.box),
        graph=codec.graph_from_json(r.graph),
        version=r.version,
    )


class PgBookletRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def _rows(self, table: Table, college_id: CollegeId, **where: object) -> Sequence[Row[Any]]:
        stmt = select(table).where(table.c.college_id == college_id)
        for column, value in where.items():
            stmt = stmt.where(table.c[column] == value)
        return list(self._conn.execute(stmt.order_by(table.c.seq)))

    def get(self, college_id: CollegeId, booklet_id: BookletId) -> Booklet:
        rows = self._rows(m.booklets, college_id, id=booklet_id)
        return _booklet(_one(rows[0] if rows else None, f"booklet {booklet_id}"))

    def list(self, college_id: CollegeId) -> Sequence[Booklet]:
        return [_booklet(r) for r in self._rows(m.booklets, college_id)]

    def find_by_hash(self, college_id: CollegeId, file_sha256: str) -> Sequence[Booklet]:
        rows = self._rows(m.booklets, college_id, file_sha256=file_sha256)
        return [_booklet(r) for r in rows]

    def save(self, college_id: CollegeId, booklet: Booklet) -> None:
        _check_tenant(college_id, booklet.college_id, "booklet")
        with writing(self._conn, f"booklet {booklet.id}"):
            _upsert(
                self._conn,
                m.booklets,
                {
                    "id": booklet.id,
                    "college_id": booklet.college_id,
                    "student_id": booklet.student_id,
                    "blueprint_id": booklet.blueprint.id,
                    "blueprint_version": booklet.blueprint.version,
                    "file_sha256": booklet.file_sha256,
                    "uploaded_by": booklet.uploaded_by,
                    "uploaded_at": booklet.uploaded_at,
                    "status": booklet.status.value,
                    "version": booklet.version,
                },
            )

    def delete(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """Pages, regions, readings, segments, answers, diagrams, embeddings, scores, reviews
        and result sheets go with the booklet (foreign keys cascade)."""
        self.get(college_id, booklet_id)
        t = m.booklets
        with writing(self._conn, f"booklet {booklet_id}"):
            self._conn.execute(delete(t).where(t.c.id == booklet_id, t.c.college_id == college_id))

    def pages(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Page]:
        pages = [_page(r) for r in self._rows(m.pages, college_id, booklet_id=booklet_id)]
        return sorted(pages, key=lambda p: p.index)

    def save_page(self, college_id: CollegeId, page: Page) -> None:
        _check_tenant(college_id, page.college_id, "page")
        with writing(self._conn, f"page {page.id}"):
            _upsert(
                self._conn,
                m.pages,
                {
                    "id": page.id,
                    "college_id": page.college_id,
                    "booklet_id": page.booklet_id,
                    "page_index": page.index,
                    "image_key": page.image.value,
                    "width": page.width,
                    "height": page.height,
                },
            )

    def regions(self, college_id: CollegeId, page_id: PageId) -> Sequence[Region]:
        rows = self._rows(m.regions, college_id, page_id=page_id)
        if not rows:
            return []
        lr = m.line_readings
        readings: dict[UUID, list[LineReading]] = {}
        for r in self._conn.execute(
            select(lr)
            .where(lr.c.college_id == college_id, lr.c.region_id.in_([r.id for r in rows]))
            .order_by(lr.c.region_id, lr.c.ordinal)
        ):
            readings.setdefault(r.region_id, []).append(_reading(r))
        return [
            Region(
                id=RegionId(r.id),
                college_id=CollegeId(r.college_id),
                page_id=PageId(r.page_id),
                kind=RegionKind(r.kind),
                box=codec.box_from_list(r.box),
                readings=tuple(readings.get(r.id, ())),
                chosen=r.chosen,
                teacher_text=r.teacher_text,
            )
            for r in rows
        ]

    def save_region(self, college_id: CollegeId, region: Region) -> None:
        """All N engine readings are kept, in order; re-saving replaces them."""
        _check_tenant(college_id, region.college_id, "region")
        lr = m.line_readings
        with writing(self._conn, f"region {region.id}"):
            _upsert(
                self._conn,
                m.regions,
                {
                    "id": region.id,
                    "college_id": region.college_id,
                    "page_id": region.page_id,
                    "kind": region.kind.value,
                    "box": codec.box_to_list(region.box),
                    "chosen": region.chosen,
                    "teacher_text": region.teacher_text,
                },
            )
            self._conn.execute(
                delete(lr).where(lr.c.region_id == region.id, lr.c.college_id == college_id)
            )
            if region.readings:
                self._conn.execute(
                    insert(lr),
                    [
                        {
                            "college_id": region.college_id,
                            "region_id": region.id,
                            "ordinal": n,
                            "engine_name": reading.engine.name,
                            "engine_version": reading.engine.version,
                            "text": reading.text,
                            "box": codec.box_to_list(reading.box),
                            "confidence": reading.confidence,
                            "char_confidences": None
                            if reading.char_confidences is None
                            else list(reading.char_confidences),
                        }
                        for n, reading in enumerate(region.readings)
                    ],
                )

    def segments(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Segment]:
        return [_segment(r) for r in self._rows(m.segments, college_id, booklet_id=booklet_id)]

    def save_segment(self, college_id: CollegeId, segment: Segment) -> None:
        _check_tenant(college_id, segment.college_id, "segment")
        with writing(self._conn, f"segment {segment.id}"):
            _upsert(
                self._conn,
                m.segments,
                {
                    "id": segment.id,
                    "college_id": segment.college_id,
                    "booklet_id": segment.booklet_id,
                    "slot_label": segment.slot_label,
                    "spans": codec.spans_to_json(segment.spans),
                    "source": segment.source.value,
                    "match_score": segment.match_score,
                },
            )

    def answers(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[Answer]:
        return [_answer(r) for r in self._rows(m.answers, college_id, booklet_id=booklet_id)]

    def get_answer(self, college_id: CollegeId, answer_id: AnswerId) -> Answer:
        rows = self._rows(m.answers, college_id, id=answer_id)
        return _answer(_one(rows[0] if rows else None, f"answer {answer_id}"))

    def save_answer(self, college_id: CollegeId, answer: Answer) -> None:
        _check_tenant(college_id, answer.college_id, "answer")
        with writing(self._conn, f"answer {answer.id}"):
            _upsert(
                self._conn,
                m.answers,
                {
                    "id": answer.id,
                    "college_id": answer.college_id,
                    "booklet_id": answer.booklet_id,
                    "slot_label": answer.slot_label,
                    "segment_ids": list(answer.segment_ids),
                    "status": answer.status.value,
                    "version": answer.version,
                },
            )

    def diagrams(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[StudentDiagram]:
        rows = self._rows(m.diagram_graphs, college_id, booklet_id=booklet_id)
        return [_student_diagram(r) for r in rows]

    def save_diagram(self, college_id: CollegeId, diagram: StudentDiagram) -> None:
        _check_tenant(college_id, diagram.college_id, "diagram")
        with writing(self._conn, f"diagram {diagram.id}"):
            _upsert(
                self._conn,
                m.diagram_graphs,
                {
                    "id": diagram.id,
                    "college_id": diagram.college_id,
                    "booklet_id": diagram.booklet_id,
                    "segment_id": diagram.segment_id,
                    "box": codec.box_to_list(diagram.box),
                    "graph": codec.graph_to_json(diagram.graph),
                    "version": diagram.version,
                },
            )


# --- scores, reviews, result sheets -----------------------------------------------------------


class PgScoreRepository:
    """Insert-only history: the app role cannot UPDATE scores or reviews."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def save_score(self, college_id: CollegeId, score: AnswerScore) -> None:
        _check_tenant(college_id, score.college_id, "answer score")
        with writing(self._conn, f"answer score {score.id}"):
            self._conn.execute(
                insert(m.answer_scores).values(
                    id=score.id,
                    college_id=score.college_id,
                    answer_id=score.answer_id,
                    question_id=score.question.id,
                    question_version=score.question.version,
                    mark_step=score.mark_step,
                    mark=score.mark,
                    content_versions=codec.refs_to_json(score.content_versions),
                    created_at=score.created_at,
                )
            )
            self._conn.execute(
                insert(m.criterion_scores),
                [
                    {
                        "college_id": score.college_id,
                        "answer_score_id": score.id,
                        "ordinal": n,
                        "criterion_id": c.criterion.id,
                        "criterion_version": c.criterion.version,
                        "weight": c.weight,
                        "credit": c.credit,
                        "scorer_name": c.scorer.name,
                        "scorer_version": c.scorer.version,
                        "evidence": c.evidence,
                        "flags": list(c.flags),
                    }
                    for n, c in enumerate(score.criterion_scores)
                ],
            )

    def scores(self, college_id: CollegeId, answer_id: AnswerId) -> Sequence[AnswerScore]:
        t, cs = m.answer_scores, m.criterion_scores
        rows = list(
            self._conn.execute(
                select(t)
                .where(t.c.college_id == college_id, t.c.answer_id == answer_id)
                .order_by(t.c.seq)
            )
        )
        if not rows:
            return []
        criteria: dict[UUID, list[CriterionScore]] = {}
        for c in self._conn.execute(
            select(cs)
            .where(cs.c.college_id == college_id, cs.c.answer_score_id.in_([r.id for r in rows]))
            .order_by(cs.c.answer_score_id, cs.c.ordinal)
        ):
            criteria.setdefault(c.answer_score_id, []).append(
                CriterionScore(
                    criterion=ContentRef(
                        kind=ContentKind.RUBRIC_CRITERION,
                        id=c.criterion_id,
                        version=c.criterion_version,
                    ),
                    weight=c.weight,
                    credit=c.credit,
                    scorer=EngineRef(name=c.scorer_name, version=c.scorer_version),
                    evidence=c.evidence,
                    flags=tuple(c.flags),
                )
            )
        return [
            AnswerScore(
                id=AnswerScoreId(r.id),
                college_id=CollegeId(r.college_id),
                answer_id=AnswerId(r.answer_id),
                question=ContentRef(
                    kind=ContentKind.QUESTION, id=r.question_id, version=r.question_version
                ),
                criterion_scores=tuple(criteria.get(r.id, ())),
                mark_step=r.mark_step,
                mark=r.mark,
                content_versions=codec.refs_from_json(r.content_versions),
                created_at=r.created_at,
            )
            for r in rows
        ]

    def save_review(self, college_id: CollegeId, review: Review) -> None:
        _check_tenant(college_id, review.college_id, "review")
        with writing(self._conn, f"review {review.id}"):
            self._conn.execute(
                insert(m.reviews).values(
                    id=review.id,
                    college_id=review.college_id,
                    answer_id=review.answer_id,
                    answer_score_id=review.answer_score_id,
                    ai_mark=review.ai_mark,
                    teacher_mark=review.teacher_mark,
                    reviewer=review.reviewer,
                    reviewed_at=review.reviewed_at,
                    tags=list(review.tags),
                    remarks=review.remarks,
                )
            )

    def reviews(self, college_id: CollegeId, answer_id: AnswerId) -> Sequence[Review]:
        t = m.reviews
        rows = self._conn.execute(
            select(t)
            .where(t.c.college_id == college_id, t.c.answer_id == answer_id)
            .order_by(t.c.seq)
        )
        return [
            Review(
                id=ReviewId(r.id),
                college_id=CollegeId(r.college_id),
                answer_id=AnswerId(r.answer_id),
                answer_score_id=None
                if r.answer_score_id is None
                else AnswerScoreId(r.answer_score_id),
                ai_mark=r.ai_mark,
                teacher_mark=r.teacher_mark,
                reviewer=UserId(r.reviewer),
                reviewed_at=r.reviewed_at,
                tags=tuple(r.tags),
                remarks=r.remarks,
            )
            for r in rows
        ]

    def delete_for_answers(self, college_id: CollegeId, answer_ids: Sequence[AnswerId]) -> None:
        if not answer_ids:
            return
        ids = list(answer_ids)
        with writing(self._conn, "scores of deleted answers"):
            for t in (m.reviews, m.answer_scores):  # criterion scores cascade
                self._conn.execute(
                    delete(t).where(t.c.college_id == college_id, t.c.answer_id.in_(ids))
                )


class PgResultSheetRepository:
    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def save(self, college_id: CollegeId, sheet: ResultSheet) -> None:
        _check_tenant(college_id, sheet.college_id, "result sheet")
        t = m.result_sheets
        count = self._conn.execute(
            select(func.count())
            .select_from(t)
            .where(t.c.college_id == college_id, t.c.booklet_id == sheet.booklet_id)
        ).scalar_one()
        if sheet.version != count + 1:
            raise InvariantError(f"next result sheet version is {count + 1}, got {sheet.version}")
        with writing(self._conn, f"result sheet {sheet.id}"):
            self._conn.execute(
                insert(t).values(
                    id=sheet.id,
                    college_id=sheet.college_id,
                    booklet_id=sheet.booklet_id,
                    version=sheet.version,
                    lines=codec.lines_to_json(sheet.lines),
                    total=sheet.total,
                    max_marks=sheet.max_marks,
                    issued_by=sheet.issued_by,
                    issued_at=sheet.issued_at,
                    pdf_key=None if sheet.pdf is None else sheet.pdf.value,
                )
            )

    def versions(self, college_id: CollegeId, booklet_id: BookletId) -> Sequence[ResultSheet]:
        t = m.result_sheets
        rows = self._conn.execute(
            select(t)
            .where(t.c.college_id == college_id, t.c.booklet_id == booklet_id)
            .order_by(t.c.version)
        )
        return [
            ResultSheet(
                id=ResultSheetId(r.id),
                college_id=CollegeId(r.college_id),
                booklet_id=BookletId(r.booklet_id),
                version=r.version,
                lines=codec.lines_from_json(r.lines),
                total=Decimal(r.total),
                max_marks=Decimal(r.max_marks),
                issued_by=UserId(r.issued_by),
                issued_at=r.issued_at,
                pdf=None if r.pdf_key is None else BlobKey(r.pdf_key),
            )
            for r in rows
        ]

    def delete_for_booklet(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        t = m.result_sheets
        with writing(self._conn, f"result sheets of booklet {booklet_id}"):
            self._conn.execute(
                delete(t).where(t.c.college_id == college_id, t.c.booklet_id == booklet_id)
            )
