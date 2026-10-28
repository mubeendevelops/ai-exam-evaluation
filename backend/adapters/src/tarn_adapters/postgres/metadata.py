"""SQLAlchemy Core tables used to build queries. The schema itself (constraints, row-level
security, grants, triggers) is defined by the migrations; a test checks the two agree."""

from typing import Any

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    Date,
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

SCORING_DIMENSION = 384
"""Dimension of ``sentence_embeddings.embedding`` (migration 0008): the scoring model's (D99)."""

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


def _meta_columns() -> list[Column[Any]]:
    # Content belongs to one college (0012, D165); ``college_id`` was ``owning_college_id``.
    return [
        _uuid("created_by"),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        _seq(),
        _uuid("college_id"),
    ]


def _versioned(name: str, *columns: Column[Any]) -> Table:
    return Table(
        name,
        metadata,
        _uuid("id", primary_key=True),
        _int("version", primary_key=True),
        *columns,
        *_meta_columns(),
    )


# --- colleges and their content (college content since 0012, D165) --------------------------

colleges = Table(
    "colleges",
    metadata,
    _uuid("id", primary_key=True),
    _text("name"),
    _text("code"),
    Column("llm_scoring", Boolean(), nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

# Id and name of every college, readable by all (the public college directory).
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
    *_meta_columns(),
)

subjects = _versioned(
    "subjects",
    _text("code"),
    _text("name"),
    Column("retired", Boolean(), nullable=False, server_default="false"),
)

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
    Column("off_target_terms", ARRAY(Text()), nullable=False, server_default="{}"),
)

reference_diagrams = _versioned(
    "reference_diagrams",
    _uuid("question_id"),
    _text("png_key"),
    Column("graph", JSONB(), nullable=False),
    Column("kind", Text(), nullable=False, server_default="flowchart"),
    Column("recognition", Text(), nullable=False, server_default="pending"),
)

exam_blueprints = _versioned(
    "exam_blueprints",
    _uuid("subject_id"),
    _text("title"),
    Column("course_code", Text(), nullable=False, server_default=""),
    Column("exam_code", Text(), nullable=False, server_default=""),  # 0014 (P26)
    Column("duration_minutes", Integer(), nullable=True),
    _num("total_marks"),
    _num("mark_step"),
    Column("sections", JSONB(), nullable=False),
)

# Tarn-operator content (0006): no owning college and no creating user (both NULL).
ocr_calibrations = Table(
    "ocr_calibrations",
    metadata,
    _uuid("id", primary_key=True),
    _int("version", primary_key=True),
    _text("engine_name"),
    _text("engine_version"),
    Column("params", JSONB(), nullable=False),
    _uuid("owning_college_id", nullable=True),
    _uuid("created_by", nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    _text("copied_from_kind", nullable=True),
    _uuid("copied_from_id", nullable=True),
    _int("copied_from_version", nullable=True),
    _seq(),
    _text("content_class"),
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
    Column("removed_at", DateTime(timezone=True), nullable=True),
)

# An SME's subjects (P24, migration 0013, D160).
sme_subjects = Table(
    "sme_subjects",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("user_id", primary_key=True),
    _uuid("subject_id", primary_key=True),
    Column("subject_version", Integer(), nullable=False, server_default="1"),
    _uuid("granted_by", nullable=True),
    Column("granted_at", DateTime(timezone=True), nullable=False),
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

# Units of work (P26, migration 0014, D162).
units = Table(
    "units",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("id", primary_key=True),
    _text("code"),
    _uuid("paper_id"),
    Column("paper_version", Integer(), nullable=False, server_default="1"),
    _uuid("subject_id"),
    Column("subject_version", Integer(), nullable=False, server_default="1"),
    _text("year"),
    _uuid("owner_id"),
    Column("due_date", Date(), nullable=False),
    Column("state", Text(), nullable=False, server_default="open"),
    _uuid("created_by", nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer(), nullable=False, server_default="1"),
    _seq(),
)

# Transfers and the delegation inbox (P27, migration 0015, D163).
delegation_requests = Table(
    "delegation_requests",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("id", primary_key=True),
    _uuid("unit_id"),
    _uuid("requested_by"),
    _text("reason"),
    Column("requested_at", DateTime(timezone=True), nullable=False),
    Column("state", Text(), nullable=False, server_default="pending"),
    _uuid("closed_by", nullable=True),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("note", Text(), nullable=False, server_default=""),
    _seq(),
)

unit_transfers = Table(
    "unit_transfers",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("id", primary_key=True),
    _uuid("unit_id"),
    _uuid("from_owner"),
    _uuid("to_owner"),
    _text("reason"),
    _uuid("by_user"),
    Column("at", DateTime(timezone=True), nullable=False),
    _uuid("request_id", nullable=True),
    _seq(),
)

# Submit, return, freeze (P28, migration 0016, D164).
unit_submissions = Table(
    "unit_submissions",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("id", primary_key=True),
    _uuid("unit_id"),
    _uuid("submitted_by"),
    Column("submitted_at", DateTime(timezone=True), nullable=False),
    _uuid("returned_by", nullable=True),
    Column("returned_at", DateTime(timezone=True), nullable=True),
    Column("return_note", Text(), nullable=False, server_default=""),
    _seq(),
)

unmarked_booklets = Table(
    "unmarked_booklets",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("submission_id", primary_key=True),
    _uuid("booklet_id", primary_key=True),
    _text("reason"),
    Column("note", Text(), nullable=False, server_default=""),
)

frozen_units = Table(
    "frozen_units",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("unit_id", primary_key=True),
    _text("unit_code"),
    _text("year"),
    _uuid("subject_id"),
    _text("subject_code"),
    _text("subject_name"),
    _uuid("submission_id"),
    _uuid("frozen_by"),
    Column("frozen_at", DateTime(timezone=True), nullable=False),
    Column("papers", JSONB(), nullable=False),
)

frozen_booklets = Table(
    "frozen_booklets",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("unit_id"),
    _uuid("booklet_id", primary_key=True),
    _uuid("student_id"),
    _uuid("paper_id"),
    _int("paper_version"),
    _text("outcome"),
    _text("reason", nullable=True),
    Column("note", Text(), nullable=False, server_default=""),
    _num("total", nullable=True),
    _num("max_marks"),
    Column("sheets", JSONB(), nullable=False),
    _seq(),
)

frozen_answers = Table(
    "frozen_answers",
    metadata,
    _uuid("college_id", primary_key=True),
    _uuid("booklet_id"),
    _uuid("answer_id", primary_key=True),
    _text("slot_label"),
    _uuid("question_id", nullable=True),
    _int("question_version", nullable=True),
    Column("question_code", Text(), nullable=False, server_default=""),
    _num("max_marks"),
    _num("ai_mark", nullable=True),
    _num("sme_mark", nullable=True),
    _uuid("reviewer", nullable=True),
    Column("reviewed_at", DateTime(timezone=True), nullable=True),
    Column("counted", Boolean(), nullable=False),
    _seq(),
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
    _uuid("unit_id"),  # 0014
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
    Column("text_read", Boolean(), nullable=False),
    Column("needs_text", Boolean(), nullable=False),
    Column("ocr_failures", ARRAY(Text()), nullable=False),
    _int("written_number", nullable=True),
    _int("reading_order", nullable=True),
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
    _text("content_class", nullable=True),
    Column("line_score", Double(), nullable=True),
    Column("flagged", Boolean(), nullable=False),
    Column("calibrations", JSONB(), nullable=False),
    _uuid("parent_id", nullable=True),
    _int("row_index", nullable=True),
    _int("col_index", nullable=True),
    Column("struck_out", Boolean(), nullable=False, server_default="false"),
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
    Column("p_calibrated", Double(), nullable=True),
    Column("agreement", Double(), nullable=True),
    Column("lexicon", Double(), nullable=True),
    Column("weight", Double(), nullable=True),
    Column("score", Double(), nullable=True),
    Column("competing", Boolean(), nullable=True),
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
    Column("region_ids", ARRAY(Uuid()), nullable=False),
    Column("flags", ARRAY(Text()), nullable=False),
    _int("position"),
    _text("proposed_label", nullable=True),
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
    Column("rescore_pending", Boolean(), nullable=False, server_default="false"),
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
    _uuid("region_id", nullable=True),
    Column("kind", Text(), nullable=False, server_default="flowchart"),
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
    _num("mark", nullable=True),
    Column("content_versions", JSONB(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    _seq(),
    Column("flags", ARRAY(Text()), nullable=False, server_default="{}"),
    Column("relevance", Double(), nullable=True),
    _text("embedder_name", nullable=True),
    _text("embedder_version", nullable=True),
    Column("reasons", ARRAY(Text()), nullable=False, server_default="{}"),
    Column("llm_usage", JSONB(none_as_null=True), nullable=True),
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
    Column("similarity", Double(), nullable=True),
    Column("reason", JSONB(), nullable=True),
    Column("detail", JSONB(none_as_null=True), nullable=True),
    Column("second_opinion", JSONB(none_as_null=True), nullable=True),
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
    Column("note", Text(), nullable=False, server_default=""),
)

# The review (0010).
booklet_locks = Table(
    "booklet_locks",
    metadata,
    _uuid("college_id"),
    _uuid("booklet_id", primary_key=True),
    _uuid("holder"),
    Column("acquired_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
)

amendments = Table(
    "amendments",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _uuid("answer_id"),
    _uuid("base_review_id"),
    _uuid("opened_by"),
    Column("opened_at", DateTime(timezone=True), nullable=False),
    Column("reason", Text(), nullable=False, server_default=""),
    _uuid("closed_by", nullable=True),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    _text("outcome", nullable=True),
    _int("sheet_version", nullable=True),
    _seq(),
)

region_edits = Table(
    "region_edits",
    metadata,
    _uuid("id", primary_key=True),
    _uuid("college_id"),
    _uuid("booklet_id"),
    _uuid("region_id"),
    _uuid("actor_id"),
    Column("at", DateTime(timezone=True), nullable=False),
    _text("before_text", nullable=True),
    _text("after_text", nullable=True),
    Column("before_struck_out", Boolean(), nullable=False),
    Column("after_struck_out", Boolean(), nullable=False),
    _seq(),
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
    Column("embedding", VECTOR(SCORING_DIMENSION), nullable=False),
    _text("text_sha256"),
)

# Tarn-operator content (0008): scoring bands and flag thresholds per embedding model.
scoring_calibrations = Table(
    "scoring_calibrations",
    metadata,
    _uuid("id", primary_key=True),
    _int("version", primary_key=True),
    _text("embedder_name"),
    Column("params", JSONB(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
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

CONTENT_TABLES: tuple[Table, ...] = (
    subjects,
    questions,
    question_versions,
    reference_answers,
    rubric_criteria,
    glossaries,
    reference_diagrams,
    key_files,
    exam_blueprints,
)
"""A college's content: college RLS since 0012 (D165)."""
COLLEGE_TABLES: tuple[Table, ...] = (
    colleges,
    users,
    sme_subjects,
    students,
    units,
    delegation_requests,
    unit_transfers,
    unit_submissions,
    unmarked_booklets,
    frozen_units,
    frozen_booklets,
    frozen_answers,
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
    booklet_locks,
    amendments,
    region_edits,
    *CONTENT_TABLES,
)
GLOBAL_TABLES: tuple[Table, ...] = (
    ocr_calibrations,
    scoring_calibrations,
)
"""Tarn-operator content: every college reads, only the migration owner writes (D72)."""
