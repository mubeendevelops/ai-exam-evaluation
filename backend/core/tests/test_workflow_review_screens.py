"""What the segmentation screens rely on (P16): every text correction becomes OCR ground truth
in the booklet's own folder (and goes with the booklet), and "Re-Segment" queues a job that
segments the booklet again under the lock and its version."""

from datetime import timedelta

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, BookletStatus, SegmentSource
from tarn_core.domain.common import college_blob_key
from tarn_core.domain.groundtruth import TruthOrigin, TruthStatus
from tarn_core.errors import BookletLockedError, InvariantError, StaleWriteError
from tarn_core.ids import AnswerId, RegionId
from tarn_core.ports.jobs import JOB_RESEGMENT_BOOKLET
from tarn_core.services.groundtruth import GroundTruthService
from tarn_core.services.scoring.booklet import AnswerApprovedError
from tarn_core.services.workflow import BookletTruthStore, LockPolicy, truth_page_id
from tarn_core.testing import InMemory
from tarn_core.testing.builders import CollegeFixture, add_college, ci_shaped_blueprint
from tarn_core.testing.workflow import GOOD, HALF, Workflow, scored_booklet, workflow

THREE = {"1": GOOD, "2": HALF, "3": GOOD}


def setup() -> tuple[InMemory, CollegeFixture, Booklet, ExamBlueprint, Workflow]:
    mem = InMemory()
    college = add_college(mem, "A")
    blueprint = ci_shaped_blueprint(mem, college)
    booklet = scored_booklet(mem, college, blueprint, THREE)
    wf = workflow(mem, policy=LockPolicy(timeout=timedelta(minutes=15)))
    wf.review.open(college.id, college.teacher.id, booklet.id)
    return mem, college, mem.booklets.get(college.id, booklet.id), blueprint, wf


def answers(mem: InMemory, booklet: Booklet) -> dict[str, AnswerId]:
    return {a.slot_label: a.id for a in mem.booklets.answers(booklet.college_id, booklet.id)}


def region_of(mem: InMemory, booklet: Booklet, label: str) -> RegionId:
    answer = mem.booklets.get_answer(booklet.college_id, answers(mem, booklet)[label])
    segment = next(
        s
        for s in mem.booklets.segments(booklet.college_id, booklet.id)
        if s.id == answer.segment_ids[0]
    )
    return segment.region_ids[0]


def truth_store(mem: InMemory, booklet: Booklet) -> BookletTruthStore:
    pages = mem.booklets.pages(booklet.college_id, booklet.id)
    return BookletTruthStore(
        mem.blobs, booklet.college_id, booklet.id, [truth_page_id(p) for p in pages]
    )


# --- ground truth from corrections ------------------------------------------------------------


def test_a_text_correction_is_kept_as_verified_ground_truth() -> None:
    mem, college, booklet, _, wf = setup()
    region = region_of(mem, booklet, "2")
    wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        region,
        expected_version=booklet.version,
        text="alpha and  beta.",
    )
    pages = GroundTruthService(truth_store(mem, booklet)).pages()
    assert len(pages) == 1
    line = pages[0].lines[0]
    assert (line.text, line.status, line.origin) == (
        "alpha and beta.",
        TruthStatus.VERIFIED,
        TruthOrigin.TEACHER_CORRECTION,
    )
    assert line.region_id == region
    assert pages[0].college_id == college.id
    assert pages[0].verified() == (line,)


def test_ground_truth_is_stored_under_the_booklet_so_deleting_it_removes_it() -> None:
    mem, college, booklet, _, wf = setup()
    wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        region_of(mem, booklet, "2"),
        expected_version=booklet.version,
        text="alpha and beta.",
    )
    (page_id,) = truth_store(mem, booklet).page_ids()
    key = college_blob_key(
        college.id, "booklet", str(booklet.id), "groundtruth", f"{page_id}.jsonl"
    )
    assert mem.blobs.exists(key)
    mem.blobs.delete_prefix(college_blob_key(college.id, "booklet", str(booklet.id)))
    assert not mem.blobs.exists(key)


def test_striking_a_line_out_is_not_a_text_correction() -> None:
    mem, college, booklet, _, wf = setup()
    wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        region_of(mem, booklet, "2"),
        expected_version=booklet.version,
        struck_out=True,
    )
    assert truth_store(mem, booklet).page_ids() == []


def test_a_second_correction_on_the_same_line_replaces_the_first() -> None:
    mem, college, booklet, _, wf = setup()
    region = region_of(mem, booklet, "2")
    for text in ("first try", "second try"):
        current = mem.booklets.get(college.id, booklet.id)
        wf.text.correct(
            college.id,
            college.teacher.id,
            booklet.id,
            region,
            expected_version=current.version,
            text=text,
        )
    (page,) = GroundTruthService(truth_store(mem, booklet)).pages()
    assert [ln.text for ln in page.lines] == ["second try"]


# --- re-segment -------------------------------------------------------------------------------


def request_resegment(
    mem: InMemory, college: CollegeFixture, booklet: Booklet, wf: Workflow
) -> int:
    queued = wf.resegment.request(
        college.id, college.teacher.id, booklet.id, expected_version=booklet.version
    )
    return queued.version


def test_resegment_needs_the_lock_and_the_current_version() -> None:
    mem, college, booklet, _, wf = setup()
    other = college.admin
    with pytest.raises(BookletLockedError):
        wf.resegment.request(college.id, other.id, booklet.id, expected_version=booklet.version)
    with pytest.raises(StaleWriteError):
        wf.resegment.request(
            college.id, college.teacher.id, booklet.id, expected_version=booklet.version - 1
        )
    assert not [j for j in mem.jobs.jobs if j.kind == JOB_RESEGMENT_BOOKLET]


def test_resegment_is_queued_once_with_the_version_it_was_asked_at() -> None:
    mem, college, booklet, _, wf = setup()
    version = request_resegment(mem, college, booklet, wf)
    request_resegment(mem, college, booklet, wf)
    jobs = [j for j in mem.jobs.jobs if j.kind == JOB_RESEGMENT_BOOKLET]
    assert len(jobs) == 1
    assert jobs[0].payload["expected_version"] == version
    assert jobs[0].payload["actor_id"] == str(college.teacher.id)


def test_resegment_is_refused_while_an_answer_is_approved() -> None:
    mem, college, booklet, _, wf = setup()
    answer = mem.booklets.get_answer(college.id, answers(mem, booklet)["1"])
    wf.review.approve_answer(
        college.id, college.teacher.id, booklet.id, answer.id, expected_version=answer.version
    )
    booklet = mem.booklets.get(college.id, booklet.id)
    with pytest.raises(AnswerApprovedError):
        request_resegment(mem, college, booklet, wf)


def test_resegment_is_refused_once_the_machine_is_still_working() -> None:
    mem, college, booklet, _, wf = setup()
    from dataclasses import replace

    mem.booklets.save(
        college.id, replace(booklet, status=BookletStatus.SEGMENTED, version=booklet.version + 1)
    )
    with pytest.raises(InvariantError):
        request_resegment(mem, college, mem.booklets.get(college.id, booklet.id), wf)


def test_the_job_replaces_every_segment_and_keeps_the_answers() -> None:
    mem, college, booklet, _, wf = setup()
    before_answers = answers(mem, booklet)
    # A teacher's own edit that the machine's proposal replaces.
    segment = mem.booklets.segments(college.id, booklet.id)[0]
    edited = wf.segments.reassign(
        college.id,
        college.teacher.id,
        booklet.id,
        expected_version=booklet.version,
        segment_id=segment.id,
        label=None,
    )
    assert any(s.source is SegmentSource.TEACHER for s in edited.segments)
    booklet = mem.booklets.get(college.id, booklet.id)
    version = request_resegment(mem, college, booklet, wf)

    done = wf.resegmenter.run(college.id, college.teacher.id, booklet.id, expected_version=version)

    assert done is not None
    assert done.booklet.version == version + 1
    segments = mem.booklets.segments(college.id, booklet.id)
    assert segments and all(s.source is not SegmentSource.TEACHER for s in segments)
    assert sorted(s.position for s in segments) == list(range(len(segments)))
    # Answers keep their ids; the ones whose text changed were sent to re-score.
    assert set(answers(mem, booklet).values()) <= set(before_answers.values())
    event = next(
        e
        for e in mem.audit.events
        if e.action is AuditAction.SEGMENT_EDITED and "'resegment'" in str(e.after)
    )
    assert "alpha" not in str(event.before) + str(event.after)  # ids and sizes, no text


def test_a_job_that_finds_another_version_stands_down() -> None:
    mem, college, booklet, _, wf = setup()
    version = request_resegment(mem, college, booklet, wf)
    # The teacher edited in the meantime.
    wf.text.correct(
        college.id,
        college.teacher.id,
        booklet.id,
        region_of(mem, booklet, "2"),
        expected_version=version,
        struck_out=True,
    )
    before = mem.booklets.segments(college.id, booklet.id)
    assert (
        wf.resegmenter.run(college.id, college.teacher.id, booklet.id, expected_version=version)
        is None
    )
    assert mem.booklets.segments(college.id, booklet.id) == before
