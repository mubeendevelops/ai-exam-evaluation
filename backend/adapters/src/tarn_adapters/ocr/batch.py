"""OCR without a database or a queue: ``tarn ocr read`` (clean and read files page by page) and
the ground-truth reading behind ``tarn ocr calibrate``.

The read report holds counts, scores and timings only. Recognised text is student data: it is
written only when asked (``--text``), to a folder the operator chooses, never printed."""

import json
import time
from collections.abc import Collection, Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from tarn_adapters.imaging.batch import _pages
from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
from tarn_adapters.imaging.pdf import PyMuPdfSplitter
from tarn_adapters.ocr.images import crop, decode, encode_jpeg
from tarn_adapters.ocr.wiring import OcrSetup
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.domain.ocr import ContentClass, EngineCalibration
from tarn_core.errors import InvariantError
from tarn_core.services.ocr.calibration import GroundTruthSample
from tarn_core.services.ocr.reader import OrientationCheck, PageOcr, PageText
from tarn_core.services.ocr.selector import Lexicon


@dataclass(frozen=True, slots=True)
class PageRead:
    booklet: int
    """Position of the file among those given (1-based); names stay out of the report."""
    page: int
    rotation_degrees: int
    """The cleaner's quarter turns plus the OCR's 180° check."""
    turned: bool
    orientation_decided: bool
    upright_quality: float
    turned_quality: float
    lines: int
    flagged: int
    tables: int
    diagrams: int
    classes: dict[str, int]
    engines: tuple[str, ...]
    failures: tuple[str, ...]
    mean_line_score: float | None
    seconds: float

    def line(self) -> str:
        turn = (
            "turned 180"
            if self.turned
            else ("upright" if self.orientation_decided else "undecided")
        )
        score = "n/a" if self.mean_line_score is None else f"{self.mean_line_score:.2f}"
        failures = f"  FAILED:{','.join(self.failures)}" if self.failures else ""
        return (
            f"booklet {self.booklet:>2} page {self.page:>2}  rot {self.rotation_degrees:>3} "
            f"{turn:<10} (q {self.upright_quality:.2f}/{self.turned_quality:.2f})  "
            f"lines {self.lines:>3} flagged {self.flagged:>3}  score {score}  "
            f"tables {self.tables} diagrams {self.diagrams}  {self.seconds:.1f}s{failures}"
        )


def _summary(
    booklet: int,
    page: int,
    rotation: int,
    check_turned: bool,
    decided: bool,
    qualities: tuple[float, float],
    text: PageText,
    seconds: float,
) -> PageRead:
    lines = text.lines
    classes: dict[str, int] = {}
    for line in lines:
        if line.content_class is not None:
            classes[line.content_class.value] = classes.get(line.content_class.value, 0) + 1
    scores = [
        ln.choice.line_score for ln in lines if ln.choice and ln.choice.line_score is not None
    ]
    return PageRead(
        booklet=booklet,
        page=page,
        rotation_degrees=rotation,
        turned=check_turned,
        orientation_decided=decided,
        upright_quality=qualities[0],
        turned_quality=qualities[1],
        lines=len(lines),
        flagged=sum(1 for ln in lines if ln.choice is not None and ln.choice.flagged),
        tables=sum(1 for r in text.regions if r.kind is RegionKind.TABLE),
        diagrams=sum(1 for r in text.regions if r.kind is RegionKind.DIAGRAM),
        classes=classes,
        engines=text.engines,
        failures=text.failures,
        mean_line_score=round(sum(scores) / len(scores), 3) if scores else None,
        seconds=round(seconds, 2),
    )


def page_ocr(setup: OcrSetup, calibrations: Sequence[EngineCalibration] = ()) -> PageOcr:
    return PageOcr(
        layout=setup.layout,
        engines=setup.engines,
        transform=setup.transform,
        settings=setup.settings,
        calibrations={(c.engine, c.content_class): c for c in calibrations},
        orientation=setup.orientation,
    )


@dataclass(frozen=True, slots=True)
class ReadPage:
    """One page cleaned, oriented and read: the image the engines saw and what they read."""

    index: int
    """0-based position in the file."""
    image: bytes
    text: PageText
    check: OrientationCheck
    rotation_degrees: int
    seconds: float


def read_pages(
    data: bytes,
    setup: OcrSetup,
    ocr: PageOcr,
    lexicon: Lexicon,
    *,
    max_pages: int = 60,
    only: Collection[int] | None = None,
    splitter: PyMuPdfSplitter | None = None,
    cleaner: OpenCvPageCleaner | None = None,
) -> Iterator[ReadPage]:
    """Split a PDF (or take an image), then for each page (or each index of ``only``, 0-based)
    clean it, settle its orientation and read it. Shared by ``tarn ocr read`` and the
    ground-truth pre-fill."""
    splitter = splitter or PyMuPdfSplitter()
    cleaner = cleaner or OpenCvPageCleaner()
    for image in _pages(data, splitter, max_pages):
        if only is not None and image.index not in only:
            continue
        started = time.perf_counter()
        cleaned = cleaner.clean(image.data)
        pixels = cleaned.image
        check = ocr.check_orientation(pixels)
        if check.turned:
            pixels = setup.transform.rotate(pixels, 180)
        rotation = (cleaned.metrics.rotation_degrees + (180 if check.turned else 0)) % 360
        text = ocr.read(pixels, lexicon)
        yield ReadPage(
            index=image.index,
            image=pixels,
            text=text,
            check=check,
            rotation_degrees=rotation,
            seconds=time.perf_counter() - started,
        )


def read_files(
    paths: Iterable[Path],
    setup: OcrSetup,
    *,
    text_dir: Path | None = None,
    max_pages: int = 60,
    calibrations: Sequence[EngineCalibration] = (),
) -> Iterator[PageRead]:
    """Clean every page of every file, then read it; with ``text_dir``, the chosen text of each
    line is written to ``text_dir/booklet-NN.json`` (student data: keep that folder local)."""
    splitter, cleaner = PyMuPdfSplitter(), OpenCvPageCleaner()
    ocr = page_ocr(setup, calibrations)
    lexicon = Lexicon(frozenset(), setup.word_list)
    for number, path in enumerate(paths, start=1):
        texts: list[dict[str, object]] = []
        for read in read_pages(
            path.read_bytes(),
            setup,
            ocr,
            lexicon,
            max_pages=max_pages,
            splitter=splitter,
            cleaner=cleaner,
        ):
            text = read.text
            yield _summary(
                number,
                read.index + 1,
                read.rotation_degrees,
                read.check.turned,
                read.check.decided,
                (read.check.upright_quality, read.check.turned_quality),
                text,
                read.seconds,
            )
            if text_dir is not None:
                texts.append(
                    {
                        "page": read.index + 1,
                        "lines": [
                            {
                                "box": [ln.box.x0, ln.box.y0, ln.box.x1, ln.box.y1],
                                "class": None
                                if ln.content_class is None
                                else ln.content_class.value,
                                "flagged": ln.choice.flagged if ln.choice else True,
                                "chosen": None
                                if not ln.choice or ln.choice.chosen is None
                                else ln.readings[ln.choice.chosen].text,
                                "readings": {
                                    r.engine.name: {
                                        "text": r.text,
                                        "confidence": round(r.confidence, 3),
                                    }
                                    for r in ln.readings
                                },
                            }
                            for ln in text.lines
                        ],
                    }
                )
        if text_dir is not None:
            text_dir.mkdir(parents=True, exist_ok=True)
            out = text_dir / f"booklet-{number:02d}.json"
            out.write_text(json.dumps(texts, ensure_ascii=False, indent=1), encoding="utf-8")
            out.chmod(0o600)


def write_report(reads: Iterable[PageRead], path: Path) -> None:
    path.write_text(json.dumps([asdict(r) for r in reads], indent=1), encoding="utf-8")


# --- ground truth for calibration -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TruthLine:
    image: Path
    box: Box | None
    text: str
    content_class: ContentClass


def read_manifest(path: Path) -> list[TruthLine]:
    """JSON lines: ``{"image": "...", "box": [x0, y0, x1, y1] (optional), "text": "...",
    "class": "print" | "cursive" | "numeric"}``; image paths relative to the file.

    ``path`` may also be a ground-truth folder (P11): every ``*.jsonl`` page file in it is read,
    and only the lines a person verified (the pre-filled and ignored ones are not truth)."""
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    lines = []
    for file in files:
        for number, raw in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
                if item.get("status", "verified") != "verified":
                    continue
                box = item.get("box")
                lines.append(
                    TruthLine(
                        image=(file.parent / item["image"]).resolve(),
                        box=None
                        if box is None
                        else Box(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                        text=str(item["text"]),
                        content_class=ContentClass(item["class"]),
                    )
                )
            except (
                KeyError,
                ValueError,
                TypeError,
                IndexError,
                AttributeError,
                InvariantError,
            ) as error:
                raise InvariantError(f"manifest line {number}: {error}") from None
    return lines


def read_ground_truth(lines: Sequence[TruthLine], setup: OcrSetup) -> list[GroundTruthSample]:
    """Every engine reads every ground-truth line (a line image, or a box of a page)."""
    samples: list[GroundTruthSample] = []
    for truth in lines:
        pixels = decode(truth.image.read_bytes())
        if truth.box is not None:
            piece = crop(pixels, truth.box)
            if piece is None:
                continue
            pixels = piece
        height, width = pixels.shape[:2]
        image = encode_jpeg(pixels)
        whole = Box(x0=0, y0=0, x1=width, y1=height)
        for name, engine in setup.engines.items():
            try:
                readings = engine.read(image, [whole])
            except Exception:  # noqa: S112  (an engine that fails here is not calibrated)
                continue
            text = " ".join(r.text for r in sorted(readings, key=lambda r: r.box.x0))
            confidence = (
                sum(r.confidence * max(1, len(r.text)) for r in readings)
                / sum(max(1, len(r.text)) for r in readings)
                if readings
                else 0.0
            )
            samples.append(
                GroundTruthSample(
                    engine=name,
                    engine_version=engine.ref.version,
                    content_class=truth.content_class,
                    confidence=min(1.0, max(0.0, confidence)),
                    text=text,
                    truth=truth.text,
                )
            )
    return samples
