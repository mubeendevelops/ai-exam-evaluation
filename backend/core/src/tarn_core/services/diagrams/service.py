"""Diagram recognition in the pipeline and the teacher's graph edits (U6 Q20).

**References.** An uploaded PNG queues ``diagram.reference``; the worker reads it (shapes and
arrows by the recognizer of the diagram's kind, labels by the best-of-N OCR) and stores the
graph as the next version of the reference diagram (``recognised``; ``failed`` when it cannot
be read). The teacher of the owning college may then edit the graph or change the kind (next
version, ``edited``). The question's glossary takes the reference labels each time (U6 Q21).
Scores record the reference version they used; re-scoring after a reference change is P15's
key-change rule.

**Student drawings.** Segmentation queues ``booklet.diagrams``. One step per diagram region
(one transaction each): a diagram region inside an answer to a question with a diagram
criterion is recognised with the recognizer of that reference's kind, its labels are the
page's text regions inside it (already read best-of-N by P10), and it is stored as a
``StudentDiagram``. A region the recognizer cannot read, or with no recognizer for the kind, is
stored with an empty graph: its criteria go to the teacher, who may draw the graph. When no
region is left, scoring is queued. The status stays ``segmented`` throughout.

Audit events carry ids, versions and counts, never labels (student text)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import AnswerStatus, BookletStatus, Page, Region, RegionKind
from tarn_core.domain.common import ContentRef, JsonValue
from tarn_core.domain.content import (
    DiagramParams,
    Glossary,
    Question,
    ReferenceDiagram,
    RubricCriterion,
)
from tarn_core.domain.diagram import (
    DiagramDetection,
    DiagramGraph,
    DiagramKind,
    DiagramText,
    RecognitionState,
    StudentDiagram,
)
from tarn_core.errors import EngineFailedError, InvariantError, NotFoundError
from tarn_core.ids import (
    AnswerId,
    BookletId,
    CollegeId,
    QuestionId,
    ReferenceDiagramId,
    SegmentId,
    StudentDiagramId,
    UserId,
)
from tarn_core.ports.engines import DiagramRecognizer
from tarn_core.ports.jobs import JobQueue
from tarn_core.ports.repositories import BookletRepository, ContentRepository
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.content import ensure_can_edit
from tarn_core.services.diagrams.build import GraphBuilder, centre, inside
from tarn_core.services.diagrams.editor import GraphEdit, apply_edits
from tarn_core.services.diagrams.jobs import queue_diagrams, queue_reference_recognition
from tarn_core.services.ocr.reader import PageText
from tarn_core.services.ocr.selector import Lexicon
from tarn_core.services.scoring.booklet import AnswerApprovedError, queue_scoring

FAILED_DIAGRAMS = "diagrams_failed"
_TEXT_KINDS = frozenset({RegionKind.LABEL, RegionKind.TEXT_LINE})


class LabelReader(Protocol):
    """Reads the text lines of an image best-of-N (``PageOcr``)."""

    @property
    def engine_names(self) -> tuple[str, ...]: ...

    def read(self, image: bytes, lexicon: Lexicon) -> PageText: ...


class GlossarySync(Protocol):
    """Refreshes a question's glossary from its reference diagrams' labels."""

    def refresh_reference_labels(
        self, college_id: CollegeId, actor_id: UserId, question_id: QuestionId
    ) -> object: ...


class StaleGraphError(InvariantError):
    """The graph changed since the teacher loaded it: reload and edit again."""


type Recognizers = Mapping[DiagramKind, DiagramRecognizer]


def page_texts(text: PageText) -> list[DiagramText]:
    """The read lines of an image as diagram texts (chosen reading, line score)."""
    out: list[DiagramText] = []
    for line in text.lines:
        if line.choice is None or line.choice.chosen is None:
            continue
        reading = line.readings[line.choice.chosen].text.strip()
        if reading:
            out.append(DiagramText(text=reading, box=line.box, confidence=line.choice.line_score))
    return out


class ReferenceDiagrams:
    def __init__(
        self,
        *,
        content: ContentRepository,
        blobs: BlobStore,
        runtime: Runtime,
        recognizers: Recognizers,
        labels: LabelReader | None,
        glossary: GlossarySync,
        builder: GraphBuilder | None = None,
    ) -> None:
        self._content = content
        self._blobs = blobs
        self._rt = runtime
        self._recognizers = recognizers
        self._labels = labels
        self._glossary = glossary
        self._builder = builder or GraphBuilder()

    def recognize(
        self, college_id: CollegeId, actor_id: UserId, diagram_id: ReferenceDiagramId
    ) -> ReferenceDiagram:
        """Read the PNG into the next version's graph. A recognizer failure is stored as
        ``failed`` (the teacher draws the graph), not raised."""
        diagram = self._content.get(ReferenceDiagram, diagram_id)
        recognizer = self._recognizers.get(diagram.kind)
        state = RecognitionState.FAILED
        graph = DiagramGraph(nodes=())
        if recognizer is not None:
            image = self._blobs.get(diagram.png)
            try:
                detection = recognizer.recognize(image)
            except EngineFailedError:
                detection = None
            if detection is not None:
                texts, engines = self._read_labels(image, diagram.question_id)
                graph = self._builder.build(
                    detection, texts, recognizer=recognizer.ref, label_engines=engines
                )
                state = RecognitionState.RECOGNISED
        new = replace(
            diagram,
            meta=replace(diagram.meta, version=diagram.meta.version + 1, created_by=actor_id),
            graph=graph,
            recognition=state,
        )
        self._content.save(new)
        self._audit(college_id, actor_id, diagram, new, action="recognised")
        self._glossary.refresh_reference_labels(college_id, actor_id, diagram.question_id)
        return new

    def edit(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        diagram_id: ReferenceDiagramId,
        *,
        expected_version: int,
        edits: Sequence[GraphEdit] = (),
        kind: DiagramKind | None = None,
    ) -> ReferenceDiagram:
        """The teacher's corrections (owning college only), as the next version."""
        diagram = self._content.get(ReferenceDiagram, diagram_id)
        ensure_can_edit(college_id, self._content.get(Question, diagram.question_id))
        if diagram.meta.version != expected_version:
            raise StaleGraphError("the reference graph changed since it was loaded")
        if not edits and (kind is None or kind is diagram.kind):
            raise InvariantError("nothing to change")
        graph = apply_edits(diagram.graph, edits) if edits else diagram.graph
        new = replace(
            diagram,
            meta=replace(diagram.meta, version=diagram.meta.version + 1, created_by=actor_id),
            graph=graph,
            kind=kind or diagram.kind,
            recognition=RecognitionState.EDITED if edits else diagram.recognition,
        )
        self._content.save(new)
        self._audit(college_id, actor_id, diagram, new, action="edited", edits=len(edits))
        self._glossary.refresh_reference_labels(college_id, actor_id, diagram.question_id)
        return new

    def _read_labels(
        self, image: bytes, question_id: QuestionId
    ) -> tuple[list[DiagramText], tuple[str, ...]]:
        if self._labels is None:
            return [], ()
        terms = frozenset(
            t for g in self._content.for_question(Glossary, question_id) for t in g.teacher_terms
        )
        text = self._labels.read(image, Lexicon(terms))
        return page_texts(text), text.engines

    def _audit(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        before: ReferenceDiagram,
        after: ReferenceDiagram,
        *,
        action: str,
        edits: int | None = None,
    ) -> None:
        detail: dict[str, JsonValue] = {
            **_ref(after.ref),
            "diagram": action,
            "kind": after.kind.value,
            "recognition": after.recognition.value,
            "nodes": len(after.graph.nodes),
            "edges": len(after.graph.edges),
        }
        if edits is not None:
            detail["edits"] = edits
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_EDITED, before=_ref(before.ref), after=detail
        )


class BookletDiagrams:
    """The ``booklet.diagrams`` stage."""

    def __init__(
        self,
        *,
        booklets: BookletRepository,
        content: ContentRepository,
        blobs: BlobStore,
        runtime: Runtime,
        jobs: JobQueue,
        recognizers: Recognizers,
        builder: GraphBuilder | None = None,
    ) -> None:
        self._booklets = booklets
        self._content = content
        self._blobs = blobs
        self._rt = runtime
        self._jobs = jobs
        self._recognizers = recognizers
        self._builder = builder or GraphBuilder()

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        """Recognise the next diagram region; True when none is left (scoring is queued)."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status is not BookletStatus.SEGMENTED:
            return True
        pending = self._pending(college_id, booklet_id)
        if not pending:
            queue_scoring(self._jobs, college_id, booklet_id)
            return True
        self._recognize(college_id, booklet_id, pending[0])
        return False

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status is not BookletStatus.SEGMENTED:
            return
        self._booklets.save(
            college_id,
            replace(
                booklet,
                status=BookletStatus.FAILED,
                failure_reason=FAILED_DIAGRAMS,
                version=booklet.version + 1,
            ),
        )
        self._rt.record(
            college_id,
            None,
            AuditAction.BOOKLET_FAILED,
            booklet_id=booklet_id,
            after={"reason": FAILED_DIAGRAMS},
        )

    def _pending(self, college_id: CollegeId, booklet_id: BookletId) -> list["_Pending"]:
        booklet = self._booklets.get(college_id, booklet_id)
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        done = {d.region_id for d in self._booklets.diagrams(college_id, booklet_id)}
        pages = {p.id: p for p in self._booklets.pages(college_id, booklet_id)}
        kinds: dict[str, DiagramKind | None] = {}
        out: list[_Pending] = []
        regions_of: dict[object, Sequence[Region]] = {}
        for segment in self._booklets.segments(college_id, booklet_id):
            if segment.slot_label is None:
                continue
            if segment.slot_label not in kinds:
                kinds[segment.slot_label] = self._kind_for(blueprint, segment.slot_label)
            kind = kinds[segment.slot_label]
            if kind is None:
                continue
            wanted = set(segment.region_ids)
            for page_id in segment.page_ids:
                if page_id not in regions_of:
                    regions_of[page_id] = self._booklets.regions(college_id, page_id)
                for region in regions_of[page_id]:
                    if (
                        region.id in wanted
                        and region.kind is RegionKind.DIAGRAM
                        and region.id not in done
                    ):
                        out.append(
                            _Pending(
                                segment_id=segment.id,
                                region=region,
                                page=pages[page_id],
                                page_regions=regions_of[page_id],
                                kind=kind,
                            )
                        )
        return out

    def _kind_for(self, blueprint: ExamBlueprint, slot_label: str) -> DiagramKind | None:
        """The kind of the first reference diagram a diagram criterion of the slot's question
        points at; None when the question has no diagram criterion."""
        try:
            _, question_id, _ = blueprint.leaf(slot_label)
        except (InvariantError, KeyError):
            return None
        for criterion in self._content.for_question(RubricCriterion, question_id):
            if isinstance(criterion.params, DiagramParams):
                ref = self._content.get(ReferenceDiagram, criterion.params.reference_diagram_id)
                return ref.kind
        return None

    def _recognize(
        self,
        college_id: CollegeId,
        booklet_id: BookletId,
        item: "_Pending",
    ) -> None:
        region, page_regions, kind = item.region, item.page_regions, item.kind
        recognizer = self._recognizers.get(kind)
        graph = DiagramGraph(nodes=())
        outcome = "unavailable"
        if recognizer is not None:
            image = self._blobs.get(item.page.image)
            detection: DiagramDetection | None
            try:
                detection = recognizer.recognize(image, region.box)
            except EngineFailedError:
                detection = None
                outcome = "failed"
            if detection is not None:
                texts = [
                    DiagramText(text=r.text or "", box=r.box, confidence=r.line_score)
                    for r in page_regions
                    if r.kind in _TEXT_KINDS
                    and r.text
                    and not r.struck_out
                    and inside(centre(r.box), region.box)
                ]
                engines = tuple(
                    dict.fromkeys(
                        e for r in page_regions for e in r.read_by if r.kind in _TEXT_KINDS
                    )
                )
                graph = self._builder.build(
                    detection,
                    texts,
                    recognizer=recognizer.ref,
                    label_engines=engines,
                    region=region.box,
                )
                outcome = "recognised"
        diagram = StudentDiagram(
            id=self._rt.new_id(StudentDiagramId),
            college_id=college_id,
            booklet_id=booklet_id,
            segment_id=item.segment_id,
            box=region.box,
            graph=graph,
            region_id=region.id,
            kind=kind,
        )
        self._booklets.save_diagram(college_id, diagram)
        self._rt.record(
            college_id,
            None,
            AuditAction.DIAGRAM_RECOGNISED,
            booklet_id=booklet_id,
            after={
                "diagram_id": str(diagram.id),
                "outcome": outcome,
                "kind": kind.value,
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
            },
        )


@dataclass(frozen=True, slots=True)
class _Pending:
    segment_id: SegmentId
    region: Region
    page: Page
    page_regions: Sequence[Region]
    kind: DiagramKind


class StudentDiagrams:
    """The teacher's edits of a recognised student drawing; the answer is re-scored."""

    def __init__(
        self,
        *,
        booklets: BookletRepository,
        runtime: Runtime,
        rescore: "Rescorer",
    ) -> None:
        self._booklets = booklets
        self._rt = runtime
        self._rescore = rescore

    def edit(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        booklet_id: BookletId,
        diagram_id: StudentDiagramId,
        *,
        expected_version: int,
        edits: Sequence[GraphEdit],
    ) -> StudentDiagram:
        diagram = next(
            (d for d in self._booklets.diagrams(college_id, booklet_id) if d.id == diagram_id),
            None,
        )
        if diagram is None:
            raise NotFoundError(f"diagram {diagram_id}")
        if diagram.version != expected_version:
            raise StaleGraphError("the diagram changed since it was loaded")
        answers = [
            a
            for a in self._booklets.answers(college_id, booklet_id)
            if diagram.segment_id in a.segment_ids
        ]
        if any(a.status is AnswerStatus.APPROVED for a in answers):
            raise AnswerApprovedError("an approved answer changes only through an amendment")
        new = replace(diagram, graph=apply_edits(diagram.graph, edits), version=diagram.version + 1)
        self._booklets.save_diagram(college_id, new)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.DIAGRAM_EDITED,
            booklet_id=booklet_id,
            before={"diagram_id": str(diagram.id), "version": diagram.version},
            after={
                "diagram_id": str(new.id),
                "version": new.version,
                "edits": len(edits),
                "nodes": len(new.graph.nodes),
                "edges": len(new.graph.edges),
            },
        )
        self._rescore.rescore(college_id, actor_id, [a.id for a in answers])
        return new


class Rescorer(Protocol):
    def rescore(
        self, college_id: CollegeId, actor_id: UserId | None, answer_ids: Sequence[AnswerId]
    ) -> object: ...


def _ref(ref: ContentRef) -> dict[str, JsonValue]:
    return {"kind": ref.kind.value, "id": str(ref.id), "version": ref.version}


__all__ = [
    "BookletDiagrams",
    "ReferenceDiagrams",
    "StaleGraphError",
    "StudentDiagrams",
    "page_texts",
    "queue_diagrams",
    "queue_reference_recognition",
]
