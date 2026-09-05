"""tests/test_api/conftest.py — fixtures shared by the API test modules.

Every test here runs against a LIVE Postgres (marked `db`, the suite's
existing convention) through the real dependency chain — httpx.AsyncClient
over the ASGI app, real psycopg2 connections, real RLS context. Nothing is
mocked, because the things under test are precisely the seams a mock would
paper over: whether SET LOCAL took effect inside the request's transaction,
whether FOR UPDATE SKIP LOCKED actually serializes two claimers, and whether
the endpoint's WHERE clause really excludes another tenant's row.
"""
from __future__ import annotations

import pathlib
import uuid

import httpx
import pytest

import core.db
import core.jobs
from api.deps.db import set_admin_context
from api.deps.identity import DEBUG_COLLEGE_HEADER
from api.main import create_app
from api.settings import Settings

#: seed_minimal.sql's demo college — the tenant that actually exists.
COLLEGE_A = "11111111-1111-1111-1111-111111111111"

#: A second, deliberately NON-EXISTENT college id. evaluation_jobs.college_id
#: has an FK to colleges, so anything actually inserted for tenant B has to use
#: a real college — see the `college_b` fixture. For read-only "can B see A's
#: job?" checks this value is enough, and using an id with no rows of its own
#: makes an accidental leak obvious.
COLLEGE_B_ABSENT = "22222222-2222-2222-2222-222222222222"

#: Smallest byte string that passes the endpoint's %PDF- signature check.
#: Used for most tests deliberately: they exercise upload PLUMBING (size caps,
#: storage refs, job rows), and nothing in that path parses the PDF, so a
#: 300 KB fixture would only make them slower. The one test that wants a real
#: booklet asks for `sample_booklet_bytes`.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLE_BOOKLET = REPO_ROOT / "media" / "booklets" / "sample_booklet.pdf"


@pytest.fixture
def api_settings():
    """Settings for the test app: debug endpoints on, dummy storage.

    dummy storage on purpose — core/storage.py's dummy mode needs no MinIO, no
    boto3 and no network, so the upload tests run on a bare checkout. The
    'minio' branch is one `core.storage.upload_file` call away and is exercised
    by the CLI scripts that already use it.
    """
    return Settings(enable_debug_endpoints=True, storage_mode="dummy")


@pytest.fixture
def make_client(_dotenv, api_settings):
    """Factory: make_client(college_id) -> httpx.AsyncClient already sending
    that college's stub credential.

    A factory rather than one client because the cross-tenant tests need two
    callers in the same test, and a shared client with mutated headers would
    make it far too easy to write a test that passes for the wrong reason.
    """
    app = create_app(api_settings)

    def make(college_id=COLLEGE_A, *, raise_app_exceptions: bool = True) -> httpx.AsyncClient:
        # raise_app_exceptions=True (httpx's default) re-raises an unhandled
        # server exception into the test instead of returning the 500 the app's
        # handler produced — which is what you want almost always, so a broken
        # endpoint fails with its real traceback rather than an opaque 500.
        # The one test that asserts the 500 RESPONSE itself passes False.
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
            base_url="http://testserver",
            headers={DEBUG_COLLEGE_HEADER: str(college_id)},
        )

    return make


@pytest.fixture
def admin_conn(_dotenv):
    """A committed-writes connection with the platform-admin RLS bypass.

    Distinct from conftest.py's `db_conn`, which rolls back on teardown. Tests
    here need to SEE rows the API committed in its own connection, and to
    delete them afterwards for real — a rolled-back cleanup would leave the
    job table growing across runs.
    """
    conn = core.db.get_connection()
    with conn.cursor() as cur:
        set_admin_context(cur)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def track_jobs(admin_conn):
    """Registers job ids for deletion at teardown.

    These tests commit real rows through the API, so they must clean up after
    themselves; `db_conn`'s rollback-everything approach cannot help here. The
    delete runs as platform admin so it can reach every tenant's rows.
    """
    created: list[str] = []

    def track(job_id):
        created.append(str(job_id))
        return job_id

    yield track

    if created:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("DELETE FROM evaluation_jobs WHERE job_id = ANY(%s::uuid[])", (created,))
        admin_conn.commit()


@pytest.fixture
def track_uploads(admin_conn):
    """Registers booklet_uploads ids for deletion at teardown.

    Uploads made through the API are committed, so — like jobs — they must be
    cleaned up for real rather than rolled back.
    """
    created: list[str] = []

    def track(upload_id):
        created.append(str(upload_id))
        return upload_id

    yield track

    if created:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("DELETE FROM booklet_uploads WHERE upload_id = ANY(%s::uuid[])",
                        (created,))
        admin_conn.commit()


@pytest.fixture
def make_job(admin_conn, track_jobs):
    """Factory: make_job(college_id=..., job_type=...) -> job dict, committed.

    Inserts directly via core/jobs.py rather than through the API so a test
    can create a job for a tenant whose credential it is not about to use —
    which is exactly what the cross-tenant 404 test needs.
    """
    def make(*, college_id=COLLEGE_A, job_type="booklet_evaluation", payload=None):
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            job = core.jobs.enqueue_job(
                cur,
                college_id=college_id,
                job_type=job_type,
                payload=payload or {"blob_url": "dummy-storage/booklets/test.pdf"},
            )
        admin_conn.commit()
        track_jobs(job["job_id"])
        return job

    return make


@pytest.fixture
def unique_job_type():
    """A job_type nobody else is using, so a claim test cannot accidentally
    pick up a real queued job (or another test's) and pass for the wrong
    reason. evaluation_jobs.job_type is free TEXT precisely so this is cheap.
    """
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def sample_booklet_bytes():
    """The real 4-page benchmark booklet, or a skip if it hasn't been
    generated (scripts/generate_booklet_benchmark.py)."""
    if not SAMPLE_BOOKLET.is_file():
        pytest.skip(
            f"{SAMPLE_BOOKLET} not present — run "
            f"scripts/generate_booklet_benchmark.py to create it."
        )
    return SAMPLE_BOOKLET.read_bytes()


# ─────────────────────── fixtures for evaluate / results ────────────────────

@pytest.fixture
def make_booklet(admin_conn):
    """Factory: builds a complete, self-contained booklet in the DB and
    returns the ids the API needs — {upload_id, exam_id, student_id,
    answer_id, question_id, blob_url, ...}. Everything it creates is deleted
    at teardown, in FK order.

    Self-contained rather than leaning on seed_minimal.sql because these tests
    assert on ledger row COUNTS for one answer, and a shared fixture answer
    that another test also scores would make those assertions depend on test
    ordering. The college, student and exam ARE reused from the seed — those
    are stable reference data, not per-test state.

    It creates the whole chain a real booklet has after ingestion:
    question -> reference_answer_variant -> answer -> answer_blocks, plus a
    booklet_uploads row whose blob_url matches answers.source_scan_url, which
    is exactly how core/booklet_evaluator.load_booklet_tasks finds the regions
    for one ingestion of one booklet.
    """
    created: list[tuple[str, str, str]] = []      # (table, pk column, id)

    def make(*, college_id=COLLEGE_A, student_id=None, marks_max=10.0,
             answer_text="A stack is LIFO and a queue is FIFO.",
             reference_text="A stack follows LIFO order; a queue follows FIFO order."):
        student_id = student_id or "cccccccc-0001-0001-0001-cccccccccccc"
        exam_id = "dddddddd-0001-0001-0001-dddddddddddd"
        blob_url = f"dummy-storage/booklets/{uuid.uuid4()}.pdf"

        question_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        answer_id = str(uuid.uuid4())
        block_id = str(uuid.uuid4())
        upload_id = str(uuid.uuid4())

        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            # Enum values taken from migration 001, not invented:
            # question_style is (long|short|one_word|mcq) and
            # question_source_type is (sentence|paragraph|diagram|table|
            # formula|manual). question_group_id is NOT NULL with no default.
            cur.execute(
                """
                INSERT INTO questions (question_id, status, source_type, style,
                                       marks_max, question_group_id, content)
                VALUES (%s, 'live', 'manual', 'short', %s, gen_random_uuid(), %s)
                """,
                (question_id, marks_max, "Explain the difference between a stack and a queue."),
            )
            created.append(("questions", "question_id", question_id))

            cur.execute(
                """
                INSERT INTO reference_answer_variants
                    (variant_id, question_id, variant_type, content, is_current)
                VALUES (%s, %s, 'short', %s, true)
                """,
                (variant_id, question_id, reference_text),
            )
            created.append(("reference_answer_variants", "variant_id", variant_id))

            cur.execute(
                """
                INSERT INTO answers (answer_id, question_id, student_id, exam_id,
                                     source_scan_url, text_extracted, status, college_id)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending_evaluation', %s)
                """,
                (answer_id, question_id, student_id, exam_id, blob_url,
                 answer_text, college_id),
            )
            created.append(("answers", "answer_id", answer_id))

            # A text block carrying already-digital content, the shape
            # core/booklet_evaluator.load_booklet_tasks wraps in PlainText —
            # so no OCR, no model, no storage fetch is needed to score it.
            cur.execute(
                """
                INSERT INTO answer_blocks (block_id, answer_id, block_type, content,
                                           sequence_order, page_number,
                                           classification_label,
                                           classification_confidence, needs_review)
                VALUES (%s, %s, 'text', %s, 0, 1, 'text', 0.95, false)
                """,
                (block_id, answer_id, answer_text),
            )
            created.append(("answer_blocks", "block_id", block_id))

            cur.execute(
                """
                INSERT INTO booklet_uploads (upload_id, college_id, blob_url, filename,
                                             content_type, size_bytes, storage_mode)
                VALUES (%s, %s, %s, 'booklet.pdf', 'application/pdf', 1024, 'dummy')
                """,
                (upload_id, college_id, blob_url),
            )
            created.append(("booklet_uploads", "upload_id", upload_id))

        admin_conn.commit()
        return {
            "upload_id": upload_id,
            "exam_id": exam_id,
            "student_id": student_id,
            "answer_id": answer_id,
            "question_id": question_id,
            "variant_id": variant_id,
            "block_id": block_id,
            "blob_url": blob_url,
            "college_id": college_id,
            "marks_max": marks_max,
        }

    yield make

    # Teardown in reverse FK order, plus the rows evaluation created.
    answer_ids = [i for table, _, i in created if table == "answers"]
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        if answer_ids:
            for table in ("evaluation_results", "answer_reviews", "answer_status_history"):
                cur.execute(f"DELETE FROM {table} WHERE answer_id = ANY(%s::uuid[])",
                            (answer_ids,))
        for table, pk, row_id in reversed(created):
            cur.execute(f"DELETE FROM {table} WHERE {pk} = %s", (row_id,))
    admin_conn.commit()


@pytest.fixture
def ledger_snapshot(admin_conn):
    """Factory: ledger_snapshot(answer_id) -> a comparable snapshot of every
    evaluation_results row for that answer.

    Returns the full row contents, not just a count. A count alone would pass
    if an override UPDATEd a score in place — which is exactly the violation
    the override tests exist to catch — so the snapshot carries score,
    explanation, is_current and evaluated_at too.
    """
    def snapshot(answer_id):
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute(
                """
                SELECT evaluation_id, score, explanation, evaluator_type,
                       evaluator_model, is_current, evaluated_at, metrics
                  FROM evaluation_results
                 WHERE answer_id = %s
                 ORDER BY evaluation_id
                """,
                (str(answer_id),),
            )
            return cur.fetchall()

    return snapshot


# ───────────────── fixtures for the question bank / papers ──────────────────
#
# These build rows in the SHARED question schema — questions, paper_patterns
# and everything hanging off them carry no college_id and are not under RLS
# (migration 003 lines 5 and 341, 006 line 15, 008 line 15). So unlike
# `make_booklet`, none of these factories takes a college: there is no tenant
# to give them. They still use `admin_conn` for the same reason the others do
# — the API commits its own rows in its own transaction, so tests must be able
# to see and then really delete them.

#: seed_minimal.sql's two reviewers. Both are platform-level (college_id NULL),
#: which migration 003 line 51 defines as "eligible to review the shared
#: question bank regardless of tenant" — the right kind of reviewer for gate
#: tests that are not about tenancy at all.
REVIEWER_TEACHER = "44444444-4444-4444-4444-444444444444"
REVIEWER_SME = "55555555-5555-5555-5555-555555555555"


@pytest.fixture
def track_questions(admin_conn):
    """Registers question ids for deletion at teardown, children first.

    A question is referenced from five directions once it has been through the
    gates (reviews, status history, topic links, paper assignments, and
    answers). Deleting in that order here means an individual test never has
    to think about it — and a test that forgets to clean up a review row would
    otherwise fail the NEXT run with an opaque FK violation.
    """
    created: list[str] = []

    def track(question_id):
        created.append(str(question_id))
        return question_id

    yield track

    if created:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("DELETE FROM question_reviews WHERE question_id = ANY(%s::uuid[])",
                        (created,))
            cur.execute(
                "DELETE FROM question_status_history WHERE question_id = ANY(%s::uuid[])",
                (created,))
            cur.execute(
                "DELETE FROM topic_links WHERE entity_type = 'question' "
                "AND entity_id = ANY(%s::uuid[])", (created,))
            cur.execute("DELETE FROM paper_questions WHERE question_id = ANY(%s::uuid[])",
                        (created,))
            cur.execute("DELETE FROM questions WHERE question_id = ANY(%s::uuid[])",
                        (created,))
        admin_conn.commit()


@pytest.fixture
def make_question(admin_conn, track_questions):
    """Factory: make_question(status=..., style=..., marks_max=...) -> id.

    Inserts directly rather than going through POST /questions/generate,
    because most tests here are about what happens to a question AFTER it
    exists, and routing every one of them through an LLM-stub generation call
    would make the gate tests depend on the generation endpoint's behaviour
    too.
    """
    def make(*, status="draft", style="long", marks_max=10.0, content=None,
             source_type="manual", is_ai_generated=True):
        question_id = str(uuid.uuid4())
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute(
                """
                INSERT INTO questions (question_id, status, source_type, style,
                                       marks_max, question_group_id, content,
                                       is_ai_generated, created_at)
                VALUES (%s, %s, %s, %s, %s, gen_random_uuid(), %s, %s, now())
                """,
                (question_id, status, source_type, style, marks_max,
                 content or f"Test question {question_id[:8]}?", is_ai_generated),
            )
        admin_conn.commit()
        track_questions(question_id)
        return question_id

    return make


@pytest.fixture
def make_paragraph(admin_conn):
    """Factory: make_paragraph(status='active') -> paragraph_id, committed.

    seed_minimal.sql seeds no paragraphs (it says so at line 11), so the
    generation endpoint has nothing to generate FROM without this.
    """
    created: list[str] = []

    def make(*, status="active", content=None):
        paragraph_id = str(uuid.uuid4())
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute(
                """
                INSERT INTO paragraphs (paragraph_id, content, source_document,
                                        version, status)
                VALUES (%s, %s, %s, 1, %s)
                """,
                (paragraph_id,
                 content or "A stack is a LIFO structure; a queue is FIFO.",
                 "tests/test_api fixture", status),
            )
        admin_conn.commit()
        created.append(paragraph_id)
        return paragraph_id

    yield make

    if created:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            # Generated questions point at the paragraph via source_id, but
            # that column has no FK (it is polymorphic over
            # question_source_type), so the questions do not block this delete
            # — track_questions removes them on its own teardown.
            cur.execute("DELETE FROM paragraphs WHERE paragraph_id = ANY(%s::uuid[])",
                        (created,))
        admin_conn.commit()


@pytest.fixture
def make_pattern(admin_conn):
    """Factory: builds a self-contained paper pattern and returns its id.

    `slots` is a list of (label, marks, style) — one mandatory section, one
    leaf slot each, which is the smallest shape that exercises real matching.

    Self-contained rather than reusing seed_minimal.sql's real DSCE pattern
    for the same reason `make_booklet` is: the seed pattern's slots are all
    (long, 10) and the shared bank already contains live (long, 10) questions,
    so a test using it could not tell "the generator matched MY question" from
    "the generator matched a seed question". A pattern with odd marks values
    and marks_tolerance=0 can.
    """
    created: list[str] = []

    def make(*, slots=(("Q1", 9.25, "long"),), is_active=True, is_mandatory=True,
             name=None):
        pattern_id = str(uuid.uuid4())
        section_id = str(uuid.uuid4())
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute(
                """
                INSERT INTO paper_patterns (pattern_id, name, description,
                                            total_marks, course_code, is_active)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (pattern_id, name or f"test pattern {pattern_id[:8]}",
                 "tests/test_api fixture", sum(s[1] for s in slots), "TEST101",
                 is_active),
            )
            cur.execute(
                """
                INSERT INTO pattern_sections (section_id, pattern_id, section_label,
                                              section_order, is_mandatory)
                VALUES (%s, %s, %s, 1, %s)
                """,
                (section_id, pattern_id, "Part A — test", is_mandatory),
            )
            for order, (label, marks, style) in enumerate(slots, start=1):
                cur.execute(
                    """
                    INSERT INTO pattern_slots (slot_id, section_id, slot_label,
                                               slot_order, marks, style, parent_slot_id)
                    VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, NULL)
                    """,
                    (section_id, label, order, marks, style),
                )
        admin_conn.commit()
        created.append(pattern_id)
        return pattern_id

    yield make

    if created:
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            # paper_sections/paper_questions cascade from generated_papers;
            # pattern_sections/pattern_slots cascade from paper_patterns. But
            # paper_sections REFERENCES pattern_sections ON DELETE RESTRICT, so
            # any generated paper has to go first.
            cur.execute("DELETE FROM generated_papers WHERE pattern_id = ANY(%s::uuid[])",
                        (created,))
            cur.execute("DELETE FROM paper_patterns WHERE pattern_id = ANY(%s::uuid[])",
                        (created,))
        admin_conn.commit()


@pytest.fixture
def question_status(admin_conn):
    """Reads a question's CURRENT status straight from the DB.

    The gate tests assert on this, not only on the HTTP status code. A 409
    response from an endpoint that had nevertheless promoted the row is the
    exact failure that matters, and it is invisible to a test that only reads
    the response.
    """
    def read(question_id):
        with admin_conn.cursor() as cur:
            set_admin_context(cur)
            cur.execute("SELECT status FROM questions WHERE question_id = %s",
                        (str(question_id),))
            row = cur.fetchone()
            return row[0] if row else None

    return read


# ──────────────────── the whoami probe (TEST-ONLY, not shipped) ─────────────
#
# api/routers/debug.py used to expose this as GET /api/v1/_debug/whoami,
# env-gated. It was deleted in the Day 5 hardening pass: an endpoint that
# reports the caller's identity and the server's session state should not be
# one mis-set environment variable away from being live, and this build is
# heading toward real authentication.
#
# The probe itself is still the only thing that can prove, from OUTSIDE the
# process, that the request → dependency → transaction chain actually
# establishes the RLS context — a unit test of api/deps/db.py cannot, because
# the interesting part is precisely the chain. So it lives here instead, and
# is mounted onto a test app. A fixture cannot be enabled in production.

WHOAMI_PATH = "/_test/whoami"


def mount_whoami_probe(app):
    """Adds the identity/RLS-context probe to an app under test.

    Deliberately mirrors what the deleted endpoint did: it reports the
    identity the API resolved, and — the part that matters — what Postgres
    thinks the tenant context is INSIDE this request's own transaction. Read
    with `current_setting(..., true)`, the missing_ok form, so an unset GUC
    comes back NULL instead of raising: a null here on a request that
    otherwise succeeded is the exact silent breakage api/deps/db.py exists to
    prevent.
    """
    from fastapi import Depends

    from api.deps.db import get_tenant_conn
    from api.deps.identity import CurrentUser, get_current_user

    @app.get(WHOAMI_PATH, include_in_schema=False)
    def _whoami(
        user: CurrentUser = Depends(get_current_user),
        conn=Depends(get_tenant_conn),
    ) -> dict:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT current_setting('app.current_college_id', true),
                       current_setting('app.is_platform_admin', true),
                       current_database(),
                       current_user
            """)
            college_id, is_admin, database, db_user = cur.fetchone()

        return {
            "identity": {
                "college_id": str(user.college_id),
                "is_platform_admin": user.is_platform_admin,
            },
            "db": {
                "current_college_id": college_id,
                "is_platform_admin": is_admin,
                "database": database,
                "user": db_user,
            },
            "rls_context_ok": college_id == str(user.college_id),
        }

    return app


@pytest.fixture
def whoami_app(_dotenv, api_settings):
    """A test app carrying the whoami probe at `WHOAMI_PATH`."""
    return mount_whoami_probe(create_app(api_settings))
