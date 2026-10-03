"""Uploading a booklet and cleaning its pages: limits, duplicates, the queue, the stages, the
quality gate, "use anyway", failures, and resuming after a crash. Image work is faked."""

import hashlib
from dataclasses import dataclass

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import Booklet, BookletStatus, RetakeReason
from tarn_core.errors import (
    DuplicateBookletError,
    InvariantError,
    NotFoundError,
    QueueFullError,
    UnsupportedFileError,
    UploadTooLargeError,
)
from tarn_core.services.pipeline import (
    FAILED_PROCESSING,
    FAILED_TOO_MANY_PAGES,
    FAILED_UNREADABLE,
    PageDecisions,
    PagePipeline,
)
from tarn_core.services.uploads import UploadLimits, UploadService, combined_hash, sniff_media_type
from tarn_core.testing import (
    FakePageCleaner,
    FakePageSplitter,
    InMemory,
    fake_image,
    fake_pdf,
)
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    ci_shaped_blueprint,
    make_services,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"rest"


@dataclass
class World:
    mem: InMemory
    college: CollegeFixture
    other: CollegeFixture
    blueprint_id: object
    cleaner: FakePageCleaner
    uploads: UploadService
    pipeline: PagePipeline
    decisions: PageDecisions

    def upload(
        self, *files: bytes, who: str = "teacher", student: int = 0, **kw: object
    ) -> Booklet:
        user = self.college.teacher if who == "teacher" else self.college.admin
        return self.uploads.upload(
            self.college.id,
            user.id,
            student_id=self.college.students[student].id,
            blueprint_id=self.blueprint_id,  # type: ignore[arg-type]
            files=list(files),
            **kw,  # type: ignore[arg-type]
        ).booklet

    def finish(self, booklet: Booklet) -> Booklet:
        for _ in range(100):
            if self.pipeline.step(self.college.id, booklet.id):
                return self.mem.booklets.get(self.college.id, booklet.id)
        raise AssertionError("the pipeline did not finish")


def make_world(*, max_waiting: int = 5, max_pages: int = 40, **limits: int) -> World:
    mem = InMemory()
    college = add_college(mem, "A")
    other = add_college(mem, "B")
    blueprint = ci_shaped_blueprint(mem, college)
    svc = make_services(mem)
    cleaner = FakePageCleaner()
    return World(
        mem=mem,
        college=college,
        other=other,
        blueprint_id=blueprint.id,
        cleaner=cleaner,
        uploads=UploadService(
            booklets=svc.booklets,
            repository=mem.booklets,
            blobs=mem.blobs,
            jobs=mem.jobs,
            runtime=mem.runtime,
            limits=UploadLimits(max_waiting=max_waiting, **limits),
        ),
        pipeline=PagePipeline(
            booklets=mem.booklets,
            blobs=mem.blobs,
            splitter=FakePageSplitter(),
            cleaner=cleaner,
            runtime=mem.runtime,
            max_pages=max_pages,
        ),
        decisions=PageDecisions(booklets=mem.booklets, runtime=mem.runtime),
    )


@pytest.fixture
def w() -> World:
    return make_world()


def audit(w: World, action: AuditAction) -> list:  # type: ignore[type-arg]
    return [e for e in w.mem.audit.events if e.action is action]


# --- the file types ---------------------------------------------------------------------------


def test_the_type_is_judged_by_the_first_bytes() -> None:
    assert sniff_media_type(b"%PDF-1.7 ...") == "application/pdf"
    assert sniff_media_type(fake_image()) == "image/jpeg"
    assert sniff_media_type(PNG) == "image/png"
    for bad in (b"", b"hello", b"GIF89a", b"PK\x03\x04", b"MZ\x90"):
        with pytest.raises(UnsupportedFileError):
            sniff_media_type(bad)


def test_a_file_hash_is_the_sha256_and_an_image_set_hashes_its_images_in_order() -> None:
    one = hashlib.sha256(b"abc").hexdigest()
    assert combined_hash([one]) == one
    a, b = hashlib.sha256(b"a").hexdigest(), hashlib.sha256(b"b").hexdigest()
    assert combined_hash([a, b]) != combined_hash([b, a])
    assert combined_hash([a, b]) == combined_hash([a, b])


# --- upload -----------------------------------------------------------------------------------


def test_a_pdf_is_stored_registered_and_queued(w: World) -> None:
    pdf = fake_pdf(3)
    booklet = w.upload(pdf)
    assert booklet.status is BookletStatus.UPLOADED
    assert booklet.file_sha256 == hashlib.sha256(pdf).hexdigest()
    (source,) = booklet.sources
    assert source.media_type == "application/pdf" and source.size_bytes == len(pdf)
    assert source.key.value == f"college/{w.college.id}/booklet/{booklet.id}/source/001.pdf"
    assert w.mem.blobs.get(source.key) == pdf
    (job,) = w.mem.jobs.jobs
    assert job.college_id == w.college.id and job.payload == {"booklet_id": str(booklet.id)}
    assert len(audit(w, AuditAction.BOOKLET_REGISTERED)) == 1


def test_images_are_stored_in_the_order_sent(w: World) -> None:
    files = [fake_image("one"), PNG, fake_image("three")]
    booklet = w.upload(*files)
    assert [s.media_type for s in booklet.sources] == ["image/jpeg", "image/png", "image/jpeg"]
    assert [s.key.value.rsplit("/", 1)[1] for s in booklet.sources] == [
        "001.jpg",
        "002.png",
        "003.jpg",
    ]
    assert [w.mem.blobs.get(s.key) for s in booklet.sources] == files


@pytest.mark.parametrize(
    ("files", "error"),
    [
        ([], InvariantError),
        ([b""], InvariantError),
        ([b"just text"], UnsupportedFileError),
        ([fake_pdf(1), fake_image()], InvariantError),
        ([fake_pdf(1), fake_pdf(1)], InvariantError),
        ([fake_image(), b"just text"], UnsupportedFileError),
    ],
)
def test_bad_uploads_are_refused_and_leave_nothing_behind(
    w: World, files: list[bytes], error: type[Exception]
) -> None:
    with pytest.raises(error):
        w.upload(*files)
    assert w.mem.blobs.keys == () and w.mem.jobs.jobs == []
    assert w.mem.booklets.list(w.college.id) == []


def test_the_size_and_file_count_limits() -> None:
    small = make_world(max_total_bytes=100, max_files=2)
    with pytest.raises(UploadTooLargeError):
        small.upload(fake_image("x" * 200))
    with pytest.raises(UploadTooLargeError):
        small.upload(fake_image("a"), fake_image("b"), fake_image("c"))
    small.upload(fake_image("a"), fake_image("b"))


def test_a_failed_registration_removes_the_stored_files(w: World) -> None:
    with pytest.raises(NotFoundError):
        w.uploads.upload(
            w.college.id,
            w.college.teacher.id,
            student_id=w.other.students[0].id,  # another college's student
            blueprint_id=w.blueprint_id,  # type: ignore[arg-type]
            files=[fake_pdf(1)],
        )
    assert w.mem.blobs.keys == () and w.mem.jobs.jobs == []


# --- the teacher's queue ----------------------------------------------------------------------


def test_a_teacher_may_have_a_limited_number_of_booklets_waiting() -> None:
    w = make_world(max_waiting=2)
    first = w.upload(fake_pdf(1, "1"))
    w.upload(fake_pdf(1, "2"))
    with pytest.raises(QueueFullError):
        w.upload(fake_pdf(1, "3"))
    # Another user of the college has their own queue.
    w.upload(fake_pdf(1, "4"), who="admin")
    # Processing frees a slot; a booklet that needs a retake no longer waits either.
    w.finish(first)
    w.upload(fake_pdf(1, "5"))


def test_booklets_in_processing_count_against_the_limit_but_failed_ones_do_not() -> None:
    w = make_world(max_waiting=1)
    booklet = w.upload(b"%PDF-bad")
    assert w.pipeline.step(w.college.id, booklet.id) is True  # unreadable: FAILED at once
    assert w.mem.booklets.get(w.college.id, booklet.id).status is BookletStatus.FAILED
    w.upload(fake_pdf(1))


# --- duplicates -------------------------------------------------------------------------------


def test_the_same_file_in_the_same_college_is_refused_unless_confirmed(w: World) -> None:
    pdf = fake_pdf(2)
    first = w.upload(pdf)
    with pytest.raises(DuplicateBookletError) as caught:
        w.upload(pdf, student=1)
    assert caught.value.duplicates == (first.id,)
    second = w.uploads.upload(
        w.college.id,
        w.college.teacher.id,
        student_id=w.college.students[1].id,
        blueprint_id=w.blueprint_id,  # type: ignore[arg-type]
        files=[pdf],
        allow_duplicate=True,
    )
    assert second.duplicates == (first.id,)
    assert second.booklet.id != first.id


def test_an_image_set_is_a_duplicate_only_with_the_same_images_in_the_same_order(
    w: World,
) -> None:
    a, b = fake_image("a"), fake_image("b")
    w.upload(a, b)
    w.upload(b, a)  # another order is another booklet
    with pytest.raises(DuplicateBookletError):
        w.upload(a, b)


def test_another_colleges_upload_is_not_a_duplicate(w: World) -> None:
    w.upload(fake_pdf(1))
    svc = make_services(w.mem)
    from tarn_core.services.uploads import UploadService as Upload  # same code, other college

    other_upload = Upload(
        booklets=svc.booklets,
        repository=w.mem.booklets,
        blobs=w.mem.blobs,
        jobs=w.mem.jobs,
        runtime=w.mem.runtime,
    )
    registration = other_upload.upload(
        w.other.id,
        w.other.teacher.id,
        student_id=w.other.students[0].id,
        blueprint_id=w.blueprint_id,  # type: ignore[arg-type]
        files=[fake_pdf(1)],
    )
    assert registration.duplicates == ()


# --- the pipeline -----------------------------------------------------------------------------


def test_a_pdf_is_split_cleaned_and_gated_one_step_at_a_time(w: World) -> None:
    booklet = w.upload(fake_pdf(3))
    college = w.college.id
    assert w.pipeline.step(college, booklet.id) is False  # prepare + split
    assert w.mem.booklets.get(college, booklet.id).status is BookletStatus.PROCESSING
    pages = w.mem.booklets.pages(college, booklet.id)
    assert [p.index for p in pages] == [0, 1, 2] and not any(p.cleaned for p in pages)
    assert [p.original.value.rsplit("/", 2)[1:] for p in pages if p.original] == [
        ["original", "001.jpg"],
        ["original", "002.jpg"],
        ["original", "003.jpg"],
    ]
    for cleaned in (1, 2, 3):
        assert w.pipeline.step(college, booklet.id) is False  # one page per step
        assert sum(p.cleaned for p in w.mem.booklets.pages(college, booklet.id)) == cleaned
    assert w.pipeline.step(college, booklet.id) is True  # gate
    done = w.mem.booklets.get(college, booklet.id)
    assert done.status is BookletStatus.PAGES_READY and done.version > booklet.version
    pages = w.mem.booklets.pages(college, booklet.id)
    assert all(p.cleaned and p.metrics and not p.retake_reasons for p in pages)
    assert [p.image.value.rsplit("/", 2)[1:] for p in pages] == [
        ["clean", "001.jpg"],
        ["clean", "002.jpg"],
        ["clean", "003.jpg"],
    ]
    assert w.mem.blobs.get(pages[0].image).startswith(b"CLEAN:")
    (event,) = audit(w, AuditAction.BOOKLET_PROCESSED)
    assert event.actor_id is None and event.booklet_id == booklet.id
    assert event.after == {
        "status": "pages_ready",
        "pages": 3,
        "flagged_pages": [],
        "cleaner": "fake-cleaner 1",
    }


def test_an_image_upload_is_its_own_original_and_page_order_is_upload_order(w: World) -> None:
    booklet = w.upload(fake_image("first"), fake_image("second"), PNG)
    w.cleaner.calls.clear()
    done = w.finish(booklet)
    pages = w.mem.booklets.pages(w.college.id, booklet.id)
    assert [p.original for p in pages] == [s.key for s in booklet.sources]
    assert w.cleaner.calls == [w.mem.blobs.get(s.key) for s in booklet.sources]
    assert done.status is BookletStatus.PAGES_READY
    assert not any("original/" in k.value for k in w.mem.blobs.keys)  # nothing copied


def test_the_quality_gate_flags_pages_with_reasons(w: World) -> None:
    booklet = w.upload(
        fake_image("fine"),
        fake_image("blur"),
        fake_image("glare"),
        fake_image("nopage"),
        fake_image("small"),
        fake_image("blur-glare"),
    )
    done = w.finish(booklet)
    assert done.status is BookletStatus.NEEDS_RETAKE
    pages = w.mem.booklets.pages(w.college.id, booklet.id)
    assert [list(p.retake_reasons) for p in pages] == [
        [],
        [RetakeReason.BLURRY],
        [RetakeReason.GLARE],
        [RetakeReason.NO_PAGE_FOUND],
        [RetakeReason.LOW_RESOLUTION],
        [RetakeReason.BLURRY, RetakeReason.GLARE],
    ]
    (event,) = audit(w, AuditAction.BOOKLET_PROCESSED)
    assert event.after["status"] == "needs_retake"
    assert event.after["flagged_pages"] == [2, 3, 4, 5, 6]


def test_use_anyway_overrides_one_flagged_page_at_a_time(w: World) -> None:
    booklet = w.upload(fake_image("fine"), fake_image("blur"), fake_image("glare"))
    w.finish(booklet)
    college, teacher = w.college.id, w.college.teacher.id
    first = w.decisions.use_anyway(college, teacher, booklet.id, 1)
    assert first.status is BookletStatus.NEEDS_RETAKE  # page 3 is still flagged
    page = w.mem.booklets.pages(college, booklet.id)[1]
    assert page.use_anyway and page.retake_reasons == (RetakeReason.BLURRY,)  # the flag stays
    assert not page.needs_retake
    last = w.decisions.use_anyway(college, teacher, booklet.id, 2)
    assert last.status is BookletStatus.PAGES_READY
    events = audit(w, AuditAction.PAGE_USED_ANYWAY)
    assert [e.after["page"] for e in events] == [2, 3]
    assert all(e.actor_id == teacher for e in events)
    assert events[1].after["booklet_status"] == "pages_ready"


def test_use_anyway_needs_a_flagged_page_and_a_booklet_waiting_for_the_decision(w: World) -> None:
    booklet = w.upload(fake_image("fine"), fake_image("blur"))
    college, teacher = w.college.id, w.college.teacher.id
    with pytest.raises(InvariantError):  # not processed yet
        w.decisions.use_anyway(college, teacher, booklet.id, 1)
    w.finish(booklet)
    with pytest.raises(InvariantError):  # page 1 passed
        w.decisions.use_anyway(college, teacher, booklet.id, 0)
    with pytest.raises(InvariantError):  # no page 9
        w.decisions.use_anyway(college, teacher, booklet.id, 8)
    w.decisions.use_anyway(college, teacher, booklet.id, 1)
    with pytest.raises(InvariantError):  # the booklet is ready now
        w.decisions.use_anyway(college, teacher, booklet.id, 1)


def test_use_anyway_is_refused_across_colleges(w: World) -> None:
    booklet = w.upload(fake_image("blur"))
    w.finish(booklet)
    with pytest.raises(NotFoundError):
        w.decisions.use_anyway(w.other.id, w.other.teacher.id, booklet.id, 0)


# --- failures ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ([b"%PDF-bad"], FAILED_UNREADABLE),
        ([fake_image("fine"), fake_image("bad")], FAILED_UNREADABLE),
        ([fake_pdf(0)], FAILED_UNREADABLE),
    ],
)
def test_an_unreadable_file_ends_the_booklet_as_failed(
    w: World, files: list[bytes], reason: str
) -> None:
    booklet = w.upload(*files)
    done = w.finish(booklet)
    assert done.status is BookletStatus.FAILED and done.failure_reason == reason
    (event,) = audit(w, AuditAction.BOOKLET_FAILED)
    assert event.actor_id is None and event.after == {"reason": reason}
    assert w.pipeline.step(w.college.id, booklet.id) is True  # finished: nothing more to do


def test_too_many_pages_ends_the_booklet_as_failed() -> None:
    pdf = make_world(max_pages=5)
    done = pdf.finish(pdf.upload(fake_pdf(6)))
    assert done.status is BookletStatus.FAILED and done.failure_reason == FAILED_TOO_MANY_PAGES
    images = make_world(max_pages=2)
    done = images.finish(images.upload(fake_image("a"), fake_image("b"), fake_image("c")))
    assert done.failure_reason == FAILED_TOO_MANY_PAGES


def test_a_missing_stored_file_fails_the_booklet_instead_of_retrying_forever(w: World) -> None:
    booklet = w.upload(fake_image("a"))
    w.mem.blobs.delete(booklet.sources[0].key)
    done = w.finish(booklet)
    assert done.status is BookletStatus.FAILED and done.failure_reason == FAILED_UNREADABLE


# --- crashes ----------------------------------------------------------------------------------


def test_a_crash_resumes_from_the_last_finished_step(w: World) -> None:
    booklet = w.upload(fake_pdf(4))
    college = w.college.id
    for _ in range(3):  # split + two pages cleaned, then "the worker dies"
        w.pipeline.step(college, booklet.id)
    assert w.cleaner.calls and len(w.cleaner.calls) == 2
    w.cleaner.calls.clear()
    revived = PagePipeline(  # a new process, same stored state
        booklets=w.mem.booklets,
        blobs=w.mem.blobs,
        splitter=FakePageSplitter(),
        cleaner=w.cleaner,
        runtime=w.mem.runtime,
    )
    while not revived.step(college, booklet.id):
        pass
    assert len(w.cleaner.calls) == 2  # only the two pages left; nothing re-done
    assert w.mem.booklets.get(college, booklet.id).status is BookletStatus.PAGES_READY
    assert len(w.mem.booklets.pages(college, booklet.id)) == 4
    assert len(audit(w, AuditAction.BOOKLET_PROCESSED)) == 1


def test_stepping_a_finished_booklet_does_nothing(w: World) -> None:
    booklet = w.upload(fake_pdf(1))
    w.finish(booklet)
    calls = len(w.cleaner.calls)
    events = len(w.mem.audit.events)
    assert w.pipeline.step(w.college.id, booklet.id) is True
    assert len(w.cleaner.calls) == calls and len(w.mem.audit.events) == events


def test_an_infrastructure_error_propagates_and_a_retry_finishes_the_job(w: World) -> None:
    booklet = w.upload(fake_image("boom"))
    w.cleaner.boom_times = 1
    w.pipeline.step(w.college.id, booklet.id)  # split
    with pytest.raises(RuntimeError):
        w.pipeline.step(w.college.id, booklet.id)  # the cleaner's storage is down
    assert w.mem.booklets.get(w.college.id, booklet.id).status is BookletStatus.PROCESSING
    done = w.finish(booklet)  # the retry
    assert done.status is BookletStatus.PAGES_READY


def test_abandoning_a_job_fails_the_booklet_once(w: World) -> None:
    booklet = w.upload(fake_pdf(2))
    w.pipeline.step(w.college.id, booklet.id)
    w.pipeline.abandon(w.college.id, booklet.id)
    done = w.mem.booklets.get(w.college.id, booklet.id)
    assert done.status is BookletStatus.FAILED and done.failure_reason == FAILED_PROCESSING
    w.pipeline.abandon(w.college.id, booklet.id)  # already ended: no second event
    assert len(audit(w, AuditAction.BOOKLET_FAILED)) == 1


# --- isolation and deletion -------------------------------------------------------------------


def test_another_college_cannot_run_or_see_the_booklet(w: World) -> None:
    booklet = w.upload(fake_pdf(1))
    with pytest.raises(NotFoundError):
        w.pipeline.step(w.other.id, booklet.id)
    assert w.mem.booklets.count_waiting(w.other.id, w.other.teacher.id) == 0


def test_deleting_a_booklet_removes_every_stored_file(w: World) -> None:
    svc = make_services(w.mem)
    pdf_booklet = w.finish(w.upload(fake_pdf(2)))
    images = w.finish(w.upload(fake_image("x"), fake_image("y")))
    keep = w.mem.blobs.keys
    assert any("/clean/" in k.value for k in keep)
    svc.booklets.delete(w.college.id, w.college.teacher.id, pdf_booklet.id)
    left = {k.value for k in w.mem.blobs.keys}
    assert left and all(f"/{images.id}/" in v for v in left)  # only the other booklet remains
    # Something a later stage stored (not a source, original or cleaned page) goes too.
    from tarn_core.domain.common import college_blob_key

    w.mem.blobs.put(
        college_blob_key(w.college.id, "booklet", str(images.id), "crops", "a.png"),
        b"x",
        "image/png",
    )
    svc.booklets.delete(w.college.id, w.college.teacher.id, images.id)
    assert w.mem.blobs.keys == ()


def test_booklet_ids_in_keys_are_not_prefix_confusable(w: World) -> None:
    # delete_prefix removes "<prefix>/..." only, never a sibling whose id starts the same way.
    from tarn_core.domain.common import college_blob_key

    store = w.mem.blobs
    a = college_blob_key(w.college.id, "booklet", "1")
    b = college_blob_key(w.college.id, "booklet", "10")
    store.put(college_blob_key(w.college.id, "booklet", "1", "x.jpg"), b"a", "image/jpeg")
    store.put(college_blob_key(w.college.id, "booklet", "10", "x.jpg"), b"b", "image/jpeg")
    assert store.delete_prefix(a) == 1
    assert [k.value.split("/")[3] for k in store.keys] == ["10"]
    assert store.delete_prefix(b) == 1
