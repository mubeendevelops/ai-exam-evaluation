"""SQLAlchemy Core tables used to build queries. The schema itself (constraints, row-level
security, grants, triggers) is defined by the migrations; a test checks the two agree."""

from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Double,
    Identity,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def _uuid(name: str, *, nullable: bool = False, primary_key: bool = False) -> Column[Any]:
    return Column(name, Uuid(), nullable=nullable, primary_key=primary_key)


def _num(name: str, *, nullable: bool = False) -> Column[Any]:
    # asdecimal: marks and credits are Decimal end to end.
    return Column(name, Numeric(asdecimal=True), nullable=nullable)


def _text(name: str, *, nullable: bool = False) -> Column[Any]:
    return Column(name, Text(), nullable=nullable)


def _int(name: str, *, nullable: bool = False, primary_key: bool = False) -> Column[Any]:
    return Column(name, Integer(), nullable=nullable, primary_key=primary_key)


def _seq() -> Column[Any]:
    # Identity column: insertion order, for "oldest first" listings. Never written.
    return Column("seq", BigInteger(), Identity(always=True), nullable=False)


def _box(name: str = "box") -> Column[Any]:
    return Column(name, ARRAY(Integer()), nullable=False)


def _meta_columns(*, owner: bool = True) -> list[Column[Any]]:
    cols: list[Column[Any]] = []
    if owner:
        cols.append(_uuid("owning_college_id"))
    cols += [
        _uuid("created_by"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        _text("copied_from_kind", nullable=True),
        _uuid("copied_from_id", nullable=True),
        _int("copied_from_version", nullable=True),
        _seq(),
    ]
    return cols


def _versioned(name: str, *columns: Column[Any]) -> Table:
    return Table(
        name,
        metadata,
        _uuid("id", primary_key=True),
        _int("version", primary_key=True),
        *columns,
        *_meta_columns(),
    )


# --- global content ---------------------------------------------------------------------------

colleges = Table(
    "colleges",
    metadata,
    _uuid("id", primary_key=True),
    _text("name"),
    _text("code"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Id and name of every college, readable by all: who owns a piece of global content is public.
college_directory = Table(
    "college_directory", metadata, _uuid("id", primary_key=True), _text("name")
)

questions = Table(
    "questions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("subject_id"),
    *_meta_columns(),
)

question_versions = Table(
    "question_versions",
    metadata,
    _uuid("question_id", primary_key=True),
    _int("version", primary_key=True),
    _text("code"),
    _text("text"),
    _num("max_marks"),
    _text("category"),
    _text("difficulty"),
    *_meta_columns(owner=False),
)

subjects = _versioned("subjects", _text("code"), _text("name"))

reference_answers = _versioned(
    "reference_answers",
    _uuid("question_id"),
    _text("text"),
    Column("synthetic", Boolean(), nullable=False),
    Column("guidance_only", Boolean(), nullable=False),
    Column("retired", Boolean(), nullable=False, server_default="false"),
)

rubric_criteria = _versioned(
    "rubric_criteria",
    _uuid("question_id"),
    _text("label"),
    _text("type"),
    _num("weight"),
    Column("params", JSONB(), nullable=False),
    Column("retired", Boolean(), nullable=False, server_default="false"),
)

key_files = _versioned(
    "key_files",
    _uuid("question_id"),
    _text("name"),
    _text("media_type"),
    Column("size_bytes", BigInteger(), nullable=False),
    _text("sha256"),
    _text("blob_key"),
    Column("keywords", ARRAY(Text()), nullable=False),
    Column("no_student_data_confirmed", Boolean(), nullable=False),
)

glossaries = _versioned(
    "glossaries",
    _uuid("question_id"),
    Column("teacher_terms", ARRAY(Text()), nullable=False),
    Column("reference_labels", ARRAY(Text()), nullable=False),
)

reference_diagrams = _versioned(
    "reference_diagrams",
    _uuid("question_id"),
    _text("png_key"),
    Column("graph", JSONB(), nullable=False),
)

exam_blueprints = _versioned(
    "exam_blueprints",
    _uuid("subject_id"),
    _text("title"),
    Column("course_code", Text(), nullable=False, server_default=""),
    Column("duration_minutes", Integer(), nullable=True),
    _num("total_marks"),
    _num("mark_step"),
    Column("sections", JSONB(), nullable=False),
)

ocr_calibrations = _versioned(
    "ocr_calibrations",
    _text("engine_name"),
    _text("engine_version"),
    Column("params", JSONB(), nullable=False),
)

# --- college data -----------------------------------------------------------------------------

users = Table(
    "users",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _text("display_name"),
    _text("email"),
    _text("role"),
    Column("active", Boolean(), nullable=False),
    _seq(),
)

students = Table(
    "students",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _text("name"),
    _text("usn"),
    _seq(),
    _text("class_section"),
)

booklets = Table(
    "booklets",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("student_id"),
    _uuid("blueprint_id"),
    _int("blueprint_version"),
    _text("file_sha256"),
    _uuid("uploaded_by"),
    Column("uploaded_at", DateTime(timezone=True), nullable=False),
    _text("status"),
    _int("version"),
    Column("sources", JSONB(), nullable=False),
    _text("failure_reason", nullable=True),
    _seq(),
)

pages = Table(
    "pages",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _int("page_index"),
    _text("image_key"),
    _int("width"),
    _int("height"),
    _text("original_key", nullable=True),
    Column("cleaned", Boolean(), nullable=False),
    Column("use_anyway", Boolean(), nullable=False),
    Column("metrics", JSONB(), nullable=True),
    Column("retake_reasons", ARRAY(Text()), nullable=False),
    _seq(),
)

regions = Table(
    "regions",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("page_id"),
    _text("kind"),
    _box(),
    _int("chosen", nullable=True),
    _text("teacher_text", nullable=True),
    _seq(),
)

line_readings = Table(
    "line_readings",
    metadata,
    _uuid("college_id"),
    _uuid("region_id", primary_key=True),
    _int("ordinal", primary_key=True),
    _text("engine_name"),
    _text("engine_version"),
    _text("text"),
    _box(),
    Column("confidence", Double(), nullable=False),
    Column("char_confidences", ARRAY(Double()), nullable=True),
)

segments = Table(
    "segments",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _text("slot_label", nullable=True),
    Column("spans", JSONB(), nullable=False),
    _text("source"),
    Column("match_score", Double(), nullable=True),
    _seq(),
)

answers = Table(
    "answers",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _text("slot_label"),
    Column("segment_ids", ARRAY(Uuid()), nullable=False),
    _text("status"),
    _int("version"),
    _seq(),
)

diagram_graphs = Table(
    "diagram_graphs",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _uuid("segment_id"),
    _box(),
    Column("graph", JSONB(), nullable=False),
    _int("version"),
    _seq(),
)

answer_scores = Table(
    "answer_scores",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("answer_id"),
    _uuid("question_id"),
    _int("question_version"),
    _num("mark_step"),
    _num("mark"),
    Column("content_versions", JSONB(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    _seq(),
)

criterion_scores = Table(
    "criterion_scores",
    metadata,
    _uuid("college_id"),
    _uuid("answer_score_id", primary_key=True),
    _int("ordinal", primary_key=True),
    _uuid("criterion_id"),
    _int("criterion_version"),
    _num("weight"),
    _num("credit"),
    _text("scorer_name"),
    _text("scorer_version"),
    _text("evidence"),
    Column("flags", ARRAY(Text()), nullable=False),
)

reviews = Table(
    "reviews",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("answer_id"),
    _uuid("answer_score_id", nullable=True),
    _num("ai_mark", nullable=True),
    _num("teacher_mark"),
    _uuid("reviewer"),
    Column("reviewed_at", DateTime(timezone=True), nullable=False),
    Column("tags", ARRAY(Text()), nullable=False),
    _text("remarks"),
    _seq(),
)

result_sheets = Table(
    "result_sheets",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _int("version"),
    Column("lines", JSONB(), nullable=False),
    _num("total"),
    _num("max_marks"),
    _uuid("issued_by"),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    _text("pdf_key", nullable=True),
)

sentence_embeddings = Table(
    "sentence_embeddings",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("answer_id"),
    _int("sentence_index"),
    _text("embedder_name"),
    _text("embedder_version"),
    _int("dimension"),
    Column("embedding", VECTOR(), nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("actor_id", nullable=True),
    Column("at", DateTime(timezone=True), nullable=False),
    _text("action"),
    _uuid("booklet_id", nullable=True),
    _uuid("answer_id", nullable=True),
    # none_as_null: a Python None is SQL NULL, not the JSON value null.
    Column("before", JSONB(none_as_null=True), nullable=True),
    Column("after", JSONB(none_as_null=True), nullable=True),
    _seq(),
)

deletion_records = Table(
    "deletion_records",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _uuid("actor_id"),
    Column("at", DateTime(timezone=True), nullable=False),
)

jobs = Table(
    "jobs",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _text("kind"),
    Column("payload", JSONB(), nullable=False),
    _text("dedupe_key", nullable=True),
    _text("status"),
    _int("attempts"),
    _int("max_attempts"),
    Column("run_after", DateTime(timezone=True), nullable=False),
    _text("locked_by", nullable=True),
    Column("locked_until", DateTime(timezone=True), nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    _text("last_error", nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _seq(),
)

COLLEGE_TABLES: tuple[Table, ...] = (
    colleges,
    users,
    students,
    booklets,
    pages,
    regions,
    line_readings,
    segments,
    answers,
    diagram_graphs,
    answer_scores,
    criterion_scores,
    reviews,
    result_sheets,
    sentence_embeddings,
    audit_events,
    deletion_records,
    jobs,
)
GLOBAL_TABLES: tuple[Table, ...] = (
    subjects,
    questions,
    question_versions,
    reference_answers,
    rubric_criteria,
    glossaries,
    reference_diagrams,
    key_files,
    exam_blueprints,
    ocr_calibrations,
)
