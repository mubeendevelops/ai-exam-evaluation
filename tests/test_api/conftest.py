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
import core.users
from api.deps.db import set_admin_context
from api.deps.identity import create_access_token
from api.main import create_app
from api.settings import Settings

#: seed_minimal.sql's demo college — the tenant that actually exists.
COLLEGE_A = "11111111-1111-1111-1111-111111111111"

#: seed_minimal.sql's SECOND, REAL college — "the other tenant" everywhere in
#: this package.
#:
#: It used to be a college id that existed nowhere, and that was a real
#: weakness, not a shortcut. Two things changed and both make an absent id the
#: wrong fixture:
#:
#:   1. api/deps/db.py::get_tenant_conn now verifies the college exists and is
#:      active, so a request from an absent college is refused at the edge with
#:      401. A cross-tenant test using one would assert "an unknown caller is
#:      refused" while claiming to assert "a real caller is isolated" — and
#:      would keep passing if isolation were removed entirely.
#:   2. Migration 016 made RLS load-bearing. Isolation between two REAL
#:      tenants, each owning rows, is now a thing the database can actually be
#:      asked to demonstrate.
#:
#: The `college_b` fixture guarantees this row (and B's own student/exam)
#: exists even against a database seeded before that change.
COLLEGE_B = "22222222-2222-2222-2222-222222222222"

#: College B's own student and exam, so B can own answers/uploads/jobs of its
#: own. `make_booklet(college_id=COLLEGE_B, **COLLEGE_B_OWNER)` builds B a
#: complete booklet; migration 003's trigger rejects a student/exam pair that
#: straddles two colleges, so these have to travel together.
COLLEGE_B_STUDENT = "cccccccc-0003-0003-0003-cccccccccccc"
COLLEGE_B_EXAM = "dddddddd-0002-0002-0002-dddddddddddd"
COLLEGE_B_OWNER = {"student_id": COLLEGE_B_STUDENT, "exam_id": COLLEGE_B_EXAM}

#: A college id that exists NOWHERE. Still needed — but now for what it
#: actually proves: a well-formed credential naming no tenant is a 401, not a
#: 500 on a foreign key and not a working context over an empty tenant. It is
#: never "the other tenant" in an isolation test.
COLLEGE_ABSENT = "9e0f0000-0000-4000-8000-00000000dead"

#: seed_minimal.sql's suspended college — a real row that must still be
#: refused, which is the half of _require_known_college() an absent id cannot
#: exercise.
COLLEGE_SUSPENDED = "33333333-3333-3333-3333-333333333333"

#: The colleges that really have a `colleges` row (seeded, or guaranteed by
#: the `college_b` / `suspended_college` fixtures). `make_client` creates a
#: real account only for these; anything else gets a token over synthetic ids,
#: because a users row for a nonexistent college cannot be inserted.
REAL_COLLEGES = frozenset({COLLEGE_A, COLLEGE_B, COLLEGE_SUSPENDED})

#: Smallest byte string that passes the endpoint's %PDF- signature check.
#: Used for most tests deliberately: they exercise upload PLUMBING (size caps,
#: storage refs, job rows), and nothing in that path parses the PDF, so a
#: 300 KB fixture would only make them slower. The one test that wants a real
#: booklet asks for `sample_booklet_bytes`.
MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"

#: seed_minimal.sql's paper pattern, its first (mandatory) section, and that
#: section's 'Q1' slot. `make_booklet` binds its question into this slot on a
#: generated paper of its own, so the booklet has a paper_id whose slot_label
#: 'Q1' really does resolve to that question — which is what
#: POST /api/v1/evaluate now requires and what ingestion resolves markers
#: against (there is no exams->generated_papers FK; §7C).
PATTERN_ID = "15b05a44-6b4d-54b1-be21-753c495d7314"
PATTERN_SECTION_PART_A = "b12ee7bf-57e1-5db7-9563-0a9d612d403c"
PATTERN_SLOT_Q1 = "f79d44d6-bb34-5070-8d90-fc6d2aecdee7"

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


#: The password every fixture account is created with. A constant, and a
#: comment about why that is fine: these accounts exist only in a developer's
#: local `ai_evaluation` database, which the seed script drops and rebuilds.
#: The one thing that matters is that it is not a plausible REAL password
#: someone might reuse — hence the sentence rather than a word.
TEST_PASSWORD = "tests-only password, not a secret anyone reuses"

#: Cache of the accounts these tests authenticate as, keyed by (college, role).
#:
#: Created once per database rather than once per test, and never deleted, for
#: the same reason `college_b` is not: they are reference data. Every review
#: and status-history row the API writes under a test points at the account's
#: reviewer_id by foreign key, so deleting the account at teardown would
#: either fail on those references or force every test to clean up in a
#: specific order. Two rows per college is not growth worth managing.
_ACCOUNTS: dict[tuple, dict] = {}


def _ensure_account(conn, college_id, role: str) -> dict:
    """Gets (or creates) the fixture account for one college and role.

    Every test client authenticates as a REAL `users` row with a REAL
    `reviewers` row behind it, rather than as a hand-signed token naming
    invented ids. That is not ceremony:

      * `reviewer_id` is a foreign key. Every override, review and promotion
        the API now attributes to the authenticated caller (RE-4) would fail
        on a made-up one, so a test signing an invented rid would be testing
        a code path production cannot reach.
      * migration 017 requires `users.college_id` to EQUAL the linked
        reviewer's college_id, and migration 003's
        fn_derive_and_check_answer_review_college() then uses that reviewer's
        college to accept or refuse a review. An account built any other way
        would quietly land on the permissive NULL branch of that check — the
        exact hole migration 017's header is about.
    """
    key = (str(college_id) if college_id else None, role)
    if key in _ACCOUNTS:
        return _ACCOUNTS[key]

    short = (str(college_id)[:8] if college_id else "platform")
    email = f"{role}.{short}@tests.example"

    with conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            "SELECT user_id, reviewer_id, email, role, college_id "
            "FROM users WHERE email = %s::citext", (email,))
        row = cur.fetchone()
        if row is None:
            created = core.users.create_user(
                cur,
                name=f"Test {role} {short}",
                email=email,
                password=TEST_PASSWORD,
                role=role,
                college_id=str(college_id) if college_id else None,
                # reviewers.role is a DIFFERENT enum (teacher/sme/admin) from
                # users.role (teacher/admin/platform_admin) — see migration
                # 017's header, reason 3. A platform_admin's reviewers row is
                # an 'admin'.
                reviewer_role="admin" if role != "teacher" else "teacher",
            )
            cur.execute(
                "SELECT user_id, reviewer_id, email, role, college_id "
                "FROM users WHERE user_id = %s", (str(created["user_id"]),))
            row = cur.fetchone()
    conn.commit()

    account = {
        "user_id": row[0], "reviewer_id": row[1], "email": str(row[2]),
        "role": row[3], "college_id": row[4], "password": TEST_PASSWORD,
    }
    _ACCOUNTS[key] = account
    return account


@pytest.fixture
def account(admin_conn):
    """Factory: account(college_id, role) -> the fixture account's row.

    Tests that need to assert WHO a review was attributed to read
    `account(COLLEGE_A, "teacher")["reviewer_id"]` — the same account
    `make_client(COLLEGE_A)` authenticates as.
    """
    def make(college_id=COLLEGE_A, role: str = "teacher") -> dict:
        return _ensure_account(admin_conn, college_id, role)

    return make


def _identity_for(conn, college_id, role: str) -> dict:
    """The claims a test client authenticates with.

    A REAL account for a college that really exists, and synthetic ids
    otherwise — `college_id` may name a college that does not exist, or a
    suspended one (COLLEGE_ABSENT, COLLEGE_SUSPENDED), and no account can be
    created for those because the foreign key would refuse it. Such a request
    is expected to be refused at `get_tenant_conn`, which is what those tests
    assert.
    """
    if college_id is None:
        return _ensure_account(conn, None, "platform_admin")
    if str(college_id) in REAL_COLLEGES:
        return _ensure_account(conn, college_id, role)
    return {
        "user_id": uuid.uuid4(), "reviewer_id": uuid.uuid4(),
        "email": f"nobody@{str(college_id)[:8]}.example",
        "role": role, "college_id": college_id,
    }


def _sign(identity: dict, settings: Settings) -> str:
    """Signs an access token for `identity` with the app's own settings.

    `create_access_token` is the same function POST /auth/login calls, so what
    these tests present is byte-for-byte what a real client presents. Signing
    directly rather than logging in keeps the whole suite from depending on
    the login endpoint (and from paying a bcrypt verification per client); the
    login FLOW is tested end to end where it belongs, in test_auth.py.
    """
    token, _ = create_access_token(
        user_id=identity["user_id"],
        reviewer_id=identity["reviewer_id"],
        email=identity["email"],
        role=identity["role"],
        college_id=identity["college_id"],
        settings=settings,
    )
    return token


@pytest.fixture
def sign_token(_dotenv, admin_conn):
    """Factory: sign_token(settings, college_id, role) -> a raw access token.

    Takes the SETTINGS explicitly, because the two tests that build a
    production app (to prove `stub` is refused there) must present a token
    signed with THAT app's key — `create_app` verifies against
    `app.state.settings`, and a token signed with the test app's key would be
    rejected by the production app for the right reason at the wrong moment,
    turning a 403 assertion into a confusing 401.
    """
    def make(settings, college_id=COLLEGE_A, *, role: str = "teacher") -> str:
        return _sign(_identity_for(admin_conn, college_id, role), settings)

    return make


@pytest.fixture
def bearer(_dotenv, api_settings, admin_conn):
    """Factory: bearer(college_id, role=...) -> an Authorization header dict.

    For tests that build their own httpx client (the ones probing header
    handling directly) rather than taking one from `make_client`.
    """
    def make(college_id=COLLEGE_A, *, role: str = "teacher") -> dict:
        identity = _identity_for(admin_conn, college_id, role)
        return {"Authorization": f"Bearer {_sign(identity, api_settings)}"}

    return make


@pytest.fixture
def make_client(_dotenv, api_settings, admin_conn):
    """Factory: make_client(college_id) -> httpx.AsyncClient carrying a REAL
    signed access token for that college.

    A factory rather than one client because the cross-tenant tests need two
    callers in the same test, and a shared client with mutated headers would
    make it far too easy to write a test that passes for the wrong reason.

    The client carries `.identity` — the account it authenticates as — so a
    test can assert that a review was attributed to the caller without
    re-deriving which account that was.
    """
    app = create_app(api_settings)

    def make(college_id=COLLEGE_A, *, role: str = "teacher",
             raise_app_exceptions: bool = True) -> httpx.AsyncClient:
        identity = _identity_for(admin_conn, college_id, role)

        # raise_app_exceptions=True (httpx's default) re-raises an unhandled
        # server exception into the test instead of returning the 500 the app's
        # handler produced — which is what you want almost always, so a broken
        # endpoint fails with its real traceback rather than an opaque 500.
        # The one test that asserts the 500 RESPONSE itself passes False.
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions),
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {_sign(identity, api_settings)}"},
        )
        client.identity = identity
        return client

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
def college_b(admin_conn):
    """Guarantees the SECOND REAL tenant exists, and returns its ids.

    seed_minimal.sql creates college B, its student and its exam, so on a
    freshly-seeded database this fixture finds everything already there. It
    inserts anyway (ON CONFLICT DO NOTHING) because a developer's database was
    very likely seeded before college B was added, and the alternative failure
    — every cross-tenant test 401ing with "no college with that id exists" —
    reads like a bug in api/deps/db.py rather than like stale seed data.

    Nothing is deleted at teardown. B is reference data of the same kind as
    seed_minimal.sql's college A, not per-test state: other rows (answers,
    jobs, uploads) hang off it by foreign key, and a per-test delete would
    race any test creating those.
    """
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            """
            INSERT INTO colleges (college_id, name, short_code, status)
            VALUES (%s, 'Second Demo College', 'demo2', 'active')
            ON CONFLICT (college_id) DO NOTHING
            """,
            (COLLEGE_B,),
        )
        cur.execute(
            """
            INSERT INTO students (student_id, name, roll_number, email, college_id)
            VALUES (%s, 'Chandra Nair', 'DEMO2024001', 'chandra@demo2.edu', %s)
            ON CONFLICT (student_id) DO NOTHING
            """,
            (COLLEGE_B_STUDENT, COLLEGE_B),
        )
        cur.execute(
            """
            INSERT INTO exams (exam_id, name, conducted_at, status, college_id)
            VALUES (%s, 'Second College CIA-1 Aug 2026', now(), 'live', %s)
            ON CONFLICT (exam_id) DO NOTHING
            """,
            (COLLEGE_B_EXAM, COLLEGE_B),
        )
    admin_conn.commit()
    return {"college_id": COLLEGE_B, **COLLEGE_B_OWNER}


@pytest.fixture
def suspended_college(admin_conn):
    """Guarantees seed_minimal.sql's suspended college exists. Same argument
    as `college_b`; it owns nothing, so it is only ever a credential."""
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            """
            INSERT INTO colleges (college_id, name, short_code, status)
            VALUES (%s, 'Suspended College', 'suspended', 'suspended')
            ON CONFLICT (college_id) DO NOTHING
            """,
            (COLLEGE_SUSPENDED,),
        )
    admin_conn.commit()
    return COLLEGE_SUSPENDED


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
    scans: list[str] = []                        # every booklet's source_scan_url

    def make(*, college_id=COLLEGE_A, student_id=None, exam_id=None, marks_max=10.0,
             answer_text="A stack is LIFO and a queue is FIFO.",
             reference_text="A stack follows LIFO order; a queue follows FIFO order.",
             ingested=True, blob_url=None):
        # student_id and exam_id travel together: migration 003's
        # trg_answers_derive_college REJECTS an answer whose student and exam
        # belong to different colleges, and derives college_id from the
        # student. So a booklet for college B needs BOTH of B's ids —
        # `make_booklet(college_id=COLLEGE_B, **COLLEGE_B_OWNER)`.
        student_id = student_id or "cccccccc-0001-0001-0001-cccccccccccc"
        exam_id = exam_id or "dddddddd-0001-0001-0001-dddddddddddd"
        blob_url = blob_url or f"dummy-storage/booklets/{uuid.uuid4()}.pdf"
        scans.append(blob_url)

        question_id = str(uuid.uuid4())
        variant_id = str(uuid.uuid4())
        answer_id = str(uuid.uuid4())
        block_id = str(uuid.uuid4())
        upload_id = str(uuid.uuid4())
        paper_id = str(uuid.uuid4())
        paper_section_id = str(uuid.uuid4())
        paper_question_id = str(uuid.uuid4())

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

            # ingested=False builds the state POST /upload leaves behind: a
            # stored booklet with NO answers and NO answer_blocks. That is the
            # case the booklet_ingest job exists to handle, and the one that
            # used to require running scripts/ingest_booklet.py by hand.
            if ingested:
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

            # A generated paper whose 'Q1' slot holds this booklet's question.
            # POST /api/v1/evaluate requires a paper_id (there is no FK from
            # exams to generated_papers — §7C), and ingestion resolves the
            # marker 'Q1' through exactly this join:
            #   paper_questions -> paper_sections -> pattern_slots.slot_label.
            # Built per booklet rather than seeded, because the trigger on
            # paper_questions refuses the same question in two slots of one
            # paper, so a shared fixture paper could hold only one test's
            # question.
            cur.execute(
                """
                INSERT INTO generated_papers (paper_id, pattern_id, name, status)
                VALUES (%s, %s, %s, 'draft')
                """,
                (paper_id, PATTERN_ID, f"Test paper {paper_id[:8]}"),
            )
            created.append(("generated_papers", "paper_id", paper_id))

            cur.execute(
                """
                INSERT INTO paper_sections (paper_section_id, paper_id, section_id,
                                            choose_count)
                VALUES (%s, %s, %s, 1)
                """,
                (paper_section_id, paper_id, PATTERN_SECTION_PART_A),
            )
            created.append(("paper_sections", "paper_section_id", paper_section_id))

            cur.execute(
                """
                INSERT INTO paper_questions (paper_question_id, paper_section_id,
                                             slot_id, question_id)
                VALUES (%s, %s, %s, %s)
                """,
                (paper_question_id, paper_section_id, PATTERN_SLOT_Q1, question_id),
            )
            created.append(("paper_questions", "paper_question_id", paper_question_id))

        admin_conn.commit()
        return {
            "upload_id": upload_id,
            "paper_id": paper_id,
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

    # Teardown in reverse FK order, plus every row the API/worker created.
    #
    # Answers are collected BY SCAN, not from `created`, because the ingest
    # job creates answers and answer_blocks this fixture never inserted — the
    # whole point of the ingested=False case. Nothing in the answer schema
    # cascades (migration 001's FKs are RESTRICT by default), so each child
    # table is emptied explicitly.
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        # BY QUESTION as well as by scan. Each booklet's question is created
        # fresh here and used by nothing else, so "answers to my questions"
        # catches the rows the INGEST JOB wrote — which hang off the
        # source_scan_url of an upload this fixture never saw, and would
        # otherwise survive teardown and block the question's deletion.
        question_ids = [i for table, _, i in created if table == "questions"]
        answer_ids = []
        if scans or question_ids:
            cur.execute(
                """
                SELECT answer_id FROM answers
                 WHERE source_scan_url = ANY(%s) OR question_id = ANY(%s::uuid[])
                """,
                (scans or [""], question_ids or []),
            )
            answer_ids = [str(row[0]) for row in cur.fetchall()]
        if answer_ids:
            for table in ("evaluation_results", "answer_reviews",
                          "answer_status_history", "answer_blocks"):
                cur.execute(f"DELETE FROM {table} WHERE answer_id = ANY(%s::uuid[])",
                            (answer_ids,))
            cur.execute("DELETE FROM answers WHERE answer_id = ANY(%s::uuid[])",
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
