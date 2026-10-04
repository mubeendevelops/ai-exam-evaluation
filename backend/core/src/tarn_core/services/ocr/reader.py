"""Reading a booklet's cleaned pages (design.md "OCR framework (R4)", "Reliability").

``PageOcr`` reads one image: an orientation check (the page as it is against the page turned
180°, keeping the one the OCR reads with more confidence; this also settles the direction of
the quarter turns P9 could only guess, D63), the layout, every configured engine (one engine
failing or timing out does not stop the others), alignment onto the detected lines, the line's
content class and the selector. It needs no repository, so the CLI uses it on plain files.

``BookletReader.step`` reads one page per call and is safe to repeat, so the worker runs every
page in its own transaction and a crash resumes at the first unread page:
PAGES_READY → READING (first step) → … → TEXT_READY (last step, which queues segmentation)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import UUID

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import (
    Booklet,
    BookletStatus,
    LineReading,
    Page,
    Region,
    RegionKind,
)
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.content import Glossary, ReferenceAnswer
from tarn_core.domain.ocr import ContentClass, SelectorSettings
from tarn_core.errors import EngineTimeoutError, InvariantError, NotFoundError
from tarn_core.ids import BookletId, CollegeId, QuestionId, RegionId
from tarn_core.ports.engines import (
    CalibrationStore,
    LayoutDetector,
    OcrEngine,
    PageTransform,
    WordList,
)
from tarn_core.ports.jobs import JobQueue
from tarn_core.ports.repositories import BookletRepository, ContentRepository
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime
from tarn_core.services.ocr.align import AlignedLine, align
from tarn_core.services.ocr.selector import (
    Calibrations,
    Lexicon,
    LineChoice,
    classify_line,
    select,
)
from tarn_core.services.pipeline import booklet_key
from tarn_core.services.segmentation.service import queue_segmenting

FAILED_READING = "reading_failed"

_LINE_KINDS = (RegionKind.TEXT_LINE, RegionKind.LABEL)


@dataclass(frozen=True, slots=True, kw_only=True)
class OrientationPolicy:
    """When to turn a page by 180°: the turned page must read better by ``margin`` (mean raw
    confidence, weighted by text length) with at least ``min_chars`` characters read.

    The probe finds the lines once on the page (downscaled to ``probe_edge`` if larger) and
    reads the ``probe_lines`` widest of them as they are and turned 180° (the same boxes, mapped
    onto the turned copy): two short reads instead of reading the whole page twice."""

    enabled: bool = True
    engine: str = "paddle"
    """The engine that probes; a fast local engine that reads print and handwriting."""
    probe_edge: int = 2400
    """The cleaned pages are at most 2200 px, so by default the probe sees the whole page: on a
    1400 px copy the detector found no line at all on some scanned sample pages (P10)."""
    probe_lines: int = 8
    margin: float = 0.1
    min_chars: int = 40


@dataclass(frozen=True, slots=True, kw_only=True)
class OrientationCheck:
    turned: bool
    decided: bool
    """Enough text was read to judge (False: the page was left as it is, undecided)."""
    upright_quality: float
    turned_quality: float


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadLine:
    """One region of a read page, before it gets ids."""

    kind: RegionKind
    box: Box
    readings: tuple[LineReading, ...] = ()
    choice: LineChoice | None = None
    content_class: ContentClass | None = None
    parent: int | None = None
    """Index of the table region (in the same list) this cell belongs to."""
    row: int | None = None
    col: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PageText:
    regions: tuple[ReadLine, ...]
    engines: tuple[str, ...]
    """Engines that read the page."""
    failures: tuple[str, ...]
    """``engine:timeout`` or ``engine:error`` for each engine that failed."""

    @property
    def needs_text(self) -> bool:
        """Every engine failed (or none is configured): nothing was read."""
        return not self.engines

    @property
    def lines(self) -> tuple[ReadLine, ...]:
        return tuple(r for r in self.regions if r.choice is not None)


class PageOcr:
    def __init__(
        self,
        *,
        layout: LayoutDetector,
        engines: Mapping[str, OcrEngine],
        transform: PageTransform,
        settings: SelectorSettings,
        calibrations: Calibrations,
        orientation: OrientationPolicy | None = None,
    ) -> None:
        for name, engine in engines.items():
            if engine.ref.name != name:
                raise InvariantError(f"engine {engine.ref.name!r} configured as {name!r}")
        self._layout = layout
        self._engines = dict(engines)
        self._transform = transform
        self._settings = settings
        self._calibrations = calibrations
        self._orientation = orientation or OrientationPolicy()

    @property
    def engine_names(self) -> tuple[str, ...]:
        """The engines that will read, in configured order."""
        return tuple(n for n in self._settings.all_engines if n in self._engines)

    # -- orientation -----------------------------------------------------------------------

    def check_orientation(self, image: bytes) -> OrientationCheck:
        policy = self._orientation
        probe = self._engines.get(policy.engine)
        if not policy.enabled or probe is None:
            return OrientationCheck(
                turned=False, decided=False, upright_quality=0.0, turned_quality=0.0
            )
        small = self._transform.downscale(image, policy.probe_edge)
        try:
            width, height = self._transform.size(small)
            # Text lines and table cells alike: a ruled answer page may be laid out as a table.
            lines = [
                box
                for r in self._layout.detect(small)
                for box in ([r.box] if r.kind in _LINE_KINDS else [c.box for c in r.cells])
            ]
            lines = sorted(lines, key=lambda b: b.x1 - b.x0, reverse=True)[: policy.probe_lines]
            mapped = [
                Box(x0=width - b.x1, y0=height - b.y1, x1=width - b.x0, y1=height - b.y0)
                for b in lines
            ]
            upright, upright_chars = self._probe(probe, small, lines)
            turned, turned_chars = self._probe(probe, self._transform.rotate(small, 180), mapped)
        except Exception:  # a failing probe leaves the page as it is
            return OrientationCheck(
                turned=False, decided=False, upright_quality=0.0, turned_quality=0.0
            )
        decided = max(upright_chars, turned_chars) >= policy.min_chars
        return OrientationCheck(
            turned=decided and turned - upright >= policy.margin,
            decided=decided,
            upright_quality=round(upright, 4),
            turned_quality=round(turned, 4),
        )

    def _probe(self, engine: OcrEngine, image: bytes, lines: Sequence[Box]) -> tuple[float, int]:
        if not lines:
            return 0.0, 0
        readings = engine.read(image, lines)
        chars = sum(len(r.text.strip()) for r in readings)
        if chars == 0:
            return 0.0, 0
        return sum(r.confidence * len(r.text.strip()) for r in readings) / chars, chars

    # -- reading ---------------------------------------------------------------------------

    def read(self, image: bytes, lexicon: Lexicon) -> PageText:
        """Every region in the detector's reading order (a table followed by its cells), then
        any line only an engine's own layout found."""
        plan: list[ReadLine | int] = []  # a region that is not read, or the index of a line box
        boxes: list[Box] = []
        slots: list[tuple[RegionKind, int | None, int | None, int | None]] = []
        for region in self._layout.detect(image):
            if region.kind in _LINE_KINDS:
                plan.append(len(boxes))
                boxes.append(region.box)
                slots.append((region.kind, None, None, None))
            elif region.kind is RegionKind.TABLE:
                table_step = len(plan)
                plan.append(ReadLine(kind=RegionKind.TABLE, box=region.box))
                for cell in sorted(region.cells, key=lambda c: (c.row, c.col)):
                    plan.append(len(boxes))
                    boxes.append(cell.box)
                    slots.append((RegionKind.TEXT_LINE, table_step, cell.row, cell.col))
            else:
                plan.append(ReadLine(kind=region.kind, box=region.box))

        found: dict[str, Sequence[LineReading]] = {}
        failures: list[str] = []
        for name in self.engine_names:
            engine = self._engines[name]
            try:
                readings = engine.read(image, boxes) if boxes else ()
            except EngineTimeoutError:
                failures.append(f"{name}:timeout")
                continue
            except Exception:  # any engine failure: carry on with the others
                failures.append(f"{name}:error")
                continue
            found[name] = [_named(r, engine.ref) for r in readings]

        lines = align(boxes, found)
        plan += list(range(len(boxes), len(lines)))  # extra lines at the end
        regions: list[ReadLine] = []
        position: dict[int, int] = {}  # plan index of a table → its index in ``regions``
        for step, entry in enumerate(plan):
            if isinstance(entry, ReadLine):
                position[step] = len(regions)
                regions.append(entry)
                continue
            kind, table, row, col = (
                slots[entry] if entry < len(slots) else (RegionKind.TEXT_LINE, None, None, None)
            )
            parent = None if table is None else position[table]
            regions.append(self._line(kind, lines[entry], lexicon, parent, row, col))
        return PageText(regions=tuple(regions), engines=tuple(found), failures=tuple(failures))

    def _line(
        self,
        kind: RegionKind,
        line: AlignedLine,
        lexicon: Lexicon,
        parent: int | None,
        row: int | None,
        col: int | None,
    ) -> ReadLine:
        order = {name: i for i, name in enumerate(self.engine_names)}
        readings = tuple(
            line.readings[name] for name in sorted(line.readings, key=order.__getitem__)
        )
        if not readings:
            return ReadLine(kind=kind, box=line.box, parent=parent, row=row, col=col)
        content_class = classify_line(readings, self._settings)
        return ReadLine(
            kind=kind,
            box=line.box,
            readings=readings,
            choice=select(
                readings,
                content_class,
                settings=self._settings,
                calibrations=self._calibrations,
                lexicon=lexicon,
            ),
            content_class=content_class,
            parent=parent,
            row=row,
            col=col,
        )


def _named(reading: LineReading, ref: EngineRef) -> LineReading:
    """Readings carry the engine that produced them, whatever the adapter put there."""
    return reading if reading.engine == ref else replace(reading, engine=ref)


def booklet_lexicon(
    content: ContentRepository, booklet: Booklet, word_list: WordList | None
) -> Lexicon:
    """Glossary terms and key vocabulary of every question in the booklet's pinned exam, plus
    the English word list. The line's own question is not known before segmentation (P12)."""
    blueprint = content.get(ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version)
    return exam_lexicon(content, blueprint, word_list)


def exam_lexicon(
    content: ContentRepository, blueprint: ExamBlueprint, word_list: WordList | None
) -> Lexicon:
    """The lexicon of ``booklet_lexicon`` for an exam without a booklet (benchmarks, CLI)."""
    question_ids: list[QuestionId] = []
    for slot in blueprint.slots():
        question_ids += [qid for _, qid, _ in leaves(slot) if qid is not None]
    terms: set[str] = set()
    for qid in dict.fromkeys(question_ids):
        for glossary in content.for_question(Glossary, qid):
            terms.update(glossary.terms)
        for answer in content.for_question(ReferenceAnswer, qid):
            if not answer.guidance_only:
                terms.add(answer.text)
    return Lexicon(frozenset(terms), word_list)


class BookletReader:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        content: ContentRepository,
        blobs: BlobStore,
        layout: LayoutDetector,
        engines: Mapping[str, OcrEngine],
        transform: PageTransform,
        calibrations: CalibrationStore,
        word_list: WordList | None,
        runtime: Runtime,
        settings: SelectorSettings | None = None,
        orientation: OrientationPolicy | None = None,
        jobs: JobQueue | None = None,
    ) -> None:
        self._jobs = jobs
        self._booklets = booklets
        self._content = content
        self._blobs = blobs
        self._layout = layout
        self._engines = engines
        self._transform = transform
        self._calibration_store = calibrations
        self._words = word_list
        self._rt = runtime
        self._settings = settings or SelectorSettings()
        self._orientation = orientation or OrientationPolicy()
        self._lexicons: dict[UUID, Lexicon] = {}

    def _ocr(self) -> PageOcr:
        fitted = {(c.engine, c.content_class): c for c in self._calibration_store.latest()}
        return PageOcr(
            layout=self._layout,
            engines=self._engines,
            transform=self._transform,
            settings=self._settings,
            calibrations=fitted,
            orientation=self._orientation,
        )

    def step(self, college_id: CollegeId, booklet_id: BookletId) -> bool:
        """One unit of work; True when there is nothing more to do for this booklet."""
        booklet = self._booklets.get(college_id, booklet_id)
        if booklet.status not in (BookletStatus.PAGES_READY, BookletStatus.READING):
            return True
        if booklet.status is BookletStatus.PAGES_READY:
            booklet = self._save(booklet, status=BookletStatus.READING)
        pages = self._booklets.pages(college_id, booklet_id)
        pending = next((p for p in pages if not p.text_read), None)
        if pending is not None:
            self._read(booklet, pending)
            return False
        self._finish(booklet, pages)
        return True

    def abandon(self, college_id: CollegeId, booklet_id: BookletId) -> None:
        """The job ran out of attempts: end the booklet as FAILED so it stops waiting."""
        abandon_reading(self._booklets, self._rt, college_id, booklet_id)

    def _lexicon(self, booklet: Booklet) -> Lexicon:
        if booklet.id not in self._lexicons:
            try:
                self._lexicons[booklet.id] = booklet_lexicon(self._content, booklet, self._words)
            except NotFoundError:
                self._lexicons[booklet.id] = Lexicon(frozenset(), self._words)
        return self._lexicons[booklet.id]

    def _read(self, booklet: Booklet, page: Page) -> None:
        ocr = self._ocr()
        image = self._blobs.get(page.image)
        check = ocr.check_orientation(image)
        if check.turned:
            image = self._transform.rotate(image, 180)
            key = booklet_key(
                booklet.college_id, booklet.id, "clean", f"{page.index + 1:03d}-turned.jpg"
            )
            self._blobs.put(key, image, "image/jpeg")
            page = replace(page, image=key)
        if page.metrics is not None and (check.turned or check.decided):
            rotation = (page.metrics.rotation_degrees + (180 if check.turned else 0)) % 360
            page = replace(
                page,
                metrics=replace(page.metrics, rotation_degrees=rotation, rotation_guessed=False),
            )
        text = ocr.read(image, self._lexicon(booklet))
        ids: list[RegionId] = []
        regions: list[Region] = []
        for line in text.regions:
            region_id = self._rt.new_id(RegionId)
            ids.append(region_id)
            choice = line.choice
            regions.append(
                Region(
                    id=region_id,
                    college_id=booklet.college_id,
                    page_id=page.id,
                    kind=line.kind,
                    box=line.box,
                    readings=line.readings,
                    chosen=None if choice is None else choice.chosen,
                    content_class=line.content_class,
                    scores=() if choice is None else choice.scores,
                    line_score=None if choice is None else choice.line_score,
                    flagged=line.kind in _LINE_KINDS and (choice is None or choice.flagged),
                    calibrations=() if choice is None else choice.calibrations,
                    parent_id=None if line.parent is None else ids[line.parent],
                    row=line.row,
                    col=line.col,
                )
            )
        self._booklets.replace_regions(booklet.college_id, page.id, regions)
        self._booklets.save_page(
            booklet.college_id,
            replace(
                page,
                text_read=True,
                needs_text=text.needs_text,
                ocr_failures=text.failures,
            ),
        )

    def _finish(self, booklet: Booklet, pages: Sequence[Page]) -> None:
        lines = flagged = 0
        for page in pages:
            for region in self._booklets.regions(booklet.college_id, page.id):
                if region.kind in _LINE_KINDS:
                    lines += 1
                    flagged += region.flagged
        booklet = self._save(booklet, status=BookletStatus.TEXT_READY)
        if self._jobs is not None:
            queue_segmenting(self._jobs, booklet.college_id, booklet.id)
        self._rt.record(
            booklet.college_id,
            None,
            AuditAction.BOOKLET_TEXT_READ,
            booklet_id=booklet.id,
            after={
                "pages": len(pages),
                "lines": lines,
                "flagged_lines": flagged,
                "needs_text_pages": [p.index + 1 for p in pages if p.needs_text],
                "engine_failures": {
                    str(p.index + 1): list(p.ocr_failures) for p in pages if p.ocr_failures
                },
                "engines": list(self._ocr().engine_names),
                "layout": f"{self._layout.ref.name} {self._layout.ref.version}",
                "selector": {
                    "alpha": self._settings.alpha,
                    "beta": self._settings.beta,
                    "flag_threshold": self._settings.flag_threshold,
                },
            },
        )

    def _save(self, booklet: Booklet, **changes: object) -> Booklet:
        updated = replace(booklet, version=booklet.version + 1, **changes)  # type: ignore[arg-type]
        self._booklets.save(booklet.college_id, updated)
        return updated


def abandon_reading(
    booklets: BookletRepository, runtime: Runtime, college_id: CollegeId, booklet_id: BookletId
) -> None:
    """End a booklet whose reading job ran out of attempts as FAILED (``reading_failed``)."""
    booklet = booklets.get(college_id, booklet_id)
    if booklet.status not in (BookletStatus.PAGES_READY, BookletStatus.READING):
        return
    booklets.save(
        college_id,
        replace(
            booklet,
            status=BookletStatus.FAILED,
            failure_reason=FAILED_READING,
            version=booklet.version + 1,
        ),
    )
    runtime.record(
        college_id,
        None,
        AuditAction.BOOKLET_FAILED,
        booklet_id=booklet_id,
        after={"reason": FAILED_READING},
    )
