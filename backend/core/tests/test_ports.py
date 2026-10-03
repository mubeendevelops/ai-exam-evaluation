"""Every port has an in-memory adapter that conforms to it (checked by mypy through the
annotated assignments below) and behaves sensibly."""

from decimal import Decimal
from uuid import UUID

import pytest

from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box, college_blob_key
from tarn_core.domain.content import Question
from tarn_core.domain.diagram import DiagramGraph, DiagramNode, NodeShape
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.ports import (
    AuditSink,
    BlobStore,
    BookletRepository,
    Clock,
    CollegeRepository,
    ContentRepository,
    DetectedRegion,
    DiagramRecognizer,
    Embedder,
    IdGenerator,
    JobQueue,
    LayoutDetector,
    OcrEngine,
    PageImage,
    PageSource,
    ResultSheetRepository,
    Scorer,
    ScoreRepository,
    StudentRepository,
    UserRepository,
)
from tarn_core.testing import (
    FixedCreditScorer,
    HashEmbedder,
    InMemory,
    ScriptedDiagramRecognizer,
    ScriptedLayoutDetector,
    ScriptedOcrEngine,
)
from tarn_core.testing.builders import add_college, ci_shaped_blueprint

COLLEGE = CollegeId(UUID(int=1))
BOX = Box(x0=0, y0=0, x1=10, y1=10)


def test_in_memory_adapters_conform_to_ports() -> None:
    mem = InMemory()
    graph = DiagramGraph(nodes=(DiagramNode(id="n1", shape=NodeShape.PROCESS),))
    adapters: tuple[object, ...] = ()
    content: ContentRepository = mem.content
    colleges: CollegeRepository = mem.colleges
    users: UserRepository = mem.users
    students: StudentRepository = mem.students
    booklets: BookletRepository = mem.booklets
    scores: ScoreRepository = mem.scores
    sheets: ResultSheetRepository = mem.sheets
    blobs: BlobStore = mem.blobs
    pages: PageSource = mem.page_source
    clock: Clock = mem.clock
    ids: IdGenerator = mem.ids
    jobs: JobQueue = mem.jobs
    audit: AuditSink = mem.audit
    layout: LayoutDetector = ScriptedLayoutDetector(
        [DetectedRegion(kind=RegionKind.TEXT_LINE, box=BOX)]
    )
    ocr: OcrEngine = ScriptedOcrEngine("tesseract", ["hello"])
    embedder: Embedder = HashEmbedder()
    scorer: Scorer = FixedCreditScorer()
    recognizer: DiagramRecognizer = ScriptedDiagramRecognizer(graph)
    adapters = (
        content, colleges, users, students, booklets, scores, sheets, blobs, pages,
        clock, ids, jobs, audit, layout, ocr, embedder, scorer, recognizer,
    )  # fmt: skip
    assert all(a is not None for a in adapters)


def test_blob_store_round_trip() -> None:
    store = InMemory().blobs
    key = college_blob_key(COLLEGE, "booklet", "b", "p1.png")
    store.put(key, b"png", "image/png")
    assert store.exists(key) and store.get(key) == b"png"
    store.delete(key)
    with pytest.raises(NotFoundError):
        store.get(key)


def test_page_source_returns_pages_in_order() -> None:
    source = InMemory().page_source
    booklet = BookletId(UUID(int=2))
    source.add(COLLEGE, booklet, PageImage(index=1, data=b"2", media_type="image/jpeg"))
    source.add(COLLEGE, booklet, PageImage(index=0, data=b"1", media_type="image/jpeg"))
    assert [p.data for p in source.pages(COLLEGE, booklet)] == [b"1", b"2"]
    with pytest.raises(NotFoundError):
        source.pages(CollegeId(UUID(int=3)), booklet)


def test_engines_are_deterministic() -> None:
    ocr = ScriptedOcrEngine("trocr", ["first", "second"], confidence=0.7)
    readings = ocr.read(b"img", [BOX, BOX, BOX])
    assert [r.text for r in readings] == ["first", "second", "first"]
    assert all(r.engine == ocr.ref for r in readings)
    embedder = HashEmbedder(dimension=4)
    a, b, c = embedder.embed(["same", "same", "other"])
    assert a == b != c
    assert abs(sum(x * x for x in a) - 1) < 1e-9


def test_clock_ids_and_queue() -> None:
    mem = InMemory()
    start = mem.clock.now()
    mem.clock.advance(minutes=5)
    assert (mem.clock.now() - start).total_seconds() == 300
    assert mem.ids.new() != mem.ids.new()
    job = mem.jobs.enqueue(COLLEGE, "process_booklet", {"booklet_id": "b"})
    assert mem.jobs.jobs[0].id == job and mem.jobs.jobs[0].college_id == COLLEGE


def test_content_repository_versions_are_append_only() -> None:
    mem = InMemory()
    question_id = ci_shaped_blueprint(mem, add_college(mem, "A")).leaf("1")[1]
    question = mem.content.get(Question, question_id)
    with pytest.raises(InvariantError, match="expected version 2"):
        mem.content.save(question)  # same (id, version) again
    with pytest.raises(NotFoundError):
        mem.content.get(Question, question_id, version=2)
    assert question.max_marks == Decimal(2)
