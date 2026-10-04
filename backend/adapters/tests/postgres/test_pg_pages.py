# mypy: disable-error-code="no-untyped-call"
# (PyMuPDF ships no type information for its document API.)
"""Pages and booklets on PostgreSQL, and the worker's job runner end to end: real PostgreSQL
(row-level security, the queue), the real PDF splitter and OpenCV cleaner on generated pages,
in-memory blobs."""

from dataclasses import replace
from uuid import UUID

import cv2
import numpy as np
import pymupdf
import pytest

from tarn_adapters.imaging import testing as synth
from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
from tarn_adapters.imaging.pdf import PyMuPdfSplitter
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.jobs import JobSettings
from tarn_adapters.postgres.testing import Opener, TestDatabase, World, new_college
from tarn_adapters.runtime import SystemClock, UuidGenerator
from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import (
    Booklet,
    BookletStatus,
    PageMetrics,
    RetakeReason,
    SourceFile,
)
from tarn_core.domain.common import BlobKey
from tarn_core.errors import InvariantError
from tarn_core.ids import PageId
from tarn_core.ports.pages import CleanedPage
from tarn_core.services.pipeline import PagePipeline, QualityPolicy
from tarn_core.services.uploads import UploadService
from tarn_core.testing import FakePageCleaner, MemoryBlobStore
from tarn_core.testing.builders import ci_shaped_blueprint, make_services
from tarn_worker.booklets import BookletJobRunner

pytestmark = pytest.mark.integration

METRICS = PageMetrics(
    sharpness=312.5,
    glare_share=0.0123,
    page_found=True,
    page_area_share=0.874,
    source_width=3000,
    source_height=4000,
    rotation_degrees=270,
    rotation_guessed=True,
    skew_degrees=-1.3,
    cropped=True,
    perspective_corrected=False,
    neighbour_removed=True,
)


# --- storage ----------------------------------------------------------------------------------


def test_booklet_sources_failure_and_page_quality_round_trip(session: Opener, world: World) -> None:
    cid = world.a.college.id
    booklet = world.a.booklet
    with session(cid) as s:
        stored = s.booklets.get(cid, booklet.id)
        source = SourceFile(
            key=BlobKey(f"college/{cid}/booklet/{booklet.id}/source/001.pdf"),
            media_type="application/pdf",
            size_bytes=12345,
        )
        s.booklets.save(cid, replace(stored, sources=(source,), version=stored.version + 1))
        page = s.booklets.pages(cid, booklet.id)[0]
        original = BlobKey(f"college/{cid}/booklet/{booklet.id}/original/001.jpg")
        s.booklets.save_page(
            cid,
            replace(
                page,
                original=original,
                cleaned=True,
                metrics=METRICS,
                retake_reasons=(RetakeReason.BLURRY, RetakeReason.GLARE),
                use_anyway=True,
            ),
        )
    with session(cid) as s:
        got = s.booklets.get(cid, booklet.id)
        assert got.sources == (source,) and got.failure_reason is None
        loaded = s.booklets.pages(cid, booklet.id)[0]
        assert loaded.metrics == METRICS  # floats, ints and booleans survive the jsonb
        assert loaded.original == original and loaded.use_anyway and loaded.cleaned
        assert loaded.retake_reasons == (RetakeReason.BLURRY, RetakeReason.GLARE)
        s.booklets.save(
            cid,
            replace(got, status=BookletStatus.FAILED, failure_reason="unreadable_file"),
        )
    with session(cid) as s:
        failed = s.booklets.get(cid, booklet.id)
        assert failed.status is BookletStatus.FAILED and failed.failure_reason == "unreadable_file"


def test_the_database_enforces_the_page_and_failure_rules(
    session: Opener, world: World, test_database: TestDatabase
) -> None:
    cid = world.a.college.id
    booklet = world.a.booklet
    with test_database.owner() as conn:
        with pytest.raises(Exception, match="booklet_failure_reason"):
            conn.execute("UPDATE booklets SET status = 'failed' WHERE id = %s", (booklet.id,))
        with pytest.raises(Exception, match="page_use_anyway_needs_flag"):
            conn.execute("UPDATE pages SET use_anyway = true WHERE booklet_id = %s", (booklet.id,))
    with session(cid) as s:
        page = s.booklets.pages(cid, booklet.id)[0]
        clash = replace(page, id=PageId(UUID(int=99)))  # same booklet, same page index
        with pytest.raises(InvariantError):
            s.booklets.save_page(cid, clash)


def test_waiting_booklets_are_counted_per_user_and_status(session: Opener, world: World) -> None:
    cid = world.a.college.id
    teacher, admin = world.a.college.teacher.id, world.a.college.admin.id
    booklet = world.a.booklet
    with session(cid) as s:
        base = s.booklets.get(cid, booklet.id)
        assert base.uploaded_by == teacher
        assert s.booklets.count_waiting(cid, teacher) == 1  # UPLOADED counts
        s.booklets.save(cid, replace(base, status=BookletStatus.PROCESSING))
        assert s.booklets.count_waiting(cid, teacher) == 1
        for status in (BookletStatus.PAGES_READY, BookletStatus.READING):  # waiting for OCR
            s.booklets.save(cid, replace(base, status=status))
            assert s.booklets.count_waiting(cid, teacher) == 1
        for status in (BookletStatus.NEEDS_RETAKE, BookletStatus.TEXT_READY, BookletStatus.SCORED):
            s.booklets.save(cid, replace(base, status=status))
            assert s.booklets.count_waiting(cid, teacher) == 0
        assert s.booklets.count_waiting(cid, admin) == 0
    with session(world.b.college.id) as s:  # B's identical booklet is B's
        assert s.booklets.count_waiting(world.b.college.id, world.a.college.teacher.id) == 0


# --- the worker's runner on PostgreSQL --------------------------------------------------------


def _pdf(pages: list[np.ndarray]) -> bytes:
    document = pymupdf.open()
    for image in pages:
        page = document.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=synth.jpeg(image))
    data: bytes = document.tobytes()
    return data


class Env:
    """A fresh database with one college, a blueprint, students, and the worker's runner."""

    def __init__(self, test_db: TestDatabase, db: PostgresDatabase, cleaner: object = None) -> None:
        self.test_db, self.db = test_db, db
        self.blobs = MemoryBlobStore()
        self.open = Opener(db, ids=UuidGenerator(), blobs=self.blobs)
        self.college = new_college(self.open, "A")
        with self.open(self.college.id) as s:
            self.blueprint = ci_shaped_blueprint(s, self.college)
        self.cleaner = cleaner or OpenCvPageCleaner()
        self.runner = BookletJobRunner(
            db=db,
            blobs=self.blobs,
            splitter=PyMuPdfSplitter(),
            cleaner=self.cleaner,  # type: ignore[arg-type]
            clock=SystemClock(),
            ids=UuidGenerator(),
            policy=QualityPolicy(),
            max_pages=10,
            worker="test-worker",
        )

    def upload(self, *files: bytes, student: int = 0) -> Booklet:
        with self.open(self.college.id) as s:
            svc = make_services(s)
            uploads = UploadService(
                booklets=svc.booklets,
                repository=s.booklets,
                blobs=s.blobs,
                jobs=s.jobs,
                runtime=s.runtime,
            )
            return uploads.upload(
                self.college.id,
                self.college.teacher.id,
                student_id=self.college.students[student].id,
                blueprint_id=self.blueprint.id,
                files=list(files),
            ).booklet

    def booklet(self, booklet: Booklet) -> Booklet:
        with self.open(self.college.id) as s:
            return s.booklets.get(self.college.id, booklet.id)

    def pages(self, booklet: Booklet) -> list:  # type: ignore[type-arg]
        with self.open(self.college.id) as s:
            return list(s.booklets.pages(self.college.id, booklet.id))

    def job(self) -> tuple[str, int]:
        with self.test_db.owner() as conn:
            row = conn.execute(
                "SELECT status, attempts FROM jobs WHERE kind = 'booklet.prepare'"
            ).fetchone()
        assert row is not None
        return str(row[0]), int(str(row[1]))

    def audit(self, action: AuditAction) -> list[tuple[object, ...]]:
        with self.test_db.owner() as conn:
            return conn.execute(
                "SELECT actor_id, after::text FROM audit_events WHERE action = %s", (action.value,)
            ).fetchall()


@pytest.fixture
def env(fresh: tuple[TestDatabase, PostgresDatabase]) -> Env:
    return Env(*fresh)


def test_the_runner_processes_an_uploaded_pdf_to_a_gated_booklet(env: Env) -> None:
    page = synth.ruled_page(900, 1200, seed=1, lines=14)
    blurred = cv2.GaussianBlur(synth.photograph(page, size=(900, 1200), margin=0.03), (0, 0), 6)
    booklet = env.upload(_pdf([synth.photograph(page, size=(900, 1200), margin=0.03), blurred]))
    assert env.booklet(booklet).status is BookletStatus.UPLOADED
    assert env.runner.run_one() is True
    assert env.job() == ("succeeded", 1)
    done = env.booklet(booklet)
    assert done.status is BookletStatus.NEEDS_RETAKE
    pages = env.pages(booklet)
    assert [p.cleaned for p in pages] == [True, True]
    assert pages[0].retake_reasons == () and pages[1].retake_reasons == (RetakeReason.BLURRY,)
    assert pages[0].metrics is not None and pages[0].metrics.page_found
    assert env.blobs.get(pages[0].image)[:3] == b"\xff\xd8\xff"  # a real JPEG
    assert pages[0].original != pages[0].image
    ((actor, after),) = env.audit(AuditAction.BOOKLET_PROCESSED)
    assert actor is None and '"needs_retake"' in str(after) and '"flagged_pages": [2]' in str(after)
    assert env.runner.run_one() is False  # nothing left


def test_the_runner_marks_an_unreadable_upload_failed_without_retrying(env: Env) -> None:
    booklet = env.upload(b"%PDF-1.4 this is not a pdf")
    assert env.runner.run_one() is True
    failed = env.booklet(booklet)
    assert failed.status is BookletStatus.FAILED and failed.failure_reason == "unreadable_file"
    assert env.job() == ("succeeded", 1)  # the job did its work: it reported the failure
    assert len(env.audit(AuditAction.BOOKLET_FAILED)) == 1


class _FailingCleaner(FakePageCleaner):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def clean(self, data: bytes) -> CleanedPage:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("storage unavailable")
        return OpenCvPageCleaner().clean(data)


def test_a_failing_step_is_retried_and_resumes(
    fresh: tuple[TestDatabase, PostgresDatabase],
) -> None:
    test_db, _ = fresh
    db = PostgresDatabase(
        test_db.app_url,
        job_settings=JobSettings(max_attempts=3, backoff_seconds=0, lease_seconds=60),
    )
    env = Env(test_db, db, cleaner=_FailingCleaner(1))
    booklet = env.upload(
        _pdf([synth.scan(synth.ruled_page(900, 1200, lines=10), size=(900, 1200))] * 2)
    )
    assert env.runner.run_one() is True  # split done, first page's cleaning failed
    assert env.job() == ("queued", 1)
    assert env.booklet(booklet).status is BookletStatus.PROCESSING
    assert [p.cleaned for p in env.pages(booklet)] == [False, False]  # the split survived
    assert env.runner.run_one() is True  # the retry resumes after the split
    assert env.job() == ("succeeded", 2)
    assert env.booklet(booklet).status in (BookletStatus.PAGES_READY, BookletStatus.NEEDS_RETAKE)
    assert all(p.cleaned for p in env.pages(booklet))
    db.dispose()


def test_a_job_out_of_attempts_fails_the_booklet(
    fresh: tuple[TestDatabase, PostgresDatabase],
) -> None:
    test_db, _ = fresh
    db = PostgresDatabase(
        test_db.app_url,
        job_settings=JobSettings(max_attempts=2, backoff_seconds=0, lease_seconds=60),
    )
    env = Env(test_db, db, cleaner=_FailingCleaner(99))
    booklet = env.upload(
        _pdf([synth.scan(synth.ruled_page(900, 1200, lines=10), size=(900, 1200))])
    )
    assert env.runner.run_one() and env.runner.run_one()
    assert env.job() == ("failed", 2)
    failed = env.booklet(booklet)
    assert failed.status is BookletStatus.FAILED and failed.failure_reason == "processing_failed"
    assert env.runner.run_one() is False
    db.dispose()


def test_a_crashed_worker_is_replaced_and_continues_from_the_last_finished_step(env: Env) -> None:
    images = [
        synth.scan(synth.ruled_page(900, 1200, seed=n, lines=10), size=(900, 1200))
        for n in (1, 2, 3)
    ]
    booklet = env.upload(_pdf(images))
    # A worker claims the job, finishes the split and one page, and dies.
    with env.db.scheduler() as queue:
        job = queue.claim("doomed")
    assert job is not None
    counting = FakePageCleaner()
    with env.open(env.college.id) as s:
        crashed = PagePipeline(
            booklets=s.booklets,
            blobs=env.blobs,
            splitter=PyMuPdfSplitter(),
            cleaner=OpenCvPageCleaner(),
            runtime=s.runtime,
            jobs=s.jobs,
        )
        crashed.step(env.college.id, booklet.id)
    with env.open(env.college.id) as s:
        PagePipeline(
            booklets=s.booklets,
            blobs=env.blobs,
            splitter=PyMuPdfSplitter(),
            cleaner=OpenCvPageCleaner(),
            runtime=s.runtime,
            jobs=s.jobs,
        ).step(env.college.id, booklet.id)
    assert [p.cleaned for p in env.pages(booklet)] == [True, False, False]
    assert env.runner.run_one() is False  # its lease is still alive: nobody else may take it
    with env.test_db.owner() as conn:
        conn.execute("UPDATE jobs SET locked_until = now() - interval '1 second'")
    env.runner.cleaner = counting
    assert env.runner.run_one() is True
    assert len(counting.calls) == 2  # only the two pages left were cleaned again
    assert env.job() == ("succeeded", 2)
    assert env.booklet(booklet).status is BookletStatus.PAGES_READY
    assert len(env.audit(AuditAction.BOOKLET_PROCESSED)) == 1


def test_a_job_whose_worker_died_on_its_last_attempt_fails_its_booklet(
    fresh: tuple[TestDatabase, PostgresDatabase],
) -> None:
    test_db, _ = fresh
    db = PostgresDatabase(
        test_db.app_url,
        job_settings=JobSettings(max_attempts=1, backoff_seconds=0, lease_seconds=60),
    )
    env = Env(test_db, db)
    booklet = env.upload(
        _pdf([synth.scan(synth.ruled_page(900, 1200, lines=10), size=(900, 1200))])
    )
    with db.scheduler() as queue:
        assert queue.claim("doomed") is not None
    with env.test_db.owner() as conn:
        conn.execute("UPDATE jobs SET locked_until = now() - interval '1 second'")
    assert env.runner.run_one() is True  # reaped: the booklet no longer waits forever
    assert env.job() == ("failed", 1)
    assert env.booklet(booklet).failure_reason == "processing_failed"
    db.dispose()


def test_a_booklet_deleted_while_queued_just_finishes_its_job(env: Env) -> None:
    booklet = env.upload(
        _pdf([synth.scan(synth.ruled_page(900, 1200, lines=10), size=(900, 1200))])
    )
    with env.open(env.college.id) as s:
        make_services(s).booklets.delete(env.college.id, env.college.teacher.id, booklet.id)
    assert env.runner.run_one() is True
    assert env.job() == ("succeeded", 1)
    assert env.blobs.keys == ()


def test_each_college_is_processed_in_its_own_transaction_scope(env: Env) -> None:
    other = new_college(env.open, "B")
    with env.open(other.id) as s:
        other_blueprint = ci_shaped_blueprint(s, other)
    page = synth.scan(synth.ruled_page(900, 1200, lines=10), size=(900, 1200))
    mine = env.upload(_pdf([page]))
    with env.open(other.id) as s:
        svc = make_services(s)
        theirs = UploadService(
            booklets=svc.booklets,
            repository=s.booklets,
            blobs=s.blobs,
            jobs=s.jobs,
            runtime=s.runtime,
        ).upload(
            other.id,
            other.teacher.id,
            student_id=other.students[0].id,
            blueprint_id=other_blueprint.id,
            files=[_pdf([page])],
        )
    assert env.runner.run_one() and env.runner.run_one()  # one job per college, fairly
    assert env.booklet(mine).status is BookletStatus.PAGES_READY
    with env.open(other.id) as s:
        assert s.booklets.get(other.id, theirs.booklet.id).status is BookletStatus.PAGES_READY
    keys = {k.value for k in env.blobs.keys}
    assert all(
        k.startswith(f"college/{env.college.id}/") or k.startswith(f"college/{other.id}/")
        for k in keys
    )
    assert any(f"college/{other.id}/booklet/{theirs.booklet.id}/clean/001.jpg" == k for k in keys)
