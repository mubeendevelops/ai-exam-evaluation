"""The teacher's corrections of what the machine read, under the booklet lock (D109, D110):
an OCR line's text or struck-out mark, the segments, a recognised drawing's graph. Each edit
carries a version and re-scores only the answers it touched; an edit that would change an
approved answer is refused (reopen it as an amendment first).

While a booklet is being amended, only its drafts may change: a segment edit that would touch
any other answer is refused before anything is written."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.booklet import (
    Answer,
    AnswerStatus,
    Booklet,
    BookletStatus,
    Region,
    RegionKind,
    Segment,
)
from tarn_core.domain.diagram import StudentDiagram
from tarn_core.domain.review import RegionEdit
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import (
    AnswerId,
    BookletId,
    CollegeId,
    RegionEditId,
    RegionId,
    SegmentId,
    StudentDiagramId,
    UserId,
)
from tarn_core.ports.jobs import JOB_RESEGMENT_BOOKLET, JobQueue
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services._support import Runtime
from tarn_core.services.diagrams.editor import GraphEdit
from tarn_core.services.diagrams.service import StudentDiagrams
from tarn_core.services.scoring.booklet import AnswerApprovedError
from tarn_core.services.segmentation.edits import EDITABLE, RESEGMENTABLE, EditResult, SegmentEditor
from tarn_core.services.workflow.guard import BookletGuard
from tarn_core.services.workflow.rescore import RescoreRequests
from tarn_core.services.workflow.truth import CorrectionTruth

MAX_LINE_CHARS = 2000
_TEXT_KINDS = frozenset({RegionKind.TEXT_LINE, RegionKind.TEXT_BLOCK, RegionKind.LABEL})


def _editable(booklet: Booklet) -> None:
    if booklet.status not in EDITABLE:
        raise InvariantError(f"the booklet cannot be edited while it is {booklet.status}")


def _bump(booklets: BookletRepository, booklet: Booklet) -> Booklet:
    updated = replace(booklet, version=booklet.version + 1)
    booklets.save(booklet.college_id, updated)
    return updated


@dataclass(frozen=True, slots=True, kw_only=True)
class TextEditResult:
    booklet: Booklet
    region: Region
    edit: RegionEdit
    rescoring: tuple[AnswerId, ...]


class TextEditor:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        runtime: Runtime,
        guard: BookletGuard,
        rescore: RescoreRequests,
        truth: CorrectionTruth | None = None,
    ) -> None:
        """``truth`` stores each text correction as OCR ground truth (P16); tests that do not
        look at it leave it out."""
        self._booklets = booklets
        self._rt = runtime
        self._guard = guard
        self._rescore = rescore
        self._truth = truth

    def correct(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        region_id: RegionId,
        *,
        expected_version: int,
        text: str | None = None,
        struck_out: bool | None = None,
    ) -> TextEditResult:
        """Set the line's text (None: keep it) and/or its struck-out mark (None: keep it).
        The answers holding the line are re-scored."""
        booklet = self._guard.hold(college_id, actor, booklet_id, expected_version=expected_version)
        _editable(booklet)
        region = self._find(college_id, booklet_id, region_id)
        if region.kind not in _TEXT_KINDS:
            raise InvariantError(f"a {region.kind} region has no text to correct")
        if text is not None and len(text) > MAX_LINE_CHARS:
            raise InvariantError(f"a line holds at most {MAX_LINE_CHARS} characters")
        new_text = region.text if text is None else text
        new_struck = region.struck_out if struck_out is None else struck_out
        edit = RegionEdit(
            id=self._rt.new_id(RegionEditId),
            college_id=college_id,
            booklet_id=booklet_id,
            region_id=region_id,
            actor=actor,
            at=self._rt.clock.now(),
            before_text=region.text,
            after_text=new_text,
            before_struck_out=region.struck_out,
            after_struck_out=new_struck,
        )
        affected = self._answers_holding(college_id, booklet, region_id)
        updated = replace(
            region,
            teacher_text=region.teacher_text if text is None else text,
            struck_out=new_struck,
        )
        self._booklets.save_region(college_id, updated)
        self._booklets.save_region_edit(college_id, edit)
        if self._truth is not None and text is not None and new_text != region.text:
            self._truth.record(college_id, booklet_id, updated, new_text or "")
        booklet = _bump(self._booklets, booklet)
        self._rt.record(
            college_id,
            actor,
            AuditAction.REGION_EDITED,
            booklet_id=booklet_id,
            before={
                "region_id": str(region_id),
                "chars": len(region.text or ""),
                "struck_out": region.struck_out,
                "booklet_version": expected_version,
            },
            after={
                "region_id": str(region_id),
                "edit_id": str(edit.id),
                "chars": len(new_text or ""),
                "struck_out": new_struck,
                "booklet_version": booklet.version,
                "rescore": [str(a) for a in affected],
            },
        )
        self._rescore.request(college_id, actor, affected)
        return TextEditResult(booklet=booklet, region=updated, edit=edit, rescoring=tuple(affected))

    def _find(self, college_id: CollegeId, booklet_id: BookletId, region_id: RegionId) -> Region:
        for page in self._booklets.pages(college_id, booklet_id):
            for region in self._booklets.regions(college_id, page.id):
                if region.id == region_id:
                    return region
        raise NotFoundError(f"region {region_id}")

    def _answers_holding(
        self, college_id: CollegeId, booklet: Booklet, region_id: RegionId
    ) -> list[AnswerId]:
        segments = {
            s.id
            for s in self._booklets.segments(college_id, booklet.id)
            if region_id in s.region_ids
        }
        answers = [
            a
            for a in self._booklets.answers(college_id, booklet.id)
            if segments & set(a.segment_ids)
        ]
        if any(a.status is AnswerStatus.APPROVED for a in answers):
            raise AnswerApprovedError("an approved answer changes only through an amendment")
        return [a.id for a in answers]


@dataclass(frozen=True, slots=True, kw_only=True)
class SegmentEditResult:
    booklet: Booklet
    segments: tuple[Segment, ...]
    rescoring: tuple[AnswerId, ...]
    emptied: tuple[AnswerId, ...]


class SegmentEdits:
    """``SegmentEditor``'s merge, split, reassign and boundary move, under the lock and the
    booklet's version; the answers whose text changed are re-scored."""

    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        runtime: Runtime,
        guard: BookletGuard,
        rescore: RescoreRequests,
    ) -> None:
        self._booklets = booklets
        self._guard = guard
        self._rescore = rescore
        self._editor = SegmentEditor(
            booklets=booklets,
            scores=scores,
            content=content,
            runtime=runtime,
            check=self._drafts_only,
        )

    def merge(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        *,
        expected_version: int,
        first: SegmentId,
        second: SegmentId,
    ) -> SegmentEditResult:
        return self._run(
            college_id,
            actor,
            booklet_id,
            expected_version,
            lambda: self._editor.merge(college_id, actor, booklet_id, first, second),
        )

    def split(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        *,
        expected_version: int,
        segment_id: SegmentId,
        at_region: RegionId,
        label: str | None = None,
    ) -> SegmentEditResult:
        return self._run(
            college_id,
            actor,
            booklet_id,
            expected_version,
            lambda: self._editor.split(college_id, actor, booklet_id, segment_id, at_region, label),
        )

    def reassign(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        *,
        expected_version: int,
        segment_id: SegmentId,
        label: str | None,
    ) -> SegmentEditResult:
        return self._run(
            college_id,
            actor,
            booklet_id,
            expected_version,
            lambda: self._editor.reassign(college_id, actor, booklet_id, segment_id, label),
        )

    def move_boundary(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        *,
        expected_version: int,
        upper: SegmentId,
        lower: SegmentId,
        region: RegionId,
    ) -> SegmentEditResult:
        return self._run(
            college_id,
            actor,
            booklet_id,
            expected_version,
            lambda: self._editor.move_boundary(college_id, actor, booklet_id, upper, lower, region),
        )

    def _drafts_only(self, booklet: Booklet, changed: Sequence[Answer]) -> None:
        if booklet.status is not BookletStatus.AMENDMENT_IN_PROGRESS:
            return
        drafts = {
            a.answer_id for a in self._booklets.amendments(booklet.college_id, booklet.id) if a.open
        }
        if any(a.id not in drafts for a in changed):
            raise InvariantError(
                "while the booklet is amended only its drafts can change: reopen the answers "
                "this edit touches first"
            )

    def _run(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        expected_version: int,
        edit: Callable[[], EditResult],
    ) -> SegmentEditResult:
        booklet = self._guard.hold(college_id, actor, booklet_id, expected_version=expected_version)
        _editable(booklet)
        result = edit()
        booklet = _bump(self._booklets, self._booklets.get(college_id, booklet_id))
        self._rescore.request(college_id, actor, result.rescore)
        return SegmentEditResult(
            booklet=booklet,
            segments=result.segments,
            rescoring=result.rescore,
            emptied=result.emptied,
        )


class ResegmentRequests:
    """ "Re-Segment": under the lock and the booklet's version, queue the worker's job that
    segments the booklet again from its current text (the embedder is not loaded in the API).
    Refused while any answer is approved: the new split could change it."""

    def __init__(
        self,
        *,
        booklets: BookletRepository,
        guard: BookletGuard,
        jobs: JobQueue,
    ) -> None:
        self._booklets = booklets
        self._guard = guard
        self._jobs = jobs

    def request(
        self, college_id: CollegeId, actor: UserId, booklet_id: BookletId, *, expected_version: int
    ) -> Booklet:
        booklet = self._guard.hold(college_id, actor, booklet_id, expected_version=expected_version)
        if booklet.status not in RESEGMENTABLE:
            raise InvariantError(f"a booklet that is {booklet.status} cannot be segmented again")
        if any(
            a.status is AnswerStatus.APPROVED
            for a in self._booklets.answers(college_id, booklet_id)
        ):
            raise AnswerApprovedError(
                "segmenting again could change approved answers: reopen them first"
            )
        self._jobs.enqueue(
            college_id,
            JOB_RESEGMENT_BOOKLET,
            {
                "booklet_id": str(booklet_id),
                "actor_id": str(actor),
                "expected_version": booklet.version,
            },
            key=f"{JOB_RESEGMENT_BOOKLET}:{booklet_id}",
        )
        return booklet


class StudentGraphEdits:
    """A recognised drawing corrected under the lock; its answer is re-scored."""

    def __init__(
        self,
        *,
        booklets: BookletRepository,
        runtime: Runtime,
        guard: BookletGuard,
        rescore: RescoreRequests,
    ) -> None:
        self._guard = guard
        self._diagrams = StudentDiagrams(booklets=booklets, runtime=runtime, rescore=rescore)

    def edit(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        diagram_id: StudentDiagramId,
        *,
        expected_version: int,
        edits: Sequence[GraphEdit],
    ) -> StudentDiagram:
        self._guard.hold(college_id, actor, booklet_id)
        return self._diagrams.edit(
            college_id,
            actor,
            booklet_id,
            diagram_id,
            expected_version=expected_version,
            edits=edits,
        )
