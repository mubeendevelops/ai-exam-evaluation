"""Re-segmenting a reviewed booklet on the teacher's request (P16 "Re-Segment").

The segmenter runs again over the booklet's current text (the teacher's corrections included)
and its proposal replaces every segment, the teacher's own edits among them: the screen asks
before it queues this. Answers keep their ids; those whose text changed are re-scored. It runs
in the worker (the segmenter needs the embedder), as a job queued by the API under the booklet
lock; the job stands down when the booklet's version has moved on since the request."""

from dataclasses import dataclass, replace

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, Segment
from tarn_core.ids import AnswerId, BookletId, CollegeId, SegmentId, UserId
from tarn_core.ports.engines import Embedder
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services._support import Runtime
from tarn_core.services.segmentation.edits import RESEGMENTABLE, SegmentEditor
from tarn_core.services.segmentation.segmenter import SegmentationPolicy, Segmenter
from tarn_core.services.segmentation.service import booklet_inputs, question_texts
from tarn_core.services.workflow.rescore import RescoreRequests


@dataclass(frozen=True, slots=True, kw_only=True)
class ResegmentResult:
    booklet: Booklet
    rescoring: tuple[AnswerId, ...]
    emptied: tuple[AnswerId, ...]


class BookletResegmenter:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        embedder: Embedder,
        runtime: Runtime,
        rescore: RescoreRequests,
        policy: SegmentationPolicy | None = None,
    ) -> None:
        self._booklets = booklets
        self._content = content
        self._embedder = embedder
        self._rt = runtime
        self._rescore = rescore
        self._policy = policy or SegmentationPolicy()
        self._editor = SegmentEditor(
            booklets=booklets, scores=scores, content=content, runtime=runtime
        )

    def run(
        self, college_id: CollegeId, actor: UserId, booklet_id: BookletId, *, expected_version: int
    ) -> ResegmentResult | None:
        """None: nothing was done (the booklet moved on since the request)."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.version != expected_version or booklet.status not in RESEGMENTABLE:
            return None
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        segmenter = Segmenter(
            blueprint, question_texts(self._content, blueprint), self._embedder, self._policy
        )
        result = segmenter.segment(booklet_inputs(self._booklets, college_id, booklet_id))
        position = {page_id: k for k, page_id in enumerate(result.order.order)}
        for page in self._booklets.pages(college_id, booklet_id):
            self._booklets.save_page(
                college_id,
                replace(
                    page,
                    written_number=result.order.written.get(page.id),
                    reading_order=position[page.id],
                ),
            )
        proposed = [
            Segment(
                id=self._rt.new_id(SegmentId),
                college_id=college_id,
                booklet_id=booklet_id,
                slot_label=p.slot_label,
                spans=p.spans,
                source=p.source,
                match_score=p.match_score,
                region_ids=p.region_ids,
                flags=p.flags,
                position=p.position,
                proposed_label=p.proposed_label,
            )
            for p in result.segments
            if p.spans
        ]
        done = self._editor.replace_all(college_id, actor, booklet_id, proposed)
        booklet = self._booklets.get(college_id, booklet_id)
        booklet = replace(booklet, version=booklet.version + 1)
        self._booklets.save(college_id, booklet)
        self._rescore.request(college_id, actor, done.rescore)
        return ResegmentResult(booklet=booklet, rescoring=done.rescore, emptied=done.emptied)
