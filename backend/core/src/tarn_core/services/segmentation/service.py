"""Segmenting a booklet whose text is read (``text_ready`` → ``segmented``), in one step: a
booklet's lines are a few hundred, so the whole split fits one transaction. Safe to repeat: a
booklet past ``text_ready`` is left alone, and a re-run replaces earlier segments.

Writes each page's written number and reading order, one segment per answer found (in written
order), one answer per question leaf with at least one segment, and an audit event with counts
only (``booklet.segmented``). Scoring (P13) takes over from ``segmented``."""

from collections.abc import Mapping
from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import (
    Answer,
    Booklet,
    BookletStatus,
    Segment,
    SegmentFlag,
)
from tarn_core.domain.content import Question
from tarn_core.ids import AnswerId, BookletId, CollegeId, SegmentId
from tarn_core.ports.engines import Embedder
from tarn_core.ports.jobs import JOB_SEGMENT_BOOKLET, JobQueue
from tarn_core.ports.repositories import BookletRepository, ContentRepository
from tarn_core.services._support import Runtime
from tarn_core.services.segmentation.lines import PageInput
from tarn_core.services.segmentation.segmenter import (
    SegmentationPolicy,
    SegmentationResult,
    Segmenter,
)

FAILED_SEGMENTING = "segmentation_failed"


def queue_segmenting(jobs: JobQueue, college_id: CollegeId, booklet_id: BookletId) -> None:
    """Queue the segmentation job of a booklet whose text is read (idempotent per booklet)."""
    jobs.enqueue(
        college_id,
        JOB_SEGMENT_BOOKLET,
        {"booklet_id": str(booklet_id)},
        key=f"{JOB_SEGMENT_BOOKLET}:{booklet_id}",
    )


def question_texts(content: ContentRepository, blueprint: ExamBlueprint) -> dict[str, str]:
    """The wording of each linked leaf's question (latest version), by leaf label."""
    texts: dict[str, str] = {}
    for slot in blueprint.slots():
        for label, question_id, _ in leaves(slot):
            if question_id is not None:
                texts[label] = content.get(Question, question_id).text
    return texts


def booklet_inputs(
    booklets: BookletRepository, college_id: CollegeId, booklet_id: BookletId
) -> list[PageInput]:
    return [
        PageInput(
            page_id=page.id,
            index=page.index,
            width=page.width,
            height=page.height,
            regions=tuple(booklets.regions(college_id, page.id)),
        )
        for page in booklets.pages(college_id, booklet_id)
    ]


def answers_for(
    booklet: Booklet,
    segments: list[Segment],
    existing: Mapping[str, Answer],
    runtime: Runtime,
) -> dict[str, Answer]:
    """One answer per assigned leaf, its segments in written order; an existing answer of the
    same leaf keeps its id (and gets a new version when its segments changed)."""
    grouped: dict[str, list[SegmentId]] = {}
    for segment in sorted(segments, key=lambda s: s.position):
        if segment.slot_label is not None:
            grouped.setdefault(segment.slot_label, []).append(segment.id)
    answers: dict[str, Answer] = {}
    for label, ids in grouped.items():
        old = existing.get(label)
        if old is None:
            answers[label] = Answer(
                id=runtime.new_id(AnswerId),
                college_id=booklet.college_id,
                booklet_id=booklet.id,
                slot_label=label,
                segment_ids=tuple(ids),
            )
        elif old.segment_ids != tuple(ids):
            answers[label] = replace(old, segment_ids=tuple(ids), version=old.version + 1)
        else:
            answers[label] = old
    return answers


class BookletSegmenter:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        content: ContentRepository,
        embedder: Embedder,
        runtime: Runtime,
        policy: SegmentationPolicy | None = None,
    ) -> None:
        self._booklets = booklets
        self._content = content
        self._embedder = embedder
        self._rt = runtime
        self._policy = policy or SegmentationPolicy()

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        """Segment the booklet; True (there is never more to do for it afterwards)."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status is not BookletStatus.TEXT_READY:
            return True
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        segmenter = Segmenter(
            blueprint, question_texts(self._content, blueprint), self._embedder, self._policy
        )
        result = segmenter.segment(booklet_inputs(self._booklets, college_id, booklet_id))
        self._store(booklet, result)
        return True

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """The job ran out of attempts: end the booklet as FAILED so it stops waiting."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status is not BookletStatus.TEXT_READY:
            return
        self._booklets.save(
            college_id,
            replace(
                booklet,
                status=BookletStatus.FAILED,
                failure_reason=FAILED_SEGMENTING,
                version=booklet.version + 1,
            ),
        )
        self._rt.record(
            college_id,
            None,
            AuditAction.BOOKLET_FAILED,
            booklet_id=booklet_id,
            after={"reason": FAILED_SEGMENTING},
        )

    def _store(self, booklet: Booklet, result: SegmentationResult) -> None:
        college_id = booklet.college_id
        position = {page_id: k for k, page_id in enumerate(result.order.order)}
        for page in self._booklets.pages(college_id, booklet.id):
            self._booklets.save_page(
                college_id,
                replace(
                    page,
                    written_number=result.order.written.get(page.id),
                    reading_order=position[page.id],
                ),
            )
        for answer in self._booklets.answers(college_id, booklet.id):
            self._booklets.delete_answer(college_id, answer.id)
        for old in self._booklets.segments(college_id, booklet.id):
            self._booklets.delete_segment(college_id, old.id)
        segments = [
            Segment(
                id=self._rt.new_id(SegmentId),
                college_id=college_id,
                booklet_id=booklet.id,
                slot_label=proposed.slot_label,
                spans=proposed.spans,
                source=proposed.source,
                match_score=proposed.match_score,
                region_ids=proposed.region_ids,
                flags=proposed.flags,
                position=proposed.position,
                proposed_label=proposed.proposed_label,
            )
            for proposed in result.segments
            if proposed.spans
        ]
        for segment in segments:
            self._booklets.save_segment(college_id, segment)
        answers = answers_for(booklet, segments, {}, self._rt)
        for answer in answers.values():
            self._booklets.save_answer(college_id, answer)
        self._booklets.save(
            college_id,
            replace(booklet, status=BookletStatus.SEGMENTED, version=booklet.version + 1),
        )
        self._rt.record(
            college_id,
            None,
            AuditAction.BOOKLET_SEGMENTED,
            booklet_id=booklet.id,
            after={
                "segments": len(segments),
                "answers": len(answers),
                "unassigned": sum(1 for s in segments if s.slot_label is None),
                "by_similarity": sum(1 for s in segments if s.source.value == "similarity"),
                "duplicates": sum(1 for s in segments if SegmentFlag.DUPLICATE in s.flags),
                "flagged": sum(1 for s in segments if s.flags),
                "pages_reordered": result.order.reordered,
                "slivers_left_out": result.slivers,
                "embedder": f"{result.embedder.name} {result.embedder.version}",
            },
        )
