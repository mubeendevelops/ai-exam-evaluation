"""Helpers for tests against a real PostgreSQL: a throwaway database, application sessions,
and two identically seeded colleges. Tests only; synthetic data only (nothing from samples/)."""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from decimal import Decimal
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import insert
from sqlalchemy.engine import make_url

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.database import PostgresDatabase, PostgresSession, libpq_url
from tarn_adapters.runtime import UuidGenerator
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import (
    Answer,
    AnswerStatus,
    Booklet,
    LineReading,
    Region,
    RegionKind,
)
from tarn_core.domain.common import Box, EngineRef, college_blob_key
from tarn_core.domain.diagram import (
    DiagramEdge,
    DiagramGraph,
    DiagramNode,
    NodeShape,
    StudentDiagram,
)
from tarn_core.domain.review import ResultLine, ResultSheet, Review
from tarn_core.ids import (
    BlueprintId,
    BookletId,
    CollegeId,
    RegionId,
    ResultSheetId,
    ReviewId,
    StudentDiagramId,
)
from tarn_core.testing import FixedClock, MemoryBlobStore
from tarn_core.testing.builders import (
    CollegeFixture,
    add_answer,
    add_college,
    ci_shaped_blueprint,
    make_services,
)

SHARED_HASH = "f" * 64


@dataclass(frozen=True)
class TestDatabase:
    __test__ = False  # not a pytest test class

    name: str
    owner_url: str
    app_url: str

    def owner(self) -> psycopg.Connection[tuple[object, ...]]:
        """Owner connection (a superuser in development: row-level security does not apply)."""
        return psycopg.connect(libpq_url(self.owner_url), autocommit=True)

    @contextmanager
    def app(self, college_id: CollegeId | None) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
        """Raw application-role connection inside one transaction bound to ``college_id``:
        the "crafted query" view an attacker or a buggy query would have."""
        with psycopg.connect(libpq_url(self.app_url)) as conn:
            if college_id is not None:
                conn.execute("SELECT set_config('app.college_id', %s, true)", (str(college_id),))
            yield conn
            conn.rollback()


def with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def create_test_database(owner_url: str, app_url: str) -> TestDatabase:
    """A new, migrated database the application role can log in to."""
    name = f"tarn_test_{uuid4().hex[:12]}"
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db = TestDatabase(
        name=name, owner_url=with_database(owner_url, name), app_url=with_database(app_url, name)
    )
    migrate.upgrade(db.owner_url)
    migrate.grant_app_login(db.owner_url, db.app_url)
    return db


def drop_test_database(owner_url: str, name: str) -> None:
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


@dataclass
class Opener:
    """Opens application sessions sharing one clock, id generator and blob store."""

    database: PostgresDatabase
    clock: FixedClock = field(default_factory=FixedClock)
    ids: UuidGenerator = field(default_factory=UuidGenerator)
    blobs: MemoryBlobStore = field(default_factory=MemoryBlobStore)

    def __call__(self, college_id: CollegeId | None) -> AbstractContextManager[PostgresSession]:
        return self.database.session(college_id, ids=self.ids, clock=self.clock, blobs=self.blobs)


@dataclass(frozen=True)
class Seeded:
    """One college's data: every college table has at least one row of it."""

    college: CollegeFixture
    booklet: Booklet
    answers: tuple[Answer, ...]
    deleted_booklet_id: BookletId

    @property
    def id(self) -> CollegeId:
        return self.college.id


@dataclass(frozen=True)
class World:
    a: Seeded
    b: Seeded
    blueprint: ExamBlueprint


def _seed(open_: Opener, college: CollegeFixture, blueprint_id: BlueprintId) -> Seeded:
    cid = college.id
    with open_(cid) as s:
        svc = make_services(s)
        teacher = college.teacher.id
        booklet = svc.booklets.register(
            cid,
            teacher,
            student_id=college.students[0].id,
            blueprint_id=blueprint_id,
            file_sha256=SHARED_HASH,
        ).booklet
        answers = tuple(add_answer(s, booklet, label) for label in ("1", "2", "8"))
        for answer in answers:
            svc.scoring.score_answer(cid, teacher, answer.id, answer_text="x")

        # Fill the remaining college tables.
        page = s.booklets.pages(cid, booklet.id)[0]
        engine = EngineRef(name="synthetic-ocr", version="1")
        s.booklets.save_region(
            cid,
            Region(
                id=RegionId(s.ids.new()),
                college_id=cid,
                page_id=page.id,
                kind=RegionKind.TEXT_LINE,
                box=Box(x0=0, y0=0, x1=100, y1=20),
                readings=(
                    LineReading(
                        engine=engine,
                        text="alpha",
                        box=Box(x0=0, y0=0, x1=100, y1=20),
                        confidence=0.9,
                        char_confidences=(0.9, 0.8, 0.9, 1.0, 0.7),
                    ),
                    LineReading(
                        engine=EngineRef(name="other-ocr", version="2"),
                        text="alpa",
                        box=Box(x0=0, y0=0, x1=100, y1=20),
                        confidence=0.4,
                    ),
                ),
                chosen=0,
            ),
        )
        segment = s.booklets.segments(cid, booklet.id)[0]
        s.booklets.save_diagram(
            cid,
            StudentDiagram(
                id=StudentDiagramId(s.ids.new()),
                college_id=cid,
                booklet_id=booklet.id,
                segment_id=segment.id,
                box=Box(x0=10, y0=10, x1=200, y1=200),
                graph=DiagramGraph(
                    nodes=(
                        DiagramNode(id="n1", shape=NodeShape.TERMINAL, label="Start"),
                        DiagramNode(
                            id="n2",
                            shape=NodeShape.PROCESS,
                            label="Work",
                            box=Box(x0=1, y0=2, x1=3, y1=4),
                            confidence=0.5,
                        ),
                    ),
                    edges=(DiagramEdge(id="e1", source="n1", target="n2", label="go"),),
                    recognizer=engine,
                ),
            ),
        )
        score = s.scores.scores(cid, answers[0].id)[-1]
        s.scores.save_review(
            cid,
            Review(
                id=ReviewId(s.ids.new()),
                college_id=cid,
                answer_id=answers[0].id,
                answer_score_id=score.id,
                ai_mark=score.mark,
                teacher_mark=Decimal("1.5"),
                reviewer=teacher,
                reviewed_at=s.clock.now(),
                tags=("partial",),
                remarks="synthetic remark",
            ),
        )
        s.booklets.save_answer(cid, replace(answers[0], status=AnswerStatus.APPROVED))
        s.sheets.save(
            cid,
            ResultSheet(
                id=ResultSheetId(s.ids.new()),
                college_id=cid,
                booklet_id=booklet.id,
                version=1,
                lines=(
                    ResultLine(
                        section_label="A",
                        slot_label="1",
                        mark=Decimal("1.5"),
                        counted=True,
                        reason="",
                    ),
                    ResultLine(
                        section_label="A",
                        slot_label="2",
                        mark=None,
                        counted=False,
                        reason="not counted",
                    ),
                ),
                total=Decimal("1.5"),
                max_marks=Decimal(50),
                issued_by=teacher,
                issued_at=s.clock.now(),
                pdf=college_blob_key(cid, "sheets", f"{booklet.id}-v1.pdf"),
            ),
        )
        s.conn.execute(
            insert(m.sentence_embeddings).values(
                id=s.ids.new(),
                college_id=cid,
                answer_id=answers[0].id,
                sentence_index=0,
                embedder_name="synthetic-embedder",
                embedder_version="1",
                dimension=3,
                embedding=[1.0, 0.0, 0.0],
            )
        )
        # A second booklet, deleted: leaves a deletion record.
        doomed = svc.booklets.register(
            cid,
            teacher,
            student_id=college.students[1].id,
            blueprint_id=blueprint_id,
            file_sha256="e" * 64,
        ).booklet
        svc.booklets.delete(cid, teacher, doomed.id)
    return Seeded(college=college, booklet=booklet, answers=answers, deleted_booklet_id=doomed.id)


def new_college(open_: Opener, code: str) -> CollegeFixture:
    college_id = CollegeId(uuid4())
    with open_(college_id) as s:
        return add_college(s, code, college_id)


def seed_world(open_: Opener) -> World:
    """Colleges A and B seeded identically; B uses A's (global) paper."""
    a = new_college(open_, "A")
    b = new_college(open_, "B")
    with open_(a.id) as s:
        blueprint = ci_shaped_blueprint(s, a)
    seeded_b = _seed(open_, b, blueprint.id)
    seeded_a = _seed(open_, a, blueprint.id)
    return World(a=seeded_a, b=seeded_b, blueprint=blueprint)
