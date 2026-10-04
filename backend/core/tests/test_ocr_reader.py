"""Reading a booklet's pages with every engine: the job queued when pages are ready, one page
per step, engines failing or timing out, pages nobody could read, the orientation check,
tables, the lexicon of the exam, cloud-style engines, resuming and isolation."""

from dataclasses import dataclass, replace

import pytest

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, BookletStatus, RegionKind
from tarn_core.domain.common import Box
from tarn_core.domain.content import ContentMeta, Glossary
from tarn_core.domain.ocr import ContentClass, EngineCalibration, SelectorSettings
from tarn_core.errors import EngineFailedError, EngineTimeoutError, NotFoundError
from tarn_core.ids import GlossaryId
from tarn_core.ports.engines import DetectedRegion, OcrEngine, TableCell
from tarn_core.ports.jobs import JOB_READ_BOOKLET
from tarn_core.services.ocr.reader import FAILED_READING, BookletReader, OrientationPolicy
from tarn_core.services.pipeline import PageDecisions, PagePipeline
from tarn_core.services.uploads import UploadLimits, UploadService
from tarn_core.testing import (
    FakePageCleaner,
    FakePageSplitter,
    FakePageTransform,
    InMemory,
    PageOcrEngine,
    ScriptedLayoutDetector,
    ScriptedOcrEngine,
    SetWordList,
    fake_image,
    fake_pdf,
)
from tarn_core.testing.builders import (
    CollegeFixture,
    add_college,
    ci_shaped_blueprint,
    make_services,
)

LINE_1 = Box(x0=50, y0=100, x1=900, y1=150)
LINE_2 = Box(x0=50, y0=170, x1=900, y1=220)
TWO_LINES = [DetectedRegion(kind=RegionKind.TEXT_LINE, box=b) for b in (LINE_1, LINE_2)]
ENGINES = ("trocr", "paddle", "tesseract")
SETTINGS = SelectorSettings(engine_sets=dict.fromkeys(ContentClass, ENGINES))


@dataclass
class World:
    mem: InMemory
    college: CollegeFixture
    other: CollegeFixture
    uploads: UploadService
    pipeline: PagePipeline
    decisions: PageDecisions
    transform: FakePageTransform
    blueprint_id: object

    def ready(self, *files: bytes) -> Booklet:
        booklet = self.uploads.upload(
            self.college.id,
            self.college.teacher.id,
            student_id=self.college.students[0].id,
            blueprint_id=self.blueprint_id,  # type: ignore[arg-type]
            files=list(files or [fake_pdf(2)]),
        ).booklet
        while not self.pipeline.step(self.college.id, booklet.id):
            pass
        return self.mem.booklets.get(self.college.id, booklet.id)

    def reader(
        self,
        engines: dict[str, OcrEngine] | None = None,
        *,
        regions: list[DetectedRegion] | None = None,
        orientation: OrientationPolicy | None = None,
        settings: SelectorSettings = SETTINGS,
        words: SetWordList | None = None,
    ) -> BookletReader:
        if engines is None:
            engines = {
                "trocr": ScriptedOcrEngine("trocr", ["first line", "second line"], 0.8),
                "paddle": ScriptedOcrEngine("paddle", ["first line", "second lime"], 0.7),
                "tesseract": ScriptedOcrEngine("tesseract", ["flrst 1ine", "secnd line"], 0.4),
            }
        return BookletReader(
            booklets=self.mem.booklets,
            content=self.mem.content,
            blobs=self.mem.blobs,
            layout=ScriptedLayoutDetector(TWO_LINES if regions is None else regions),
            engines=engines,
            transform=self.transform,
            calibrations=self.mem.calibrations,
            word_list=words,
            runtime=self.mem.runtime,
            settings=settings,
            orientation=orientation or OrientationPolicy(enabled=False),
        )

    def read_all(self, reader: BookletReader, booklet: Booklet) -> Booklet:
        for _ in range(100):
            if reader.step(self.college.id, booklet.id):
                return self.mem.booklets.get(self.college.id, booklet.id)
        raise AssertionError("the reader did not finish")

    def regions(self, booklet: Booklet, page: int = 0) -> list:  # type: ignore[type-arg]
        pages = self.mem.booklets.pages(self.college.id, booklet.id)
        return list(self.mem.booklets.regions(self.college.id, pages[page].id))


@pytest.fixture
def w() -> World:
    mem = InMemory()
    college = add_college(mem, "A")
    other = add_college(mem, "B")
    blueprint = ci_shaped_blueprint(mem, college)
    svc = make_services(mem)
    return World(
        mem=mem,
        college=college,
        other=other,
        uploads=UploadService(
            booklets=svc.booklets,
            repository=mem.booklets,
            blobs=mem.blobs,
            jobs=mem.jobs,
            runtime=mem.runtime,
            limits=UploadLimits(max_waiting=5),
        ),
        pipeline=PagePipeline(
            booklets=mem.booklets,
            blobs=mem.blobs,
            splitter=FakePageSplitter(),
            cleaner=FakePageCleaner(),
            runtime=mem.runtime,
            jobs=mem.jobs,
        ),
        decisions=PageDecisions(booklets=mem.booklets, runtime=mem.runtime, jobs=mem.jobs),
        transform=FakePageTransform(),
        blueprint_id=blueprint.id,
    )


# --- the queue --------------------------------------------------------------------------------


def test_pages_ready_queues_the_reading_job_once(w: World) -> None:
    booklet = w.ready()
    assert booklet.status is BookletStatus.PAGES_READY
    reads = [j for j in w.mem.jobs.jobs if j.kind == JOB_READ_BOOKLET]
    assert len(reads) == 1
    assert reads[0].payload == {"booklet_id": str(booklet.id)}
    assert reads[0].college_id == w.college.id


def test_a_retake_queues_nothing_until_the_last_use_anyway(w: World) -> None:
    booklet = w.ready(fake_image("blur"), fake_image("glare"))
    assert booklet.status is BookletStatus.NEEDS_RETAKE
    assert not [j for j in w.mem.jobs.jobs if j.kind == JOB_READ_BOOKLET]
    w.decisions.use_anyway(w.college.id, w.college.teacher.id, booklet.id, 0)
    assert not [j for j in w.mem.jobs.jobs if j.kind == JOB_READ_BOOKLET]
    w.decisions.use_anyway(w.college.id, w.college.teacher.id, booklet.id, 1)
    assert len([j for j in w.mem.jobs.jobs if j.kind == JOB_READ_BOOKLET]) == 1


# --- reading ----------------------------------------------------------------------------------


def test_one_page_per_step_then_text_ready(w: World) -> None:
    booklet = w.ready(fake_pdf(3))
    reader = w.reader()
    assert reader.step(w.college.id, booklet.id) is False
    assert w.mem.booklets.get(w.college.id, booklet.id).status is BookletStatus.READING
    pages = w.mem.booklets.pages(w.college.id, booklet.id)
    assert [p.text_read for p in pages] == [True, False, False]
    done = w.read_all(reader, booklet)
    assert done.status is BookletStatus.TEXT_READY
    assert reader.step(w.college.id, booklet.id) is True  # nothing more to do
    (event,) = [e for e in w.mem.audit.events if e.action is AuditAction.BOOKLET_TEXT_READ]
    assert event.actor_id is None
    assert event.after["pages"] == 3  # type: ignore[index,call-overload]
    assert event.after["lines"] == 6  # type: ignore[index,call-overload]
    assert event.after["engines"] == list(ENGINES)  # type: ignore[index,call-overload]


def test_every_reading_is_kept_and_the_best_one_chosen(w: World) -> None:
    booklet = w.read_all(w.reader(), w.ready(fake_pdf(1)))
    first, second = w.regions(booklet)
    assert first.read_by == ENGINES
    assert first.text == "first line"
    assert second.text == "second line"
    assert len(first.scores) == 3
    assert first.scores[first.chosen].score == max(s.score for s in first.scores)
    assert first.content_class is ContentClass.CURSIVE
    assert first.flagged is False


def test_an_engine_failing_and_one_timing_out_do_not_stop_the_others(w: World) -> None:
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["only me"], 0.9),
        "paddle": ScriptedOcrEngine("paddle", ["x"], fail=EngineFailedError("down")),
        "tesseract": ScriptedOcrEngine("tesseract", ["x"], fail=EngineTimeoutError("slow")),
    }
    booklet = w.read_all(w.reader(engines), w.ready(fake_pdf(1)))
    (page,) = w.mem.booklets.pages(w.college.id, booklet.id)
    assert page.ocr_failures == ("paddle:error", "tesseract:timeout")
    assert page.needs_text is False
    regions = w.regions(booklet)
    assert all(r.read_by == ("trocr",) for r in regions)
    assert regions[0].text == "only me"
    event = next(e for e in w.mem.audit.events if e.action is AuditAction.BOOKLET_TEXT_READ)
    assert event.after["engine_failures"] == {"1": ["paddle:error", "tesseract:timeout"]}  # type: ignore[index,call-overload]


def test_an_unexpected_engine_error_is_contained_too(w: World) -> None:
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["fine"], 0.9),
        "paddle": ScriptedOcrEngine("paddle", ["x"], fail=RuntimeError("bug in a library")),
    }
    booklet = w.read_all(w.reader(engines), w.ready(fake_pdf(1)))
    (page,) = w.mem.booklets.pages(w.college.id, booklet.id)
    assert page.ocr_failures == ("paddle:error",)


def test_a_page_no_engine_could_read_needs_text(w: World) -> None:
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["x"], fail=EngineTimeoutError("slow")),
        "paddle": ScriptedOcrEngine("paddle", ["x"], fail=EngineFailedError("down")),
    }
    booklet = w.read_all(w.reader(engines), w.ready(fake_pdf(2)))
    assert booklet.status is BookletStatus.TEXT_READY  # the teacher types those pages
    pages = w.mem.booklets.pages(w.college.id, booklet.id)
    assert all(p.needs_text for p in pages)
    regions = w.regions(booklet)
    assert [r.box for r in regions] == [LINE_1, LINE_2]  # the lines are there to type into
    assert all(r.readings == () and r.flagged for r in regions)
    event = next(e for e in w.mem.audit.events if e.action is AuditAction.BOOKLET_TEXT_READ)
    assert event.after["needs_text_pages"] == [1, 2]  # type: ignore[index,call-overload]


def test_a_crash_resumes_at_the_first_unread_page(w: World) -> None:
    booklet = w.ready(fake_pdf(3))
    trocr = ScriptedOcrEngine("trocr", ["text"], 0.9)
    reader = w.reader({"trocr": trocr})
    reader.step(w.college.id, booklet.id)
    reader.step(w.college.id, booklet.id)
    assert trocr.calls == 2
    revived_engine = ScriptedOcrEngine("trocr", ["text"], 0.9)
    w.read_all(w.reader({"trocr": revived_engine}), booklet)
    assert revived_engine.calls == 1  # only the third page


def test_reading_a_page_again_replaces_its_regions(w: World) -> None:
    booklet = w.read_all(w.reader(), w.ready(fake_pdf(1)))
    (page,) = w.mem.booklets.pages(w.college.id, booklet.id)
    w.mem.booklets.save_page(w.college.id, replace(page, text_read=False))
    w.mem.booklets.save(
        w.college.id, replace(booklet, status=BookletStatus.READING, version=booklet.version + 1)
    )
    w.read_all(w.reader(), booklet)
    assert len(w.regions(booklet)) == 2


def test_the_reader_leaves_other_statuses_alone(w: World) -> None:
    booklet = w.ready(fake_image("blur"))
    assert w.reader().step(w.college.id, booklet.id) is True
    assert w.mem.booklets.get(w.college.id, booklet.id).status is BookletStatus.NEEDS_RETAKE


def test_abandon_fails_the_booklet(w: World) -> None:
    booklet = w.ready()
    w.reader().abandon(w.college.id, booklet.id)
    failed = w.mem.booklets.get(w.college.id, booklet.id)
    assert failed.status is BookletStatus.FAILED and failed.failure_reason == FAILED_READING


def test_another_college_cannot_read_the_booklet(w: World) -> None:
    booklet = w.ready()
    with pytest.raises(NotFoundError):
        w.reader().step(w.other.id, booklet.id)
    w.mem.log.calls.clear()
    w.read_all(w.reader(), booklet)
    assert w.mem.log.colleges == {w.college.id}


# --- selection inputs -------------------------------------------------------------------------


def test_the_exams_glossary_steers_the_choice(w: World) -> None:
    booklet = w.ready(fake_pdf(1))
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["mitochondria"], 0.6),
        "paddle": ScriptedOcrEngine("paddle", ["mitochondira"], 0.6),
    }
    no_glossary = w.read_all(w.reader(engines, regions=TWO_LINES[:1]), booklet)
    first = w.regions(no_glossary)[0]
    assert first.scores[0].lexicon == 0.0
    # A glossary on one of the exam's questions: the term is now known.
    blueprint = w.mem.content.get(ExamBlueprint, w.blueprint_id)  # type: ignore[arg-type]
    question_id = next(blueprint.slots()).question_id
    assert question_id is not None
    w.mem.content.save(
        Glossary(
            id=GlossaryId(w.mem.ids.new()),
            meta=ContentMeta(owning_college_id=w.college.id, created_by=w.college.teacher.id),
            question_id=question_id,
            teacher_terms=("Mitochondria",),
        )
    )
    again = w.ready(fake_pdf(1, "again"))
    read = w.read_all(w.reader(engines, regions=TWO_LINES[:1]), again)
    region = w.regions(read)[0]
    assert region.text == "mitochondria"
    assert region.scores[0].lexicon == 1.0 and region.scores[1].lexicon == 0.0


def test_the_english_word_list_counts(w: World) -> None:
    engines: dict[str, OcrEngine] = {"trocr": ScriptedOcrEngine("trocr", ["the cat"], 0.9)}
    booklet = w.read_all(
        w.reader(engines, regions=TWO_LINES[:1], words=SetWordList(["the", "cat"])),
        w.ready(fake_pdf(1)),
    )
    assert w.regions(booklet)[0].scores[0].lexicon == 1.0


def test_fitted_calibrations_are_used_and_recorded(w: World) -> None:
    calibration = EngineCalibration(engine="paddle", content_class=ContentClass.CURSIVE, weight=0.0)
    w.mem.calibrations.save(calibration)
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["handwritten"], 0.5),
        "paddle": ScriptedOcrEngine("paddle", ["handwrltten"], 0.99),
    }
    booklet = w.read_all(w.reader(engines, regions=TWO_LINES[:1]), w.ready(fake_pdf(1)))
    region = w.regions(booklet)[0]
    assert region.text == "handwritten"  # paddle's weight 0 for cursive
    assert region.calibrations == (calibration.ref,)


def test_a_cloud_engine_is_aligned_onto_our_lines_and_adds_lines_we_missed(w: World) -> None:
    missed = Box(x0=50, y0=400, x1=900, y1=450)
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["first line", "second line"], 0.8),
        "azure": PageOcrEngine(
            "azure",
            [
                ("first", Box(x0=60, y0=105, x1=300, y1=145), 0.9),
                ("line", Box(x0=320, y0=105, x1=500, y1=145), 0.9),
                ("an extra line", missed, 0.95),
            ],
        ),
    }
    settings = SelectorSettings(engine_sets=dict.fromkeys(ContentClass, ("trocr", "azure")))
    booklet = w.read_all(w.reader(engines, settings=settings), w.ready(fake_pdf(1)))
    regions = w.regions(booklet)
    assert [r.read_by for r in regions] == [("trocr", "azure"), ("trocr",), ("azure",)]
    assert regions[0].readings[1].text == "first line"
    assert regions[0].readings[1].box == LINE_1
    assert regions[2].box == missed and regions[2].text == "an extra line"


def test_tables_keep_rows_and_columns(w: World) -> None:
    table_box = Box(x0=50, y0=300, x1=900, y1=500)
    cells = tuple(
        TableCell(
            row=r,
            col=c,
            box=Box(x0=50 + c * 400, y0=300 + r * 100, x1=440 + c * 400, y1=390 + r * 100),
        )
        for r in range(2)
        for c in range(2)
    )
    layout = [
        TWO_LINES[0],
        DetectedRegion(kind=RegionKind.TABLE, box=table_box, cells=cells),
        DetectedRegion(kind=RegionKind.DIAGRAM, box=Box(x0=50, y0=600, x1=500, y1=900)),
    ]
    engines: dict[str, OcrEngine] = {
        "trocr": ScriptedOcrEngine("trocr", ["title", "a1", "b1", "a2", "b2"], 0.9)
    }
    booklet = w.read_all(w.reader(engines, regions=layout), w.ready(fake_pdf(1)))
    regions = w.regions(booklet)
    kinds = [r.kind for r in regions]
    assert kinds.count(RegionKind.TABLE) == 1 and kinds.count(RegionKind.DIAGRAM) == 1
    table = next(r for r in regions if r.kind is RegionKind.TABLE)
    grid = {(r.row, r.col): r.text for r in regions if r.parent_id == table.id}
    assert grid == {(0, 0): "a1", (0, 1): "b1", (1, 0): "a2", (1, 1): "b2"}
    assert (
        next(r for r in regions if r.kind is RegionKind.TEXT_LINE and r.parent_id is None).text
        == "title"
    )
    assert not table.flagged and table.readings == ()


# --- orientation ------------------------------------------------------------------------------


def probe(
    turned_confidence: float, text: str = "a long enough line of written text"
) -> ScriptedOcrEngine:
    return ScriptedOcrEngine("paddle", [text], 0.5, turned_confidence=turned_confidence)


def test_an_upside_down_page_is_turned(w: World) -> None:
    booklet = w.ready(fake_pdf(1))
    reader = w.reader({"paddle": probe(0.9)}, orientation=OrientationPolicy(min_chars=10))
    done = w.read_all(reader, booklet)
    (page,) = w.mem.booklets.pages(w.college.id, done.id)
    assert page.image.value.endswith("/clean/001-turned.jpg")
    assert w.mem.blobs.get(page.image).startswith(b"turned180:")
    assert page.metrics is not None and page.metrics.rotation_degrees == 180
    # The text was read from the turned page (the probe engine reads it at 0.9).
    assert w.regions(done)[0].readings[0].confidence == 0.9


def test_an_upright_page_stays_and_a_guessed_quarter_turn_is_confirmed(w: World) -> None:
    booklet = w.ready(fake_image("guess"))
    reader = w.reader({"paddle": probe(0.2)}, orientation=OrientationPolicy(min_chars=10))
    done = w.read_all(reader, booklet)
    (page,) = w.mem.booklets.pages(w.college.id, done.id)
    assert "turned" not in page.image.value
    assert page.metrics is not None
    assert page.metrics.rotation_degrees == 90 and page.metrics.rotation_guessed is False


def test_a_wrongly_guessed_quarter_turn_becomes_the_other_one(w: World) -> None:
    booklet = w.ready(fake_image("guess"))
    reader = w.reader({"paddle": probe(0.95)}, orientation=OrientationPolicy(min_chars=10))
    (page,) = w.mem.booklets.pages(w.college.id, w.read_all(reader, booklet).id)
    assert page.metrics is not None
    assert page.metrics.rotation_degrees == 270 and page.metrics.rotation_guessed is False


def test_a_small_gain_or_too_little_text_turns_nothing(w: World) -> None:
    booklet = w.ready(fake_image("guess"))
    small_gain = w.reader(
        {"paddle": probe(0.55)}, orientation=OrientationPolicy(min_chars=10, margin=0.1)
    )
    (page,) = w.mem.booklets.pages(w.college.id, w.read_all(small_gain, booklet).id)
    assert "turned" not in page.image.value
    assert page.metrics is not None and page.metrics.rotation_guessed is False  # decided: upright
    other = w.ready(fake_image("guess2"))
    sparse = w.reader(
        {"paddle": probe(0.99, text="ab")}, orientation=OrientationPolicy(min_chars=10)
    )
    (page,) = w.mem.booklets.pages(w.college.id, w.read_all(sparse, other).id)
    assert "turned" not in page.image.value
    assert page.metrics is not None and page.metrics.rotation_guessed is True  # undecided


def test_a_failing_probe_leaves_the_page_as_it_is(w: World) -> None:
    booklet = w.ready(fake_pdf(1))
    engines: dict[str, OcrEngine] = {
        "paddle": ScriptedOcrEngine("paddle", ["x"], fail=EngineFailedError("down")),
        "trocr": ScriptedOcrEngine("trocr", ["read anyway"], 0.9),
    }
    done = w.read_all(w.reader(engines, orientation=OrientationPolicy()), booklet)
    (page,) = w.mem.booklets.pages(w.college.id, done.id)
    assert "turned" not in page.image.value
    assert w.regions(done)[0].text == "read anyway"


def test_the_probe_reads_only_the_widest_lines_upright_and_turned(w: World) -> None:
    from tarn_core.services.ocr.reader import PageOcr

    seen: list[tuple[bool, list[Box]]] = []

    class Recording(ScriptedOcrEngine):
        def read(self, image: bytes, lines):  # type: ignore[no-untyped-def]
            seen.append((image.startswith(b"turned180:"), list(lines)))
            return super().read(image, lines)

    lines = [
        DetectedRegion(
            kind=RegionKind.TEXT_LINE,
            box=Box(x0=10, y0=10 + 60 * i, x1=110 + 50 * i, y1=50 + 60 * i),
        )
        for i in range(10)
    ]
    ocr = PageOcr(
        layout=ScriptedLayoutDetector(lines),
        engines={"paddle": Recording("paddle", ["some written text"], 0.6, turned_confidence=0.9)},
        transform=w.transform,
        settings=SETTINGS,
        calibrations={},
        orientation=OrientationPolicy(probe_lines=3, min_chars=10),
    )
    check = ocr.check_orientation(b"page")
    assert check.turned and check.decided
    (upright, boxes), (turned, mapped) = seen
    assert not upright and turned
    assert [b.x1 - b.x0 for b in boxes] == [550, 500, 450]  # the three widest
    assert mapped[0] == Box(x0=10_000 - 560, y0=10_000 - 590, x1=10_000 - 10, y1=10_000 - 550)


def test_the_probe_also_reads_table_cells(w: World) -> None:
    # A ruled answer page that the layout model takes for a table: its lines are cells.
    cells = tuple(
        TableCell(row=r, col=0, box=Box(x0=50, y0=100 + 60 * r, x1=900, y1=150 + 60 * r))
        for r in range(4)
    )
    table = [
        DetectedRegion(kind=RegionKind.TABLE, box=Box(x0=40, y0=90, x1=910, y1=400), cells=cells)
    ]
    reader = w.reader(
        {"paddle": probe(0.9)}, regions=table, orientation=OrientationPolicy(min_chars=10)
    )
    done = w.read_all(reader, w.ready(fake_pdf(1)))
    (page,) = w.mem.booklets.pages(w.college.id, done.id)
    assert page.image.value.endswith("-turned.jpg")  # decided (and turned), not undecided
