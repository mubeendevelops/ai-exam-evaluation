"""Collect a benchmark run: every engine reads every ground-truth line box, the layout detector
runs on every page, and each step is timed. The arithmetic is in ``tarn_core.services.ocr.
benchmark``; this module only runs the real engines (``tarn bench ocr``).

Engines read the person's boxes, not the detector's: recognition is measured apart from line
detection (which is reported separately as coverage)."""

import contextlib
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import replace

from tarn_adapters.config import Settings
from tarn_adapters.ocr.wiring import OcrSetup
from tarn_core.domain.booklet import LineReading, RegionKind
from tarn_core.domain.common import Box
from tarn_core.domain.groundtruth import TruthLine, TruthPage, TruthStatus
from tarn_core.errors import EngineTimeoutError
from tarn_core.ports.groundtruth import GroundTruthStore
from tarn_core.services.ocr.align import align, iou
from tarn_core.services.ocr.benchmark import BenchLine, BenchRun, DeviceTiming, PageStats

_LINE_KINDS = (RegionKind.TEXT_LINE, RegionKind.LABEL)
WARM_LINES = 2


def _active(page: TruthPage) -> list[TruthLine]:
    return [ln for ln in page.lines if ln.status is not TruthStatus.IGNORED]


def engine_order(setup: OcrSetup) -> list[str]:
    return [n for n in setup.settings.all_engines if n in setup.engines]


def _warm_up(setup: OcrSetup, image: bytes, boxes: Sequence[Box]) -> None:
    """One untimed read per engine so that model loading is not counted as reading time."""
    with contextlib.suppress(Exception):  # a failing layout shows up in the timed pass
        setup.layout.detect(image)
    for name in engine_order(setup):
        with contextlib.suppress(Exception):  # so does a failing engine
            setup.engines[name].read(image, list(boxes[:WARM_LINES]))


def _layout(
    setup: OcrSetup, image: bytes, boxes: Sequence[Box]
) -> tuple[float, int, int, int, int]:
    """Seconds, detected lines, truth lines covered, tables, truth lines inside a table."""
    started = time.perf_counter()
    regions = setup.layout.detect(image)
    seconds = time.perf_counter() - started
    detected: list[Box] = []
    tables: list[Box] = []
    for region in regions:
        if region.kind in _LINE_KINDS:
            detected.append(region.box)
        elif region.kind is RegionKind.TABLE:
            tables.append(region.box)
            detected += [c.box for c in region.cells]
    matched = sum(1 for box in boxes if any(iou(box, d) >= 0.5 for d in detected))
    inside = 0
    for box in boxes:
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        inside += any(t.x0 <= cx <= t.x1 and t.y0 <= cy <= t.y1 for t in tables)
    return seconds, len(detected), matched, len(tables), inside


def _read_page(
    setup: OcrSetup, image: bytes, boxes: Sequence[Box]
) -> tuple[dict[str, float], list[dict[str, LineReading]], list[str]]:
    """Every engine's seconds, the readings aligned to the boxes, and the failures."""
    seconds: dict[str, float] = {}
    found: dict[str, Sequence[LineReading]] = {}
    failures: list[str] = []
    for name in engine_order(setup):
        engine = setup.engines[name]
        started = time.perf_counter()
        try:
            readings = engine.read(image, list(boxes))
        except EngineTimeoutError:
            failures.append(f"{name}:timeout")
            continue
        except Exception:  # any failure: the others carry on, as in the worker
            failures.append(f"{name}:error")
            continue
        finally:
            seconds[name] = time.perf_counter() - started
        found[name] = [
            r if r.engine == engine.ref else replace(r, engine=engine.ref) for r in readings
        ]
    aligned = align(list(boxes), found)
    return seconds, [dict(a.readings) for a in aligned[: len(boxes)]], failures


def _timing(device: str, stats: Sequence[PageStats], names: Sequence[str]) -> DeviceTiming | None:
    if not stats:
        return None
    return DeviceTiming(
        device=device,
        pages=len(stats),
        lines_per_page=statistics.mean(s.lines for s in stats),
        layout_seconds=statistics.median(s.layout_seconds for s in stats),
        engine_seconds={
            n: statistics.median(s.engine_seconds[n] for s in stats if n in s.engine_seconds)
            for n in names
            if any(n in s.engine_seconds for s in stats)
        },
    )


def device_label(setup: OcrSetup) -> str:
    device = setup.device
    return device.kind if device.name == device.kind else f"{device.kind} ({device.name})"


def collect(
    setup: OcrSetup,
    store: GroundTruthStore,
    *,
    settings: Settings,
    date: str,
    label: str = "",
    cpu_setup: Callable[[], OcrSetup] | None = None,
    cpu_pages: int = 3,
    progress: Callable[[str], None] = lambda message: None,
) -> BenchRun:
    """Read the whole set with ``setup``; with ``cpu_setup`` (built after the first pass, so
    that the GPU model is released first) time the first ``cpu_pages`` pages on the CPU too."""
    pages = [store.get(page_id) for page_id in store.page_ids()]
    names = engine_order(setup)
    lines: list[BenchLine] = []
    stats: list[PageStats] = []
    warmed = False
    for page in pages:
        active = _active(page)
        if not active:
            continue
        image = store.image(page.id)
        boxes = [ln.box for ln in active]
        if not warmed:
            _warm_up(setup, image, boxes)
            warmed = True
        layout_s, detected, matched, tables, inside = _layout(setup, image, boxes)
        seconds, per_line, failures = _read_page(setup, image, boxes)
        progress(f"{page.id}: {len(active)} lines")
        for truth, readings in zip(active, per_line, strict=True):
            lines.append(
                BenchLine(
                    page=page.id,
                    capture=page.capture,
                    content_class=truth.content_class,
                    truth=truth.text,
                    verified=truth.status is TruthStatus.VERIFIED,
                    readings=tuple(readings[n] for n in names if n in readings),
                    prefill=truth.prefill,
                )
            )
        stats.append(
            PageStats(
                page=page.id,
                capture=page.capture,
                lines=len(active),
                detected=detected,
                matched=matched,
                tables=tables,
                lines_in_tables=inside,
                layout_seconds=layout_s,
                engine_seconds=seconds,
                failures=tuple(failures),
            )
        )
    timings = [t for t in [_timing(device_label(setup), stats, names)] if t is not None]
    if cpu_setup is not None and setup.device.kind != "cpu" and pages:
        forced = _cpu_timing(cpu_setup, store, pages, cpu_pages, progress)
        if forced is not None:
            timings.append(forced)
    return BenchRun(
        lines=tuple(lines),
        pages=tuple(stats),
        timings=tuple(timings),
        engines={n: setup.engines[n].ref.version for n in names},
        skipped=dict(setup.skipped),
        layout=f"{setup.layout.ref.name} {setup.layout.ref.version}",
        device=device_label(setup),
        settings=setup.settings,
        date=date,
        label=label,
        config={
            "TARN_OCR_DETECTION_MODEL": settings.ocr_detection_model,
            "TARN_OCR_RECOGNITION_MODEL": settings.ocr_recognition_model,
            "TARN_TROCR_MODEL": settings.trocr_model,
            "TARN_TROCR_PRECISION": settings.trocr_precision,
        },
    )


def _cpu_timing(
    build: Callable[[], OcrSetup],
    store: GroundTruthStore,
    pages: Sequence[TruthPage],
    count: int,
    progress: Callable[[str], None],
) -> DeviceTiming | None:
    setup = build()
    names = engine_order(setup)
    stats: list[PageStats] = []
    warmed = False
    for page in [p for p in pages if _active(p)][:count]:
        active = _active(page)
        image = store.image(page.id)
        boxes = [ln.box for ln in active]
        if not warmed:
            _warm_up(setup, image, boxes)
            warmed = True
        layout_s, *_ = _layout(setup, image, boxes)
        seconds, _, failures = _read_page(setup, image, boxes)
        progress(f"{page.id} on the CPU")
        stats.append(
            PageStats(
                page=page.id,
                capture=page.capture,
                lines=len(active),
                detected=0,
                matched=0,
                tables=0,
                lines_in_tables=0,
                layout_seconds=layout_s,
                engine_seconds=seconds,
                failures=tuple(failures),
            )
        )
    return _timing("cpu (forced)", stats, names)
