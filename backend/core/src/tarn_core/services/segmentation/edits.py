"""The teacher's corrections of a segmentation (design.md "Segmentation": "The teacher can
merge, split or reassign any segment; each change re-scores only the answers it touches").

Every edit returns the answers whose text changed (to be re-scored, P13) and those left without
any segment. An answer that lost its segments but was already scored is kept, empty, so its
score history stays (an empty answer counts as not attempted); one never scored is deleted.
Edits that would change an approved answer are refused: reopen it first (an amendment once
the booklet is approved, P15). The lock and version checks are ``workflow.SegmentEdits``'s.
Edited segments become the teacher's (``source = teacher``); their machine flags are dropped
because the teacher has looked, except ``duplicate``, which is worked out again."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import (
    Answer,
    AnswerStatus,
    Booklet,
    BookletStatus,
    Segment,
    SegmentFlag,
    SegmentSource,
)
from tarn_core.domain.common import JsonValue
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import AnswerId, BookletId, CollegeId, RegionId, SegmentId, UserId
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services._support import Runtime
from tarn_core.services.segmentation.lines import PageLines, page_lines
from tarn_core.services.segmentation.segmenter import spans_for
from tarn_core.services.segmentation.service import answers_for, booklet_inputs

EDITABLE = frozenset(
    {
        BookletStatus.SEGMENTED,
        BookletStatus.SCORED,
        BookletStatus.IN_REVIEW,
        BookletStatus.AMENDMENT_IN_PROGRESS,
    }
)

RESEGMENTABLE = frozenset({BookletStatus.SCORED, BookletStatus.IN_REVIEW})
"""Where "Re-Segment" (P16) may run: not while the machine is still working on the booklet, and
not once it is approved (an amendment changes single answers, not the booklet's whole split)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class EditResult:
    segments: tuple[Segment, ...]
    """The booklet's segments after the edit, in written order."""
    rescore: tuple[AnswerId, ...]
    """Answers whose segments changed: score them again."""
    emptied: tuple[AnswerId, ...]
    """Answers left without any segment (kept when scored, else deleted)."""


class SegmentEditor:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        runtime: Runtime,
        check: Callable[[Booklet, Sequence[Answer]], None] | None = None,
    ) -> None:
        """``check`` sees the booklet and the answers an edit would change before anything is
        written, and raises to refuse the edit (the review's amendment rule, P15)."""
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._rt = runtime
        self._check = check

    # --- the four edits ---------------------------------------------------------------------

    def merge(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        first: SegmentId,
        second: SegmentId,
    ) -> EditResult:
        """``second`` joins ``first``; the result keeps ``first``'s id and question (or
        ``second``'s when ``first`` is unassigned)."""
        if first == second:
            raise InvariantError("merge needs two different segments")
        booklet, segments = self._load(college_id, booklet_id)
        a, b = _find(segments, first), _find(segments, second)
        merged = replace(
            a,
            slot_label=a.slot_label if a.slot_label is not None else b.slot_label,
            region_ids=a.region_ids + b.region_ids,
            position=min(a.position, b.position),
        )
        after = [merged if s.id == first else s for s in segments if s.id != second]
        return self._apply(booklet, actor, segments, after, {first}, "merge")

    def split(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        segment_id: SegmentId,
        at_region: RegionId,
        label: str | None = None,
    ) -> EditResult:
        """The regions from ``at_region`` on (reading order) become a new segment, right after
        the old one, for ``label`` (None: the unassigned tray)."""
        booklet, segments = self._load(college_id, booklet_id)
        old = _find(segments, segment_id)
        k = _index(old, at_region)
        if k == 0:
            raise InvariantError("a split needs at least one region before the split point")
        new = Segment(
            id=self._rt.new_id(SegmentId),
            college_id=college_id,
            booklet_id=booklet_id,
            slot_label=label,
            spans=old.spans,
            source=SegmentSource.TEACHER,
            region_ids=old.region_ids[k:],
            position=old.position + 1,
        )
        after: list[Segment] = []
        for s in segments:
            if s.id == segment_id:
                after += [replace(s, region_ids=s.region_ids[:k]), new]
            else:
                after.append(
                    replace(s, position=s.position + 1) if s.position > old.position else s
                )
        return self._apply(booklet, actor, segments, after, {segment_id, new.id}, "split")

    def reassign(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        segment_id: SegmentId,
        label: str | None,
    ) -> EditResult:
        """Give the segment another question (None: move it to the unassigned tray)."""
        booklet, segments = self._load(college_id, booklet_id)
        _find(segments, segment_id)
        after = [replace(s, slot_label=label) if s.id == segment_id else s for s in segments]
        return self._apply(booklet, actor, segments, after, {segment_id}, "reassign")

    def move_boundary(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        upper: SegmentId,
        lower: SegmentId,
        region: RegionId,
    ) -> EditResult:
        """Move the boundary between two segments next to each other in written order so that
        ``lower`` starts at ``region`` (a region of either)."""
        booklet, segments = self._load(college_id, booklet_id)
        a, b = _find(segments, upper), _find(segments, lower)
        if b.position != a.position + 1:
            raise InvariantError("only the boundary between neighbouring segments can move")
        combined = a.region_ids + b.region_ids
        if region not in combined:
            raise NotFoundError(f"region {region} is in neither segment")
        k = combined.index(region)
        if k == 0 or k == len(combined):
            raise InvariantError("both segments must keep at least one region")
        after = [
            replace(s, region_ids=combined[:k])
            if s.id == upper
            else replace(s, region_ids=combined[k:])
            if s.id == lower
            else s
            for s in segments
        ]
        return self._apply(booklet, actor, segments, after, {upper, lower}, "move_boundary")

    def replace_all(
        self,
        college_id: CollegeId,
        actor: UserId,
        booklet_id: BookletId,
        proposed: Sequence[Segment],
    ) -> EditResult:
        """Re-segment: the machine's new proposal replaces every segment, the teacher's edits
        included. Answers keep their ids; those whose text changed are returned to re-score."""
        booklet, before = self._load(college_id, booklet_id)
        return self._apply(booklet, actor, before, proposed, set(), "resegment")

    # --- shared -----------------------------------------------------------------------------

    def _load(self, college_id: CollegeId, booklet_id: BookletId) -> tuple[Booklet, list[Segment]]:
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status not in EDITABLE:
            raise InvariantError(f"segments cannot be edited while the booklet is {booklet.status}")
        return booklet, sorted(
            self._booklets.segments(college_id, booklet_id), key=lambda s: s.position
        )

    def _pages(self, booklet: Booklet) -> list[PageLines]:
        inputs = booklet_inputs(self._booklets, booklet.college_id, booklet.id)
        pages = {p.id: p for p in self._booklets.pages(booklet.college_id, booklet.id)}
        laid = [page_lines(p) for p in inputs]

        def order(item: PageLines) -> int:
            page = pages[item.page.page_id]
            return page.index if page.reading_order is None else page.reading_order

        return sorted(laid, key=order)

    def _apply(
        self,
        booklet: Booklet,
        actor: UserId,
        before: Sequence[Segment],
        after: Sequence[Segment],
        touched: set[SegmentId],
        operation: str,
    ) -> EditResult:
        college_id = booklet.college_id
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        known = {label for slot in blueprint.slots() for label, _, _ in leaves(slot)}
        pages = self._pages(booklet)
        counts: dict[str, int] = {}
        for s in after:
            if s.slot_label is not None:
                if s.slot_label not in known:
                    raise InvariantError(f"the paper has no question {s.slot_label!r}")
                counts[s.slot_label] = counts.get(s.slot_label, 0) + 1

        final: list[Segment] = []
        for position, s in enumerate(sorted(after, key=lambda s: s.position)):
            flags: list[SegmentFlag] = [f for f in s.flags if f is not SegmentFlag.DUPLICATE]
            if s.id in touched:
                flags = []
            if s.slot_label is not None and counts[s.slot_label] > 1:
                flags.append(SegmentFlag.DUPLICATE)
            changes: dict[str, object] = {"position": position, "flags": tuple(flags)}
            if s.id in touched:
                region_ids, spans = spans_for(set(s.region_ids), pages)
                if not spans:
                    raise InvariantError("a segment must keep at least one region of the booklet")
                changes |= {
                    "region_ids": region_ids,
                    "spans": spans,
                    "source": SegmentSource.TEACHER,
                    "match_score": None,
                    "proposed_label": None,
                }
            final.append(replace(s, **changes))  # type: ignore[arg-type]

        existing = {a.slot_label: a for a in self._booklets.answers(college_id, booklet.id)}
        rebuilt = answers_for(booklet, final, existing, self._rt)
        old_text = _contents(before)
        new_text = _contents(final)
        rescore = [a for label, a in rebuilt.items() if new_text[label] != old_text.get(label)]
        emptied = [a for label, a in existing.items() if label not in rebuilt and a.segment_ids]
        for answer in (*rescore, *emptied):
            stored = existing.get(answer.slot_label)
            if stored is not None and stored.status is AnswerStatus.APPROVED:
                raise InvariantError(
                    f"answer {answer.slot_label} is approved: reopen it as an amendment first"
                )
        if self._check is not None:
            self._check(booklet, [*rescore, *emptied])

        kept = {s.id for s in final}
        for s in before:
            if s.id not in kept:
                self._booklets.delete_segment(college_id, s.id)
        previous = {s.id: s for s in before}
        for s in final:
            if previous.get(s.id) != s:
                self._booklets.save_segment(college_id, s)
        for answer in rescore:
            self._booklets.save_answer(college_id, answer)
        for answer in emptied:
            if self._scores.scores(college_id, answer.id):
                self._booklets.save_answer(
                    college_id, replace(answer, segment_ids=(), version=answer.version + 1)
                )
            else:
                self._booklets.delete_answer(college_id, answer.id)
        for label, answer in rebuilt.items():
            if answer not in rescore and existing.get(label) != answer:
                self._booklets.save_answer(college_id, answer)  # order of segments changed

        self._rt.record(
            college_id,
            actor,
            AuditAction.SEGMENT_EDITED,
            booklet_id=booklet.id,
            before=_summary(before, touched or {s.id for s in before}),
            after={"operation": operation, **_summary(final, touched or {s.id for s in final})},
        )
        return EditResult(
            segments=tuple(final),
            rescore=tuple(a.id for a in rescore),
            emptied=tuple(a.id for a in emptied),
        )


def _find(segments: Sequence[Segment], segment_id: SegmentId) -> Segment:
    for s in segments:
        if s.id == segment_id:
            return s
    raise NotFoundError(f"segment {segment_id}")


def _index(segment: Segment, region: RegionId) -> int:
    if region not in segment.region_ids:
        raise NotFoundError(f"region {region} is not in segment {segment.id}")
    return segment.region_ids.index(region)


def _contents(segments: Sequence[Segment]) -> dict[str, tuple[RegionId, ...]]:
    """What each question's answer consists of: its regions, in written order."""
    found: dict[str, tuple[RegionId, ...]] = {}
    for s in sorted(segments, key=lambda s: s.position):
        if s.slot_label is not None:
            found[s.slot_label] = found.get(s.slot_label, ()) + s.region_ids
    return found


def _summary(segments: Sequence[Segment], touched: set[SegmentId]) -> dict[str, JsonValue]:
    """Ids, questions and sizes only: no text."""
    return {
        "segments": [
            {"id": str(s.id), "question": s.slot_label, "regions": len(s.region_ids)}
            for s in segments
            if s.id in touched
        ]
    }
