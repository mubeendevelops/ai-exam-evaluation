"""Initial schema: global content, college data, row-level security, append-only audit.

Revision ID: 0001
Revises:
Create Date: 2026-10-03

Frozen: never edit after it has been applied anywhere; add a new revision instead.

Rules implemented here (CLAUDE.md "Persistence (P3)"):
- Every college table has ``college_id`` and ENABLE + FORCE row-level security keyed on the
  per-transaction setting ``app.college_id``. No setting = no rows.
- Global content is readable by every college; a new row (or version) may only be inserted
  for the college set in the transaction, and only by the item's owning college. Versions
  are append-only and contiguous.
- The application role ``tarn_app`` has no superuser, no BYPASSRLS and owns nothing.
- ``audit_events`` and ``deletion_records``: the app role may SELECT and INSERT only. A
  trigger refuses every UPDATE/DELETE/TRUNCATE except the redaction of a deleted booklet's
  events (before/after set to NULL) by ``tarn_redact_booklet_audit`` (D32).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "tarn_app"

# --- global content -------------------------------------------------------------------------

_META = """
    owning_college_id uuid NOT NULL REFERENCES colleges (id),
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    copied_from_kind text,
    copied_from_id uuid,
    copied_from_version integer,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    CHECK ((copied_from_kind IS NULL) = (copied_from_id IS NULL)
       AND (copied_from_id IS NULL) = (copied_from_version IS NULL))
"""

_VERSION_META = """
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    copied_from_kind text,
    copied_from_id uuid,
    copied_from_version integer,
    seq bigint GENERATED ALWAYS AS IDENTITY,
    CHECK ((copied_from_kind IS NULL) = (copied_from_id IS NULL)
       AND (copied_from_id IS NULL) = (copied_from_version IS NULL))
"""

# Versioned global tables keyed (id, version), each with the owner columns of _META.
GLOBAL_VERSIONED = {
    "subjects": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        code text NOT NULL CHECK (btrim(code) <> ''),
        name text NOT NULL CHECK (btrim(name) <> ''),
    """,
    "reference_answers": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        question_id uuid NOT NULL REFERENCES questions (id),
        text text NOT NULL,
        synthetic boolean NOT NULL DEFAULT false,
        guidance_only boolean NOT NULL DEFAULT false,
    """,
    "rubric_criteria": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        question_id uuid NOT NULL REFERENCES questions (id),
        label text NOT NULL,
        type text NOT NULL CHECK (type IN ('list', 'numeric', 'semantic', 'diagram', 'llm')),
        weight numeric NOT NULL CHECK (weight > 0),
        params jsonb NOT NULL,
    """,
    "glossaries": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        question_id uuid NOT NULL REFERENCES questions (id),
        teacher_terms text[] NOT NULL DEFAULT '{}',
        reference_labels text[] NOT NULL DEFAULT '{}',
    """,
    "reference_diagrams": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        question_id uuid NOT NULL REFERENCES questions (id),
        png_key text NOT NULL CHECK (png_key LIKE 'global/%'),
        graph jsonb NOT NULL,
    """,
    "exam_blueprints": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        subject_id uuid NOT NULL,
        title text NOT NULL,
        total_marks numeric NOT NULL CHECK (total_marks > 0),
        mark_step numeric NOT NULL CHECK (mark_step > 0),
        sections jsonb NOT NULL,
    """,
    "ocr_calibrations": """
        id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        engine_name text NOT NULL,
        engine_version text NOT NULL,
        params jsonb NOT NULL,
    """,
}

# --- college data ---------------------------------------------------------------------------
# Order matters: parents first. Composite foreign keys (college_id, x_id) make a row unable to
# point at another college's parent, whatever RLS says.
COLLEGE_TABLES = {
    "users": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL REFERENCES colleges (id),
        display_name text NOT NULL,
        email text NOT NULL,
        role text NOT NULL CHECK (role IN ('admin', 'teacher')),
        active boolean NOT NULL DEFAULT true,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id)
    """,
    "students": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL REFERENCES colleges (id),
        name text NOT NULL,
        usn text NOT NULL,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        UNIQUE (college_id, usn)
    """,
    "booklets": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL REFERENCES colleges (id),
        student_id uuid NOT NULL,
        blueprint_id uuid NOT NULL,
        blueprint_version integer NOT NULL,
        file_sha256 text NOT NULL CHECK (file_sha256 ~ '^[0-9a-f]{64}$'),
        uploaded_by uuid NOT NULL,
        uploaded_at timestamptz NOT NULL,
        status text NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        FOREIGN KEY (college_id, student_id) REFERENCES students (college_id, id),
        FOREIGN KEY (college_id, uploaded_by) REFERENCES users (college_id, id),
        FOREIGN KEY (blueprint_id, blueprint_version) REFERENCES exam_blueprints (id, version)
    """,
    "pages": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        page_index integer NOT NULL CHECK (page_index >= 0),
        image_key text NOT NULL,
        width integer NOT NULL CHECK (width > 0),
        height integer NOT NULL CHECK (height > 0),
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        CHECK (image_key LIKE 'college/' || college_id::text || '/%'),
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE
    """,
    "regions": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        page_id uuid NOT NULL,
        kind text NOT NULL,
        box integer[] NOT NULL CHECK (cardinality(box) = 4),
        chosen integer,
        teacher_text text,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        FOREIGN KEY (college_id, page_id) REFERENCES pages (college_id, id) ON DELETE CASCADE
    """,
    "line_readings": """
        college_id uuid NOT NULL,
        region_id uuid NOT NULL,
        ordinal integer NOT NULL CHECK (ordinal >= 0),
        engine_name text NOT NULL,
        engine_version text NOT NULL,
        text text NOT NULL,
        box integer[] NOT NULL CHECK (cardinality(box) = 4),
        confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        char_confidences double precision[],
        PRIMARY KEY (region_id, ordinal),
        FOREIGN KEY (college_id, region_id) REFERENCES regions (college_id, id)
            ON DELETE CASCADE
    """,
    "segments": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        slot_label text,
        spans jsonb NOT NULL,
        source text NOT NULL,
        match_score double precision CHECK (match_score BETWEEN 0 AND 1),
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE
    """,
    "answers": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        slot_label text NOT NULL,
        segment_ids uuid[] NOT NULL,
        status text NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE
    """,
    "diagram_graphs": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        segment_id uuid NOT NULL,
        box integer[] NOT NULL CHECK (cardinality(box) = 4),
        graph jsonb NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        seq bigint GENERATED ALWAYS AS IDENTITY,
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, segment_id) REFERENCES segments (college_id, id)
            ON DELETE CASCADE
    """,
    "answer_scores": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        answer_id uuid NOT NULL,
        question_id uuid NOT NULL,
        question_version integer NOT NULL,
        mark_step numeric NOT NULL CHECK (mark_step > 0),
        mark numeric NOT NULL,
        content_versions jsonb NOT NULL,
        created_at timestamptz NOT NULL,
        seq bigint GENERATED ALWAYS AS IDENTITY,
        UNIQUE (college_id, id),
        FOREIGN KEY (college_id, answer_id) REFERENCES answers (college_id, id)
            ON DELETE CASCADE
    """,
    "criterion_scores": """
        college_id uuid NOT NULL,
        answer_score_id uuid NOT NULL,
        ordinal integer NOT NULL CHECK (ordinal >= 0),
        criterion_id uuid NOT NULL,
        criterion_version integer NOT NULL,
        weight numeric NOT NULL CHECK (weight > 0),
        credit numeric NOT NULL CHECK (credit BETWEEN 0 AND 1),
        scorer_name text NOT NULL,
        scorer_version text NOT NULL,
        evidence text NOT NULL DEFAULT '',
        flags text[] NOT NULL DEFAULT '{}',
        PRIMARY KEY (answer_score_id, ordinal),
        FOREIGN KEY (college_id, answer_score_id) REFERENCES answer_scores (college_id, id)
            ON DELETE CASCADE
    """,
    "reviews": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        answer_id uuid NOT NULL,
        answer_score_id uuid,
        ai_mark numeric,
        teacher_mark numeric NOT NULL,
        reviewer uuid NOT NULL,
        reviewed_at timestamptz NOT NULL,
        tags text[] NOT NULL DEFAULT '{}',
        remarks text NOT NULL DEFAULT '',
        seq bigint GENERATED ALWAYS AS IDENTITY,
        CHECK ((ai_mark IS NULL) = (answer_score_id IS NULL)),
        FOREIGN KEY (college_id, answer_id) REFERENCES answers (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, answer_score_id) REFERENCES answer_scores (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, reviewer) REFERENCES users (college_id, id)
    """,
    "result_sheets": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        booklet_id uuid NOT NULL,
        version integer NOT NULL CHECK (version >= 1),
        lines jsonb NOT NULL,
        total numeric NOT NULL,
        max_marks numeric NOT NULL CHECK (max_marks > 0),
        issued_by uuid NOT NULL,
        issued_at timestamptz NOT NULL,
        pdf_key text,
        UNIQUE (booklet_id, version),
        CHECK (pdf_key IS NULL OR pdf_key LIKE 'college/' || college_id::text || '/%'),
        FOREIGN KEY (college_id, booklet_id) REFERENCES booklets (college_id, id)
            ON DELETE CASCADE,
        FOREIGN KEY (college_id, issued_by) REFERENCES users (college_id, id)
    """,
    # Sentence embeddings of an answer (P13). The dimension is fixed by the model chosen in
    # P13; until then the column is untyped and the row records its own dimension.
    "sentence_embeddings": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL,
        answer_id uuid NOT NULL,
        sentence_index integer NOT NULL CHECK (sentence_index >= 0),
        embedder_name text NOT NULL,
        embedder_version text NOT NULL,
        dimension integer NOT NULL CHECK (dimension > 0),
        embedding vector NOT NULL,
        CHECK (vector_dims(embedding) = dimension),
        UNIQUE (answer_id, sentence_index, embedder_name, embedder_version),
        FOREIGN KEY (college_id, answer_id) REFERENCES answers (college_id, id)
            ON DELETE CASCADE
    """,
    # No foreign keys to booklets or answers: events outlive what they describe.
    "audit_events": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL REFERENCES colleges (id),
        actor_id uuid NOT NULL,
        at timestamptz NOT NULL,
        action text NOT NULL,
        booklet_id uuid,
        answer_id uuid,
        before jsonb,
        after jsonb,
        seq bigint GENERATED ALWAYS AS IDENTITY
    """,
    # Who deleted which booklet, when. Content-free by construction (D14, design decision 8).
    "deletion_records": """
        id uuid PRIMARY KEY,
        college_id uuid NOT NULL REFERENCES colleges (id),
        booklet_id uuid NOT NULL,
        actor_id uuid NOT NULL,
        at timestamptz NOT NULL,
        UNIQUE (college_id, booklet_id)
    """,
}

# Privileges of the app role on college tables. Score history and result sheets are never
# updated (only deleted with their booklet); audit and deletion records are insert-only.
_FULL = "SELECT, INSERT, UPDATE, DELETE"
COLLEGE_GRANTS = {
    "colleges": "SELECT, INSERT, UPDATE",
    "users": _FULL,
    "students": _FULL,
    "booklets": _FULL,
    "pages": _FULL,
    "regions": _FULL,
    "line_readings": _FULL,
    "segments": _FULL,
    "answers": _FULL,
    "diagram_graphs": _FULL,
    "answer_scores": "SELECT, INSERT, DELETE",
    "criterion_scores": "SELECT, INSERT, DELETE",
    "reviews": "SELECT, INSERT, DELETE",
    "result_sheets": "SELECT, INSERT, DELETE",
    "sentence_embeddings": _FULL,
    "audit_events": "SELECT, INSERT",
    "deletion_records": "SELECT, INSERT",
}

GLOBAL_TABLES = ("questions", "question_versions", *GLOBAL_VERSIONED)

_CURRENT = "tarn_current_college()"


def _role_sql() -> list[str]:
    return [
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END $$
        """,
        # Re-asserted on every upgrade, whatever was done to the role by hand.
        f"ALTER ROLE {APP_ROLE} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT",
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    ]


def _functions_sql() -> list[str]:
    return [
        """
        CREATE FUNCTION tarn_current_college() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.college_id', true), '')::uuid $$
        """,
        # Versions of one item are contiguous and keep the owner of version 1.
        """
        CREATE FUNCTION tarn_check_content_version() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            key_col text := TG_ARGV[0];
            check_owner boolean := TG_ARGV[1]::boolean;
            item uuid;
            latest integer;
            first_owner uuid;
        BEGIN
            EXECUTE format('SELECT ($1).%I', key_col) INTO item USING NEW;
            EXECUTE format('SELECT max(version) FROM %I.%I WHERE %I = $1',
                           TG_TABLE_SCHEMA, TG_TABLE_NAME, key_col)
                INTO latest USING item;
            IF NEW.version <> coalesce(latest, 0) + 1 THEN
                RAISE EXCEPTION '%: % expected version %, got %',
                    TG_TABLE_NAME, item, coalesce(latest, 0) + 1, NEW.version
                    USING ERRCODE = 'check_violation';
            END IF;
            IF check_owner AND NEW.version > 1 THEN
                EXECUTE format('SELECT owning_college_id FROM %I.%I WHERE %I = $1 AND version = 1',
                               TG_TABLE_SCHEMA, TG_TABLE_NAME, key_col)
                    INTO first_owner USING item;
                IF first_owner IS DISTINCT FROM NEW.owning_college_id THEN
                    RAISE EXCEPTION '%: % cannot change its owning college', TG_TABLE_NAME, item
                        USING ERRCODE = 'insufficient_privilege';
                END IF;
            END IF;
            RETURN NEW;
        END $$
        """,
        # Audit rows are never changed or removed, except that redaction may set before/after
        # to NULL. Applies to every role, the owner included.
        """
        CREATE FUNCTION tarn_audit_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND NEW.before IS NULL AND NEW.after IS NULL
               AND (NEW.id, NEW.college_id, NEW.actor_id, NEW.at, NEW.action,
                    NEW.booklet_id, NEW.answer_id, NEW.seq)
                   IS NOT DISTINCT FROM
                   (OLD.id, OLD.college_id, OLD.actor_id, OLD.at, OLD.action,
                    OLD.booklet_id, OLD.answer_id, OLD.seq) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only (% refused)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$
        """,
        """
        CREATE FUNCTION tarn_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only (% refused)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END $$
        """,
    ]


def _redaction_sql() -> list[str]:
    # Runs as the owner. Only for a booklet that is gone and has a deletion record in the
    # caller's college; touches only that booklet's events in that college.
    return [
        f"""
        CREATE FUNCTION tarn_redact_booklet_audit(p_booklet uuid) RETURNS integer
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
        DECLARE
            college uuid := {_CURRENT};
            n integer;
        BEGIN
            IF college IS NULL THEN
                RAISE EXCEPTION 'no college set' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF EXISTS (SELECT 1 FROM booklets WHERE id = p_booklet) THEN
                RAISE EXCEPTION 'booklet % still exists', p_booklet
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM deletion_records
                           WHERE booklet_id = p_booklet AND college_id = college) THEN
                RAISE EXCEPTION 'booklet % has no deletion record', p_booklet
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            UPDATE audit_events SET before = NULL, after = NULL
             WHERE booklet_id = p_booklet AND college_id = college
               AND (before IS NOT NULL OR after IS NOT NULL);
            GET DIAGNOSTICS n = ROW_COUNT;
            RETURN n;
        END $$
        """,
        "REVOKE ALL ON FUNCTION tarn_redact_booklet_audit(uuid) FROM PUBLIC",
        f"GRANT EXECUTE ON FUNCTION tarn_redact_booklet_audit(uuid) TO {APP_ROLE}",
    ]


def _tables_sql() -> list[str]:
    sql = [
        """
        CREATE TABLE colleges (
            id uuid PRIMARY KEY,
            name text NOT NULL CHECK (btrim(name) <> ''),
            code text NOT NULL CHECK (btrim(code) <> ''),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE questions (
            id uuid PRIMARY KEY,
            subject_id uuid NOT NULL,
            {_META}
        )
        """,
        f"""
        CREATE TABLE question_versions (
            question_id uuid NOT NULL REFERENCES questions (id),
            version integer NOT NULL CHECK (version >= 1),
            text text NOT NULL CHECK (btrim(text) <> ''),
            max_marks numeric NOT NULL CHECK (max_marks > 0),
            category text NOT NULL DEFAULT '',
            {_VERSION_META},
            PRIMARY KEY (question_id, version)
        )
        """,
    ]
    for name, columns in GLOBAL_VERSIONED.items():
        sql.append(f"CREATE TABLE {name} ({columns} {_META}, PRIMARY KEY (id, version))")
    for name, columns in COLLEGE_TABLES.items():
        sql.append(f"CREATE TABLE {name} ({columns})")
    sql += [
        "CREATE INDEX ON booklets (college_id, file_sha256)",
        "CREATE INDEX ON pages (booklet_id)",
        "CREATE INDEX ON regions (page_id)",
        "CREATE INDEX ON segments (booklet_id)",
        "CREATE INDEX ON answers (booklet_id)",
        "CREATE INDEX ON diagram_graphs (booklet_id)",
        "CREATE INDEX ON answer_scores (answer_id)",
        "CREATE INDEX ON reviews (answer_id)",
        "CREATE INDEX ON audit_events (college_id, booklet_id)",
        *(
            f"CREATE INDEX ON {t} (question_id)"
            for t in ("reference_answers", "rubric_criteria", "glossaries", "reference_diagrams")
        ),
    ]
    return sql


def _security_sql() -> list[str]:
    sql: list[str] = []
    # College tables: one policy for every command, keyed on the transaction's college.
    for table, grants in COLLEGE_GRANTS.items():
        key = "id" if table == "colleges" else "college_id"
        sql += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY college_isolation ON {table} "
            f"USING ({key} = {_CURRENT}) WITH CHECK ({key} = {_CURRENT})",
            f"GRANT {grants} ON {table} TO {APP_ROLE}",
        ]
    # Global content: everyone reads; only the owning college inserts; nobody updates.
    for table in GLOBAL_TABLES:
        if table == "question_versions":
            owner_check = (
                "EXISTS (SELECT 1 FROM questions q WHERE q.id = question_id "
                f"AND q.owning_college_id = {_CURRENT})"
            )
        else:
            owner_check = f"owning_college_id = {_CURRENT}"
        sql += [
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY global_read ON {table} FOR SELECT USING (true)",
            f"CREATE POLICY owner_insert ON {table} FOR INSERT WITH CHECK ({owner_check})",
            f"GRANT SELECT, INSERT ON {table} TO {APP_ROLE}",
        ]
    for table in GLOBAL_VERSIONED:
        sql.append(
            f"CREATE TRIGGER content_version BEFORE INSERT ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION tarn_check_content_version('id', 'true')"
        )
    sql.append(
        "CREATE TRIGGER content_version BEFORE INSERT ON question_versions FOR EACH ROW "
        "EXECUTE FUNCTION tarn_check_content_version('question_id', 'false')"
    )
    sql += [
        "CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION tarn_audit_guard()",
        "CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION tarn_append_only()",
        "CREATE TRIGGER append_only BEFORE UPDATE OR DELETE ON deletion_records "
        "FOR EACH ROW EXECUTE FUNCTION tarn_append_only()",
        "CREATE TRIGGER append_only_truncate BEFORE TRUNCATE ON deletion_records "
        "FOR EACH STATEMENT EXECUTE FUNCTION tarn_append_only()",
        # Identity columns draw from sequences.
        f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    ]
    return sql


def upgrade() -> None:
    for statement in (
        "CREATE EXTENSION IF NOT EXISTS vector",
        *_role_sql(),
        *_functions_sql(),
        *_tables_sql(),
        *_redaction_sql(),
        *_security_sql(),
    ):
        op.execute(statement)


def downgrade() -> None:
    # The role is cluster-wide (other databases may use it), so it is kept; its grants go
    # with the tables. The vector extension is kept too: the dev init script installs it.
    tables = [*reversed(COLLEGE_TABLES), *reversed(GLOBAL_VERSIONED), "question_versions"]
    tables += ["questions", "colleges"]
    for table in tables:
        op.execute(f"DROP TABLE {table}")
    for function in (
        "tarn_redact_booklet_audit(uuid)",
        "tarn_append_only()",
        "tarn_audit_guard()",
        "tarn_check_content_version()",
        "tarn_current_college()",
    ):
        op.execute(f"DROP FUNCTION {function}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
