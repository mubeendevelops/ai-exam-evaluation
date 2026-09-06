"""tests/test_api/test_jobs.py — GET /api/v1/jobs/{job_id} and the queue.

Two things are under test, and they fail in opposite directions:

  * TENANT ISOLATION — college B must never see college A's job. This is the
    running cross-tenant proof; extend it as each new endpoint appears rather
    than trusting that the pattern was followed.
  * CLAIM EXCLUSIVITY — two workers racing for one queued job must produce
    exactly one winner. A queue that hands the same booklet to two workers
    would write two evaluation_results rows for one answer, and
    record_evaluation() flips is_current, so the loser's score would silently
    become "the" score.
"""
from __future__ import annotations

import datetime as dt
import threading
import uuid

import pytest

import core.db
import core.jobs
from api.deps.db import set_admin_context, set_tenant_context
from tests.test_api.conftest import COLLEGE_A, COLLEGE_B

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


# ─────────────────────────────── GET /jobs (list) ────────────────────────────

async def test_jobs_list_is_isolated_by_tenant(make_client, make_job, college_b):
    """A's job list must not include B's jobs, and B's own list must include
    B's — the same "own row visible, other tenant's is not" pairing every
    isolation test in this package uses."""
    a_job = make_job(college_id=COLLEGE_A)
    b_job = make_job(college_id=college_b["college_id"])

    async with make_client(COLLEGE_A) as client:
        mine = await client.get("/api/v1/jobs")
    async with make_client(COLLEGE_B) as client:
        theirs = await client.get("/api/v1/jobs")

    assert mine.status_code == theirs.status_code == 200

    mine_ids = {j["job_id"] for j in mine.json()["items"]}
    theirs_ids = {j["job_id"] for j in theirs.json()["items"]}

    assert a_job["job_id"] in mine_ids
    assert a_job["job_id"] not in theirs_ids
    assert b_job["job_id"] in theirs_ids
    assert b_job["job_id"] not in mine_ids


async def test_jobs_list_filters_by_status_and_job_type(make_client, make_job,
                                                         unique_job_type):
    """A job_type nobody else uses makes this filter proof rather than a
    fluke of whatever else is queued."""
    matching = make_job(college_id=COLLEGE_A, job_type=unique_job_type)
    other_type = make_job(college_id=COLLEGE_A, job_type=f"{unique_job_type}_other")

    async with make_client(COLLEGE_A) as client:
        response = await client.get(
            "/api/v1/jobs", params={"job_type": unique_job_type, "status": "queued"}
        )

    assert response.status_code == 200, response.text
    ids = {j["job_id"] for j in response.json()["items"]}
    assert ids == {matching["job_id"]}
    assert other_type["job_id"] not in ids


async def test_jobs_list_filters_by_created_after(make_client, make_job, admin_conn,
                                                   unique_job_type, track_jobs):
    """`created_after` is EXCLUSIVE — a client polling with the newest id it
    already has must not see that same row again."""
    old = make_job(college_id=COLLEGE_A, job_type=unique_job_type)

    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute("SELECT created_at FROM evaluation_jobs WHERE job_id = %s",
                    (old["job_id"],))
        (cutoff,) = cur.fetchone()

    new_job_id = str(uuid.uuid4())
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        cur.execute(
            """
            INSERT INTO evaluation_jobs (job_id, college_id, job_type, status,
                                         payload, created_at)
            VALUES (%s, %s, %s, 'queued', '{}'::jsonb, %s)
            """,
            (new_job_id, COLLEGE_A, unique_job_type, cutoff + dt.timedelta(seconds=1)),
        )
    admin_conn.commit()
    track_jobs(new_job_id)

    async with make_client(COLLEGE_A) as client:
        response = await client.get(
            "/api/v1/jobs",
            params={"job_type": unique_job_type, "created_after": cutoff.isoformat()},
        )

    ids = {j["job_id"] for j in response.json()["items"]}
    assert ids == {new_job_id}
    assert old["job_id"] not in ids


async def test_jobs_list_limit_above_max_is_clamped_not_rejected(make_client, make_job):
    """A limit far above MAX_LIMIT (200) is CLAMPED, not answered with 422 —
    see api/deps/pagination.py."""
    make_job(college_id=COLLEGE_A)

    async with make_client(COLLEGE_A) as client:
        response = await client.get("/api/v1/jobs", params={"limit": 100_000})

    assert response.status_code == 200, response.text
    assert response.json()["limit"] == 200


async def test_jobs_list_orders_stably_under_tied_timestamps(
    make_client, admin_conn, track_jobs, unique_job_type
):
    """Two jobs with the IDENTICAL created_at must still page without
    skipping or repeating a row — the job_id tiebreak in core/jobs.py::
    list_jobs is what LIMIT/OFFSET paging needs to be safe under ties.
    """
    job_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    with admin_conn.cursor() as cur:
        set_admin_context(cur)
        for job_id in job_ids:
            cur.execute(
                """
                INSERT INTO evaluation_jobs (job_id, college_id, job_type, status,
                                             payload, created_at)
                VALUES (%s, %s, %s, 'queued', '{}'::jsonb, '2026-01-01T00:00:00Z')
                """,
                (job_id, COLLEGE_A, unique_job_type),
            )
    admin_conn.commit()
    for job_id in job_ids:
        track_jobs(job_id)

    async with make_client(COLLEGE_A) as client:
        page1 = await client.get(
            "/api/v1/jobs", params={"job_type": unique_job_type, "limit": 1, "offset": 0}
        )
        page2 = await client.get(
            "/api/v1/jobs", params={"job_type": unique_job_type, "limit": 1, "offset": 1}
        )

    seen = {page1.json()["items"][0]["job_id"], page2.json()["items"][0]["job_id"]}
    assert seen == set(job_ids), (
        "tied created_at values caused a row to repeat or be skipped across pages"
    )


# ───────────────────────────── tenant isolation ─────────────────────────────

async def test_job_from_another_college_is_404_not_the_job(make_client, make_job,
                                                           college_b):
    """TODAY'S CROSS-TENANT PROOF.

    A job is created under college A. College B — a REAL, active college with
    jobs of its own — asks for it by its exact id and must get 404: not the
    job, and not a 403.

    403 would be a leak: it distinguishes "exists but not yours" from "does
    not exist", which lets a caller enumerate other colleges' job ids and
    count their activity. The assertion that B's response is byte-identical to
    the response for a completely made-up id is the part that pins this down.

    B FETCHES ITS OWN JOB TOO, and that half is not decoration. Until college
    B was a real tenant this test used an id that existed nowhere, so a 404
    proved only "an unknown caller gets nothing" — it would have passed
    against an API that refused every request B ever made (which, since
    get_tenant_conn started verifying the college, is exactly what happens to
    an absent id: 401). Showing that the SAME credential succeeds on B's own
    job is what makes the 404 evidence of isolation rather than of rejection.
    """
    job = make_job(college_id=COLLEGE_A)
    job_id = job["job_id"]
    b_job_id = make_job(college_id=college_b["college_id"])["job_id"]

    async with make_client(COLLEGE_A) as owner:
        mine = await owner.get(f"/api/v1/jobs/{job_id}")
    async with make_client(COLLEGE_B) as stranger:
        theirs = await stranger.get(f"/api/v1/jobs/{job_id}")
        made_up = await stranger.get(f"/api/v1/jobs/{uuid.uuid4()}")
        own = await stranger.get(f"/api/v1/jobs/{b_job_id}")

    # A owns it and sees it.
    assert mine.status_code == 200, mine.text
    assert mine.json()["job_id"] == job_id

    # B's credential works — it is a real tenant, not a rejected one.
    assert own.status_code == 200, own.text
    assert own.json()["job_id"] == b_job_id

    # B does not own A's job, and cannot tell it apart from a nonexistent id.
    assert theirs.status_code == 404, theirs.text
    assert made_up.status_code == 404
    assert theirs.json()["detail"].replace(job_id, "X") == \
        made_up.json()["detail"].replace(made_up.request.url.path.rsplit("/", 1)[-1], "X")

    # And nothing about the real job leaked into the 404 body.
    body = theirs.text
    assert job["payload"]["blob_url"] not in body
    assert "booklet_evaluation" not in body


async def test_isolation_holds_at_the_query_layer_not_only_via_rls(make_job,
                                                                   college_b):
    """core/jobs.py::get_job() filters on college_id itself.

    Migration 016 gave the deployment a NOBYPASSRLS application role, so the
    policies now run too — but only for a role that does not bypass them, and
    "PGUSER is a superuser again" is one .env edit away (it was the default
    until 016). The predicate is the layer that holds regardless of role, so
    it is tested regardless of role: this drives the query directly under a
    DELIBERATELY WRONG tenant context and asserts the explicit predicate — not
    the policy — is what excludes the row.

    If someone ever "simplifies" get_job() by deleting the college_id
    predicate on the grounds that RLS covers it, this test fails immediately
    instead of the isolation quietly disappearing on every superuser
    deployment.
    """
    job = make_job(college_id=COLLEGE_A)

    conn = core.db.get_connection()
    try:
        with conn.cursor() as cur:
            # PLATFORM-ADMIN context, deliberately: it activates migration
            # 014's permissive bypass policy, so RLS is not filtering anything
            # here and the row IS reachable on this connection. Whatever
            # refuses the next line can only be the predicate.
            set_admin_context(cur)
            as_b = core.jobs.get_job(cur, job_id=job["job_id"], college_id=COLLEGE_B)
            # Same connection, same context, asking as the true owner — this
            # is what proves the line above failed for the right reason, and
            # not because the row was unreachable to begin with.
            as_a = core.jobs.get_job(cur, job_id=job["job_id"], college_id=COLLEGE_A)

            # And once more under a WRONG tenant context, where RLS and the
            # predicate agree. Under the pre-016 superuser role this was the
            # only leg that ran at all; it is kept because "PGUSER is a
            # superuser again" is one .env edit away.
            set_tenant_context(cur, COLLEGE_B)
            as_b_scoped = core.jobs.get_job(cur, job_id=job["job_id"],
                                            college_id=COLLEGE_B)
    finally:
        conn.rollback()
        conn.close()

    assert as_b is None, (
        "get_job returned another college's job on a connection where RLS was "
        "bypassed — its college_id predicate is not filtering"
    )
    assert as_b_scoped is None
    assert as_a is not None and as_a["job_id"] == job["job_id"]


async def test_unknown_job_is_404_and_malformed_id_is_422(make_client):
    """A non-uuid path segment is rejected by FastAPI before any SQL runs —
    otherwise it would raise inside Postgres's ::uuid cast and surface as a
    500."""
    async with make_client(COLLEGE_A) as ac:
        missing = await ac.get(f"/api/v1/jobs/{uuid.uuid4()}")
        malformed = await ac.get("/api/v1/jobs/not-a-uuid")

    assert missing.status_code == 404, missing.text
    assert malformed.status_code == 422, malformed.text


async def test_job_endpoint_requires_credentials(make_client, make_job):
    job = make_job(college_id=COLLEGE_A)

    async with make_client(COLLEGE_A) as ac:
        response = await ac.get(
            f"/api/v1/jobs/{job['job_id']}", headers={"Authorization": ""}
        )

    assert response.status_code == 401, response.text


# ───────────────────────────── claim exclusivity ────────────────────────────

def _admin_conn():
    conn = core.db.get_connection()
    with conn.cursor() as cur:
        set_admin_context(cur)
    return conn


async def test_two_workers_racing_one_job_produce_exactly_one_winner(
    make_job, unique_job_type
):
    """Two open transactions both try to claim the same single queued row.

    The first takes a row lock via FOR UPDATE and flips it to 'running'. The
    second's subquery hits that lock and, because of SKIP LOCKED, SKIPS the
    row rather than blocking on it — so it comes back empty instead of waiting
    for A and then claiming an already-running job.

    Neither transaction is committed before both have tried, which is the
    whole point: if the claim were a read-then-write pair, or lacked FOR
    UPDATE, both would see 'queued' in this window and both would win.

    `unique_job_type` keeps this test from accidentally claiming a real queued
    booklet (or another test's job) and passing for the wrong reason.
    """
    job = make_job(college_id=COLLEGE_A, job_type=unique_job_type)

    first, second = _admin_conn(), _admin_conn()
    try:
        with first.cursor() as cur_a:
            claimed_a = core.jobs.claim_next_job(cur_a, job_types=[unique_job_type])
        # A has NOT committed — its row lock is still held right here.
        with second.cursor() as cur_b:
            claimed_b = core.jobs.claim_next_job(cur_b, job_types=[unique_job_type])

        winners = [c for c in (claimed_a, claimed_b) if c is not None]
        assert len(winners) == 1, (
            f"expected exactly one claimer, got {len(winners)}. If both won, "
            f"SKIP LOCKED / FOR UPDATE is not doing its job and the same "
            f"booklet would be evaluated twice."
        )
        winner = winners[0]
        assert winner["job_id"] == job["job_id"]
        assert winner["status"] == "running"
        assert winner["attempts"] == 1, "attempts is incremented on the claim"
        assert winner["started_at"] is not None

        first.commit()
        second.commit()
    finally:
        first.close()
        second.close()


async def test_concurrent_threaded_claims_never_double_assign(make_job, unique_job_type):
    """The same guarantee under real thread concurrency over N jobs.

    Three jobs, six threads, each thread claiming in its own connection and
    committing. Every job must be claimed exactly once and no thread may see a
    job another thread already holds — the property that actually matters when
    several worker processes run.

    The single-row test above is the deterministic proof; this one is the
    non-deterministic corroboration, which is why both are here.
    """
    jobs = [make_job(college_id=COLLEGE_A, job_type=unique_job_type) for _ in range(3)]
    expected = {j["job_id"] for j in jobs}

    claimed: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(6)

    def worker():
        conn = _admin_conn()
        try:
            barrier.wait(timeout=10)          # maximize the overlap
            with conn.cursor() as cur:
                job = core.jobs.claim_next_job(cur, job_types=[unique_job_type])
            conn.commit()
            if job is not None:
                with lock:
                    claimed.append(job["job_id"])
        finally:
            conn.close()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(claimed) == len(set(claimed)), f"a job was claimed twice: {claimed}"
    assert set(claimed) == expected, (
        f"expected all 3 jobs claimed exactly once, got {sorted(claimed)}"
    )


async def test_claim_skips_job_types_it_does_not_handle(make_job, unique_job_type):
    """A worker must leave other workers' job types alone.

    A shared queue where one worker claims everything and fails what it cannot
    run would starve every other worker — and, worse, mark their work 'failed'.
    """
    other_type = f"{unique_job_type}_other"
    make_job(college_id=COLLEGE_A, job_type=other_type)

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            assert core.jobs.claim_next_job(cur, job_types=[unique_job_type]) is None
            assert core.jobs.claim_next_job(cur, job_types=[other_type]) is not None
        conn.commit()
    finally:
        conn.close()


# ───────────────────── end-to-end: upload -> worker -> poll ─────────────────

async def test_worker_reports_real_progress_and_reaches_succeeded(
    make_client, make_booklet, track_jobs
):
    """A booklet_eval job driven through the worker reports STAGES, not just
    the four statuses.

    Progress is what migration 015 added the column for: 'running' with no
    further signal for four minutes is indistinguishable from 'hung'. This
    asserts the stage/counts the worker actually wrote, and that a terminal
    job stops reporting its last mid-run snapshot — a job that failed during
    'persisting' must not keep saying 85%.
    """
    worker = _load_worker()
    booklet = make_booklet()

    async with make_client(COLLEGE_A) as ac:
        queued = await ac.post("/api/v1/evaluate", json={
            "upload_id": booklet["upload_id"],
            "exam_id": booklet["exam_id"],
            "student_id": booklet["student_id"],
            "paper_id": booklet["paper_id"],
            "stub": True,
        })
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]
        track_jobs(job_id)

        before = await ac.get(f"/api/v1/jobs/{job_id}")
        assert before.json()["progress"]["stage"] == "queued"

        processed = worker.process_one(
            stub=True, stub_seconds=0.0, dry_run=False,
            # Named, not indexed: HANDLED_JOB_TYPES now lists the ingest
            # type first, and a worker told to claim "the first type" would
            # quietly stop testing what this test is about.
            job_types=["booklet_eval"],
        )
        assert processed is not None and processed["job_id"] == job_id

        after = await ac.get(f"/api/v1/jobs/{job_id}")

    body = after.json()
    assert body["status"] == "succeeded", body
    assert body["progress"]["stage"] == "done"
    assert body["progress"]["percent"] == 1.0

    # The worker really wrote progress rows during the run — the stored value
    # is the last one ('done', with counts), even though the response above
    # deliberately reports it from the terminal status instead.
    stored = _stored_progress(job_id)
    assert stored["stage"] == "done"
    assert stored["counts"]["regions"] >= 1
    assert stored["counts"]["questions"] >= 1


def _stored_progress(job_id) -> dict:
    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT progress FROM evaluation_jobs WHERE job_id = %s",
                        (str(job_id),))
            return cur.fetchone()[0]
    finally:
        conn.close()


async def test_worker_records_failures_on_the_job(make_job, unique_job_type):
    """A job whose execution raises must end 'failed' WITH a reason.

    The alternative — a job that stays 'running' forever, with the traceback
    only in the worker's stderr — is invisible to the client polling
    /jobs/{id}, which is the only surface they have.

    A job_type the worker has no handler for is the cleanest way to force the
    failure path, and it doubles as a check that dispatch and the claim filter
    cannot drift apart silently: reaching the dispatch with an unhandled type
    raises rather than being ignored.
    """
    worker = _load_worker()
    job = make_job(college_id=COLLEGE_A, job_type=unique_job_type)

    processed = worker.process_one(
        stub=False, stub_seconds=0.0, dry_run=False, job_types=[unique_job_type]
    )
    assert processed is not None and processed["job_id"] == job["job_id"]

    conn = _admin_conn()
    try:
        with conn.cursor() as cur:
            stored = core.jobs.get_job(cur, job_id=job["job_id"], college_id=COLLEGE_A)
    finally:
        conn.close()

    assert stored["status"] == "failed"
    assert stored["error"] and "No handler for job_type" in stored["error"]
    assert stored["finished_at"] is not None
    assert stored["attempts"] == 1


def _load_worker():
    """Imports scripts/run_job_worker.py as a module.

    scripts/ is a directory of CLI entry points, not a package, so it is
    loaded by path — the same thing the shell does when running it. Importing
    the real module rather than reimplementing its loop is the point: a test
    against a copy of the worker proves nothing about the worker.
    """
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "run_job_worker.py"
    spec = importlib.util.spec_from_file_location("run_job_worker", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
