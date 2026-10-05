"""Storing a segmentation (``text_ready`` → ``segmented``), the job queued after OCR, the
teacher's edits (merge, split, reassign, move a boundary) and the answers each one sends back
for re-scoring, approved answers refused, isolation, audit without text, and the choice rules
on a segmented booklet (P12)."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import (
    Answer,
    AnswerStatus,
    Booklet,
    BookletStatus,
    Page,
    Segment,
    SegmentFlag,
    SegmentSource,
)
from tarn_core.domain.common import college_blob_key
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BookletId, CollegeId, StudentId, UserId
from tarn_core.ports.jobs import JOB_SCORE_BOOKLET, JOB_SEGMENT_BOOKLET
from tarn_core.services.marking import Outcome, apply_choice_rules
from tarn_core.services.scoring import BookletScorer, ScoringService
from tarn_core.services.segmentation.edits import SegmentEditor
from tarn_core.services.segmentation.service import (
    FAILED_SEGMENTING,
    BookletSegmenter,
    queue_segmenting,
)
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import InMemory
from tarn_core.testing.builders import make_services
from tarn_core.testing.seed_world import seed_in_memory
from tarn_core.testing.segmentation import sheet

SHA = "a" * 64

THREE_ANSWERS = [
    [
        "^ Section B",
        "> 8. the indian constitution is a living document",
        "it is amended over time as the needs of people change",
        "> 9. parliamentary control instruments over the executive",
        "question hour, zero hour, adjournment and no confidence motions",
    ],
    [
        "control of the executive through debates and motions",
        "> 10. powers of the rajya sabha and the lok sabha",
        "the rajya sabha and lok sabha share legislative powers",
    ],
]


@dataclass
class World:
    mem: InMemory
    college: CollegeId
    teacher: UserId
    other: CollegeId
    blueprint: ExamBlueprint

    def booklet(self, pages: list[list[str]], college: CollegeId | None = None) -> Booklet:
        college_id = college or self.college
        student = self.mem.students.list(college_id)[0]
        user = next(u for u in self.mem.users.list(college_id))
        booklet = Booklet(
            id=BookletId(self.mem.ids.new()),
            college_id=college_id,
            student_id=StudentId(student.id),
            blueprint=self.blueprint.ref,
            file_sha256=SHA,
            uploaded_by=user.id,
            uploaded_at=datetime(2026, 10, 4, tzinfo=UTC),
            status=BookletStatus.TEXT_READY,
        )
        self.mem.booklets.save(college_id, booklet)
        for index, lines in enumerate(pages):
            built = sheet(college_id, index, lines, booklet=str(booklet.id))
            page = Page(
                id=built.page_id,
                college_id=college_id,
                booklet_id=booklet.id,
                index=index,
                image=college_blob_key(college_id, "booklet", str(booklet.id), f"{index}.jpg"),
                width=built.width,
                height=built.height,
                text_read=True,
            )
            self.mem.booklets.save_page(college_id, page)
            self.mem.booklets.replace_regions(college_id, page.id, built.regions)
        return booklet

    def segmenter(self) -> BookletSegmenter:
        return BookletSegmenter(
            booklets=self.mem.booklets,
            content=self.mem.content,
            embedder=TrigramEmbedder(),
            runtime=self.mem.runtime,
            jobs=self.mem.jobs,
        )

    def editor(self) -> SegmentEditor:
        return SegmentEditor(
            booklets=self.mem.booklets,
            scores=self.mem.scores,
            content=self.mem.content,
            runtime=self.mem.runtime,
        )

    def segmented(self, pages: list[list[str]] = THREE_ANSWERS) -> Booklet:
        booklet = self.booklet(pages)
        assert self.segmenter().step(self.college, booklet.id)
        return booklet

    def segments(self, booklet: Booklet) -> list[Segment]:
        return list(self.mem.booklets.segments(booklet.college_id, booklet.id))

    def answers(self, booklet: Booklet) -> dict[str, Answer]:
        return {a.slot_label: a for a in self.mem.booklets.answers(booklet.college_id, booklet.id)}


@pytest.fixture
def world() -> World:
    mem = InMemory()
    seeded = seed_in_memory(mem)
    blueprint = next(b for b in mem.content.latest(ExamBlueprint) if b.title.startswith("QP-CI"))
    commerce = next(s for s in seeded if s.seed.institution_id == "DEMO_COM")
    engineering = next(s for s in seeded if s.seed.institution_id == "DEMO_ENG")
    return World(
        mem=mem,
        college=commerce.accounts.college_id,
        teacher=commerce.accounts.teacher_ids[0],
        other=engineering.accounts.college_id,
        blueprint=blueprint,
    )


# --- the service ---------------------------------------------------------------------------


def test_segmenting_stores_segments_answers_and_page_order(world: World) -> None:
    booklet = world.segmented()
    stored = world.mem.booklets.get(world.college, booklet.id)
    assert stored.status is BookletStatus.SEGMENTED and stored.version == booklet.version + 1
    segments = world.segments(booklet)
    assert [s.slot_label for s in segments] == ["8", "9", "10"]
    assert [s.position for s in segments] == [0, 1, 2]
    assert all(s.region_ids and s.spans for s in segments)
    nine = segments[1]
    assert len({span.page_id for span in nine.spans}) == 2  # continues on page 2
    answers = world.answers(booklet)
    assert set(answers) == {"8", "9", "10"}
    assert answers["9"].segment_ids == (nine.id,)
    pages = world.mem.booklets.pages(world.college, booklet.id)
    assert [p.reading_order for p in pages] == [0, 1]


def test_segmenting_queues_scoring_and_the_scorer_takes_over(world: World) -> None:
    booklet = world.segmented()
    queued = [j for j in world.mem.jobs.jobs if j.kind == JOB_SCORE_BOOKLET]
    assert [j.payload for j in queued] == [{"booklet_id": str(booklet.id)}]
    scoring = ScoringService.standard(
        booklets=world.mem.booklets,
        scores=world.mem.scores,
        content=world.mem.content,
        runtime=world.mem.runtime,
        embedder=TrigramEmbedder(),
    )
    stage = BookletScorer(booklets=world.mem.booklets, scoring=scoring, runtime=world.mem.runtime)
    assert stage.step(world.college, booklet.id)
    assert world.mem.booklets.get(world.college, booklet.id).status is BookletStatus.SCORED
    for answer in world.answers(booklet).values():
        assert world.mem.scores.scores(world.college, answer.id)


def test_segmenting_audits_counts_only(world: World) -> None:
    booklet = world.segmented()
    event = next(e for e in world.mem.audit.events if e.action is AuditAction.BOOKLET_SEGMENTED)
    assert event.booklet_id == booklet.id and event.actor_id is None
    assert isinstance(event.after, dict) and event.after["segments"] == 3
    assert "constitution" not in str(event.after)


def test_segmenting_is_idempotent_and_waits_for_text(world: World) -> None:
    booklet = world.segmented()
    first = world.segments(booklet)
    assert world.segmenter().step(world.college, booklet.id)
    assert world.segments(booklet) == first  # segmented: left alone
    reading = world.booklet(THREE_ANSWERS)
    world.mem.booklets.save(world.college, replace(reading, status=BookletStatus.READING))
    assert world.segmenter().step(world.college, reading.id)
    assert world.segments(reading) == []


def test_resegmenting_replaces_earlier_segments(world: World) -> None:
    booklet = world.segmented()
    world.mem.booklets.save(
        world.college,
        replace(world.mem.booklets.get(world.college, booklet.id), status=BookletStatus.TEXT_READY),
    )
    world.segmenter().step(world.college, booklet.id)
    assert [s.slot_label for s in world.segments(booklet)] == ["8", "9", "10"]
    assert len(world.mem.booklets.answers(world.college, booklet.id)) == 3


def test_abandoned_segmentation_fails_the_booklet(world: World) -> None:
    booklet = world.booklet(THREE_ANSWERS)
    world.segmenter().abandon(world.college, booklet.id)
    stored = world.mem.booklets.get(world.college, booklet.id)
    assert stored.status is BookletStatus.FAILED and stored.failure_reason == FAILED_SEGMENTING


def test_job_is_queued_once_per_booklet(world: World) -> None:
    booklet = world.booklet(THREE_ANSWERS)
    queue_segmenting(world.mem.jobs, world.college, booklet.id)
    queue_segmenting(world.mem.jobs, world.college, booklet.id)
    jobs = [j for j in world.mem.jobs.jobs if j.kind == JOB_SEGMENT_BOOKLET]
    assert len(jobs) == 1 and jobs[0].payload == {"booklet_id": str(booklet.id)}


def test_another_college_cannot_segment_or_see_the_booklet(world: World) -> None:
    booklet = world.booklet(THREE_ANSWERS)
    with pytest.raises(NotFoundError):
        world.segmenter().step(world.other, booklet.id)
    world.segmenter().step(world.college, booklet.id)
    assert world.mem.booklets.segments(world.other, booklet.id) == []


# --- choice rules after segmentation ---------------------------------------------------------


def test_extra_answers_are_scored_then_not_counted(world: World) -> None:
    """Six answers in "any 5 of 7": all segmented and scored, the weakest is not counted."""
    booklet = world.segmented(
        [
            [
                "^ Section A",
                "> 1. freedom of speech, assembly and movement",
                "> 2. national, state and financial emergency",
                "> 3. parliamentary system adopted from the british",
                "> 4. bicameral parliament: lok sabha and rajya sabha",
                "> 5. qualifications of a member of parliament",
                "> 6. secular state: freedom of religion",
            ]
        ]
    )
    labels = [s.slot_label or "?" for s in world.segments(booklet)]
    assert labels == ["1", "2", "3", "4", "5", "6"]
    marks = {label: Decimal(2) for label in labels} | {"4": Decimal("0.5")}
    result = apply_choice_rules(world.blueprint, marks)
    outcome = {s.slot_label: s.outcome for s in result.slots}
    assert outcome["4"] is Outcome.NOT_COUNTED_BEST_N
    assert sum(o is Outcome.COUNTED for o in outcome.values()) == 5


# --- edits ---------------------------------------------------------------------------------


def test_merge(world: World) -> None:
    booklet = world.segmented()
    _, nine, ten = world.segments(booklet)
    answers = world.answers(booklet)
    result = world.editor().merge(world.college, world.teacher, booklet.id, nine.id, ten.id)
    assert [s.slot_label for s in result.segments] == ["8", "9"]
    merged = result.segments[1]
    assert merged.id == nine.id and set(merged.region_ids) == set(nine.region_ids + ten.region_ids)
    assert merged.source is SegmentSource.TEACHER
    assert result.rescore == (answers["9"].id,)
    assert result.emptied == (answers["10"].id,)
    assert "10" not in world.answers(booklet)  # never scored: deleted
    assert ten.id not in {s.id for s in world.segments(booklet)}


def test_split_then_reassign(world: World) -> None:
    booklet = world.segmented()
    _, nine, _ = world.segments(booklet)
    cut = nine.region_ids[2]
    result = world.editor().split(world.college, world.teacher, booklet.id, nine.id, cut)
    assert [s.slot_label for s in result.segments] == ["8", "9", None, "10"]
    assert [s.position for s in result.segments] == [0, 1, 2, 3]
    tray = result.segments[2]
    assert tray.region_ids[0] == cut and tray.source is SegmentSource.TEACHER
    assert result.rescore == (world.answers(booklet)["9"].id,)

    again = world.editor().reassign(world.college, world.teacher, booklet.id, tray.id, "11")
    assert again.segments[2].slot_label == "11"
    eleven = world.answers(booklet)["11"]
    assert again.rescore == (eleven.id,)


def test_reassign_to_a_question_already_answered_flags_both(world: World) -> None:
    booklet = world.segmented()
    eight, _, ten = world.segments(booklet)
    result = world.editor().reassign(world.college, world.teacher, booklet.id, ten.id, "8")
    flagged = [s for s in result.segments if s.slot_label == "8"]
    assert len(flagged) == 2 and all(SegmentFlag.DUPLICATE in s.flags for s in flagged)
    answer = world.answers(booklet)["8"]
    assert answer.segment_ids == (eight.id, ten.id)
    assert result.rescore == (answer.id,)


def test_move_boundary(world: World) -> None:
    booklet = world.segmented()
    eight, nine, _ = world.segments(booklet)
    first_of_nine = nine.region_ids[0]
    result = world.editor().move_boundary(
        world.college, world.teacher, booklet.id, eight.id, nine.id, nine.region_ids[1]
    )
    moved_eight, moved_nine = result.segments[0], result.segments[1]
    assert moved_eight.region_ids[-1] == first_of_nine
    assert moved_nine.region_ids[0] == nine.region_ids[1]
    answers = world.answers(booklet)
    assert set(result.rescore) == {answers["8"].id, answers["9"].id}
    with pytest.raises(InvariantError):  # not neighbours
        world.editor().move_boundary(
            world.college,
            world.teacher,
            booklet.id,
            eight.id,
            result.segments[2].id,
            result.segments[2].region_ids[0],
        )


def test_edit_refused_for_unknown_question_and_wrong_status(world: World) -> None:
    booklet = world.segmented()
    eight = world.segments(booklet)[0]
    with pytest.raises(InvariantError):
        world.editor().reassign(world.college, world.teacher, booklet.id, eight.id, "99")
    waiting = world.booklet(THREE_ANSWERS)
    with pytest.raises(InvariantError):
        world.editor().reassign(world.college, world.teacher, waiting.id, eight.id, None)


def test_edit_touching_an_approved_answer_is_refused(world: World) -> None:
    booklet = world.segmented()
    eight, nine, _ = world.segments(booklet)
    answer = world.answers(booklet)["9"]
    world.mem.booklets.save_answer(
        world.college,
        replace(answer, status=AnswerStatus.APPROVED),
    )
    before = world.segments(booklet)
    with pytest.raises(InvariantError, match="approved"):
        world.editor().merge(world.college, world.teacher, booklet.id, eight.id, nine.id)
    assert world.segments(booklet) == before


def test_emptied_answer_with_a_score_is_kept_empty(world: World) -> None:
    booklet = world.segmented()
    _, _, ten = world.segments(booklet)
    answer = world.answers(booklet)["10"]
    services = make_services(world.mem)
    services.scoring.score_answer(
        world.college,
        world.teacher,
        answer.id,
        answer_text="powers",
    )
    result = world.editor().reassign(world.college, world.teacher, booklet.id, ten.id, None)
    assert result.emptied == (answer.id,)
    kept = world.mem.booklets.get_answer(world.college, answer.id)
    assert kept.segment_ids == () and world.mem.scores.scores(world.college, kept.id)
    totals = services.totals.preview(world.college, booklet.id)
    assert "10" not in {s.slot_label for s in totals.result.slots if s.mark is not None}


def test_edits_are_audited_without_text(world: World) -> None:
    booklet = world.segmented()
    eight, nine, _ = world.segments(booklet)
    world.editor().merge(world.college, world.teacher, booklet.id, eight.id, nine.id)
    event = world.mem.audit.events[-1]
    assert event.action is AuditAction.SEGMENT_EDITED and event.actor_id == world.teacher
    assert isinstance(event.after, dict) and event.after["operation"] == "merge"
    assert "constitution" not in str(event.before) + str(event.after)


def test_another_college_cannot_edit(world: World) -> None:
    booklet = world.segmented()
    eight, nine, _ = world.segments(booklet)
    with pytest.raises(NotFoundError):
        world.editor().merge(world.other, world.teacher, booklet.id, eight.id, nine.id)
