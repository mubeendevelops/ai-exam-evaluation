"""P23 (D165): migration 0012 on a database seeded under the old schema, its refusal when
content is used across colleges, and ``tarn content relocate-blobs`` run twice."""

import hashlib
import json
from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest

from tarn_adapters.config import Settings
from tarn_adapters.postgres import codec, migrate
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.relocate import relocate_content_blobs
from tarn_adapters.postgres.testing import TestDatabase, create_test_database, drop_test_database
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_core.domain.blueprint import QuestionSlot, Section
from tarn_core.domain.common import BlobKey
from tarn_core.domain.content import KeyFile, Question, ReferenceDiagram
from tarn_core.domain.diagram import DiagramGraph, DiagramNode, NodeShape
from tarn_core.ids import CollegeId, QuestionId
from tarn_core.testing import MemoryBlobStore

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.7\nsynthetic key"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture
def old_schema() -> Iterator[TestDatabase]:
    """A fresh database at revision 0011 (content still global), dropped afterwards."""
    settings = Settings()
    db = create_test_database(settings.database_url, settings.app_database_url)
    try:
        migrate.downgrade(db.owner_url, "0011")
        yield db
    finally:
        drop_test_database(settings.database_url, db.name)


def _college(conn: Conn, code: str) -> tuple[UUID, UUID]:
    college, teacher = uuid4(), uuid4()
    conn.execute(
        "INSERT INTO colleges (id, name, code) VALUES (%s, %s, %s)", (college, f"Test {code}", code)
    )
    conn.execute(
        "INSERT INTO college_directory (id, name) VALUES (%s, %s)", (college, f"Test {code}")
    )
    conn.execute(
        "INSERT INTO users (id, college_id, display_name, email, role) "
        "VALUES (%s, %s, 'Teacher', %s, 'teacher')",
        (teacher, college, f"teacher@{code.lower()}.example"),
    )
    return college, teacher


def _old_content(conn: Conn, college: UUID, teacher: UUID) -> dict[str, UUID]:
    """A subject, a question with a criterion, key, glossary, diagram and key file, and a paper
    linking it: the global-content shape of 0001-0011 (``owning_college_id``, ``global/``)."""
    ids = {k: uuid4() for k in ("subject", "question", "criterion", "diagram", "file", "paper")}
    conn.execute(
        "INSERT INTO subjects (id, version, code, name, owning_college_id, created_by) "
        "VALUES (%s, 1, 'SYN', 'Synthetic', %s, %s)",
        (ids["subject"], college, teacher),
    )
    conn.execute(
        "INSERT INTO questions (id, subject_id, owning_college_id, created_by) "
        "VALUES (%s, %s, %s, %s)",
        (ids["question"], ids["subject"], college, teacher),
    )
    conn.execute(
        "INSERT INTO question_versions (question_id, version, code, text, max_marks, created_by) "
        "VALUES (%s, 1, 'SYN-Q1', 'Explain the synthetic idea.', 2, %s)",
        (ids["question"], teacher),
    )
    conn.execute(
        "INSERT INTO rubric_criteria (id, version, question_id, label, type, weight, params, "
        "owning_college_id, created_by) VALUES (%s, 1, %s, 'Explains', 'semantic', 2, %s, %s, %s)",
        (
            ids["criterion"],
            ids["question"],
            json.dumps({"reference_statement": "The idea follows."}),
            college,
            teacher,
        ),
    )
    conn.execute(
        "INSERT INTO reference_answers (id, version, question_id, text, synthetic, "
        "owning_college_id, created_by) VALUES (%s, 1, %s, 'Model.', true, %s, %s)",
        (uuid4(), ids["question"], college, teacher),
    )
    conn.execute(
        "INSERT INTO glossaries (id, version, question_id, teacher_terms, owning_college_id, "
        "created_by) VALUES (%s, 1, %s, '{alpha}', %s, %s)",
        (uuid4(), ids["question"], college, teacher),
    )
    graph = DiagramGraph(nodes=(DiagramNode(id="n1", shape=NodeShape.PROCESS, label="Start"),))
    for version in (1, 2):  # two versions sharing one PNG, as after a teacher's edit
        conn.execute(
            "INSERT INTO reference_diagrams (id, version, question_id, png_key, graph, "
            "owning_college_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                ids["diagram"],
                version,
                ids["question"],
                f"global/diagrams/{ids['question']}/{ids['diagram']}/flow.png",
                json.dumps(codec.graph_to_json(graph)),
                college,
                teacher,
            ),
        )
    conn.execute(
        "INSERT INTO key_files (id, version, question_id, name, media_type, size_bytes, sha256, "
        "blob_key, no_student_data_confirmed, owning_college_id, created_by) "
        "VALUES (%s, 1, %s, 'key.pdf', 'application/pdf', %s, %s, %s, true, %s, %s)",
        (
            ids["file"],
            ids["question"],
            len(PDF),
            hashlib.sha256(PDF).hexdigest(),
            f"global/keys/{ids['question']}/{ids['file']}/key.pdf",
            college,
            teacher,
        ),
    )
    sections = (
        Section(
            label="A",
            items=(
                QuestionSlot(label="1", marks=Decimal(2), question_id=QuestionId(ids["question"])),
            ),
        ),
    )
    conn.execute(
        "INSERT INTO exam_blueprints (id, version, subject_id, title, total_marks, mark_step, "
        "sections, owning_college_id, created_by) VALUES (%s, 1, %s, 'Synthetic paper', 2, 0.5, "
        "%s, %s, %s)",
        (
            ids["paper"],
            ids["subject"],
            json.dumps(codec.sections_to_json(sections)),
            college,
            teacher,
        ),
    )
    return ids


def _booklet(conn: Conn, college: UUID, teacher: UUID, paper: UUID) -> None:
    student = uuid4()
    conn.execute(
        "INSERT INTO students (id, college_id, name, usn) VALUES (%s, %s, 'Student', %s)",
        (student, college, f"SYN{uuid4().hex[:6]}"),
    )
    conn.execute(
        "INSERT INTO booklets (id, college_id, student_id, blueprint_id, blueprint_version, "
        "file_sha256, uploaded_by, uploaded_at, status, version, sources) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, now(), 'uploaded', 1, '[]')",
        (uuid4(), college, student, paper, "a" * 64, teacher),
    )


def test_0012_back_fills_a_seeded_database_and_relocation_moves_its_files(
    old_schema: TestDatabase,
) -> None:
    db = old_schema
    with db.owner() as conn:
        a, a_teacher = _college(conn, "MA")
        b, b_teacher = _college(conn, "MB")
        a_ids = _old_content(conn, a, a_teacher)
        b_ids = _old_content(conn, b, b_teacher)
        _booklet(conn, a, a_teacher, a_ids["paper"])
        _booklet(conn, b, b_teacher, b_ids["paper"])
    blobs = MemoryBlobStore()
    legacy = {
        f"global/diagrams/{ids['question']}/{ids['diagram']}/flow.png": PNG
        for ids in (a_ids, b_ids)
    } | {f"global/keys/{ids['question']}/{ids['file']}/key.pdf": PDF for ids in (a_ids, b_ids)}
    for key, data in legacy.items():
        blobs.put(BlobKey(key), data, "application/octet-stream")

    migrate.upgrade(db.owner_url)
    assert migrate.current_revision(db.owner_url) == "0012"
    with db.owner() as conn:
        for table in ("subjects", "questions", "question_versions", "reference_diagrams"):
            rows = conn.execute(f"SELECT DISTINCT college_id FROM {table}").fetchall()  # noqa: S608
            assert {r[0] for r in rows} == {a, b}, table
        gone = conn.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND column_name IN ('owning_college_id', 'copied_from_id') "
            "AND table_name NOT IN ('ocr_calibrations')"
        ).fetchone()
        assert gone == (0,)
    with db.app(CollegeId(b)) as conn:
        seen = conn.execute("SELECT id FROM questions").fetchall()
        assert seen == [(b_ids["question"],)]  # A's question is invisible to B

    # The app still reads the legacy global/ keys until they are moved.
    database = PostgresDatabase(db.app_url, pool_size=1)
    ids, clock = UuidGenerator(), SystemClock()
    try:
        with database.session(CollegeId(a), ids=ids, clock=clock, blobs=blobs) as s:
            question = QuestionId(a_ids["question"])
            (diagram,) = s.content.for_question(CollegeId(a), ReferenceDiagram, question)
            assert diagram.png.value.startswith("global/") and s.blobs.get(diagram.png) == PNG

        first = relocate_content_blobs(db.owner_url, blobs)
        assert (first.colleges, first.copied, first.rows, first.missing, first.deleted) == (
            2,
            4,
            6,  # per college: two diagram versions and one key file
            0,
            4,
        )
        again = relocate_content_blobs(db.owner_url, blobs)
        assert (again.copied, again.rows, again.missing, again.deleted) == (0, 0, 0, 0)
        assert not any(blobs.exists(BlobKey(key)) for key in legacy)

        for college, own in ((a, a_ids), (b, b_ids)):
            cid = CollegeId(college)
            with database.session(cid, ids=ids, clock=clock, blobs=blobs) as s:
                question_id = QuestionId(own["question"])
                assert s.content.get(cid, Question, question_id).code == "SYN-Q1"
                versions = s.content.versions(cid, ReferenceDiagram, own["diagram"])
                assert len(versions) == 2
                assert {v.png for v in versions} == {versions[0].png}
                assert versions[0].png.value.startswith(f"college/{college}/content/diagrams/")
                assert s.blobs.get(versions[0].png) == PNG
                (key_file,) = s.content.for_question(cid, KeyFile, question_id)
                assert key_file.blob.value.startswith(f"college/{college}/content/keys/")
                assert s.blobs.get(key_file.blob) == PDF
    finally:
        database.dispose()


def test_0012_refuses_content_used_across_colleges(old_schema: TestDatabase) -> None:
    db = old_schema
    with db.owner() as conn:
        a, a_teacher = _college(conn, "XA")
        b, b_teacher = _college(conn, "XB")
        a_ids = _old_content(conn, a, a_teacher)
        _booklet(conn, b, b_teacher, a_ids["paper"])  # B's booklet on A's paper (D60)
    with pytest.raises(Exception, match="content is used across colleges"):
        migrate.upgrade(db.owner_url)
    assert migrate.current_revision(db.owner_url) == "0011"


def test_0012_downgrade_refuses_a_database_with_content(old_schema: TestDatabase) -> None:
    db = old_schema
    migrate.upgrade(db.owner_url)
    with db.owner() as conn:
        college, teacher = _college(conn, "DA")
        conn.execute(
            "INSERT INTO subjects (id, version, code, name, college_id, created_by) "
            "VALUES (%s, 1, 'S', 'S', %s, %s)",
            (uuid4(), college, teacher),
        )
    with pytest.raises(Exception, match="empty database"):
        migrate.downgrade(db.owner_url, "0011")
    assert migrate.current_revision(db.owner_url) == "0012"
