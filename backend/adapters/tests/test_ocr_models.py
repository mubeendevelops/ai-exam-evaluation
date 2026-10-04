"""The real OCR engines on generated pages (``make test-models``): Paddle's layout and
recogniser, Tesseract, TrOCR on the CPU and on CUDA when present, the whole page reader, and the
180° check. Skipped when the ``ocr`` group or the model weights are missing. Pages are drawn
with OpenCV's fonts: no sample data."""

import importlib.util
import os

import cv2
import numpy as np
import pytest

from tarn_adapters.compute import CPU_ONLY, ModelSlot, detect_device
from tarn_adapters.config import Settings
from tarn_adapters.ocr.batch import page_ocr
from tarn_adapters.ocr.images import encode_jpeg
from tarn_adapters.ocr.wiring import build_ocr, configure_model_dir
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.services.ocr.selector import Lexicon
from tarn_core.services.ocr.text import cer

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(
        any(importlib.util.find_spec(m) is None for m in ("paddleocr", "torch", "pytesseract")),
        reason="the ocr dependency group is not installed",
    ),
]

LINES = ("Answer all the questions", "Gradient descent finds a minimum", "Total marks 42")


def printed_page(lines: tuple[str, ...] = LINES, font: int = cv2.FONT_HERSHEY_SIMPLEX) -> bytes:
    page = np.full((1400, 1000, 3), 250, np.uint8)
    for i, text in enumerate(lines):
        cv2.putText(page, text, (60, 160 + i * 110), font, 1.4, (25, 25, 25), 3, cv2.LINE_AA)
    return encode_jpeg(page)


@pytest.fixture(scope="module")
def settings() -> Settings:
    s = Settings()
    configure_model_dir(s)
    if not (s.model_dir / "paddlex").exists() or not (s.model_dir / "huggingface").exists():
        pytest.skip("model weights not downloaded (tarn ocr models fetch)")
    return s


@pytest.fixture(scope="module")
def setup(settings: Settings):  # type: ignore[no-untyped-def]
    return build_ocr(settings)


def test_layout_finds_the_printed_lines(setup) -> None:  # type: ignore[no-untyped-def]
    regions = setup.layout.detect(printed_page())
    lines = [r for r in regions if r.kind is RegionKind.TEXT_LINE]
    assert len(lines) == 3
    assert [r.box.y0 for r in lines] == sorted(r.box.y0 for r in lines)  # reading order


@pytest.mark.parametrize("engine", ["paddle", "tesseract"])
def test_print_engines_read_printed_lines(setup, engine: str) -> None:  # type: ignore[no-untyped-def]
    image = printed_page()
    boxes = [r.box for r in setup.layout.detect(image) if r.kind is RegionKind.TEXT_LINE]
    readings = setup.engines[engine].read(image, boxes)
    assert len(readings) == 3
    for reading, truth in zip(readings, LINES, strict=True):
        assert cer(reading.text, truth) <= 0.1, (reading.text, truth)
        assert 0.5 <= reading.confidence <= 1


def test_trocr_reads_on_the_cpu(settings: Settings) -> None:
    from tarn_adapters.ocr.trocr import TrOcrEngine

    engine = TrOcrEngine(
        model=settings.trocr_model,
        device=CPU_ONLY,
        cache_dir=settings.model_dir / "huggingface",
        slot=ModelSlot(),
    )
    image = printed_page(LINES[1:2], cv2.FONT_HERSHEY_SCRIPT_SIMPLEX)
    (reading,) = engine.read(image, [Box(x0=40, y0=100, x1=960, y1=190)])
    assert cer(reading.text, LINES[1]) <= 0.3, reading.text
    assert 0 < reading.confidence <= 1


@pytest.mark.skipif(
    os.environ.get("TARN_DEVICE") == "cpu" or detect_device("auto").kind != "cuda",
    reason="no CUDA device",
)
def test_trocr_reads_on_cuda_in_half_precision(settings: Settings) -> None:
    import torch

    from tarn_adapters.ocr.trocr import TrOcrEngine

    slot = ModelSlot()
    engine = TrOcrEngine(
        model=settings.trocr_model,
        device=detect_device("cuda"),
        cache_dir=settings.model_dir / "huggingface",
        slot=slot,
    )
    image = printed_page(LINES[1:2], cv2.FONT_HERSHEY_SCRIPT_SIMPLEX)
    (reading,) = engine.read(image, [Box(x0=40, y0=100, x1=960, y1=190)])
    assert cer(reading.text, LINES[1]) <= 0.3, reading.text
    assert engine.device.startswith("cuda") and slot.holder is not None
    assert torch.cuda.memory_allocated() < 3 * 2**30  # leaves room on a 4 GB card
    slot.free()


def test_the_page_reader_picks_and_scores_every_line(setup) -> None:  # type: ignore[no-untyped-def]
    text = page_ocr(setup).read(printed_page(), Lexicon(frozenset(), setup.word_list))
    lines = text.lines
    assert len(lines) == 3 and not text.failures
    for line, truth in zip(lines, LINES, strict=True):
        assert line.choice is not None and line.choice.chosen is not None
        chosen = line.readings[line.choice.chosen].text
        assert cer(chosen, truth) <= 0.1, (chosen, truth)
        assert {line_r.engine.name for line_r in line.readings} == set(setup.engines)
    assert lines[2].content_class is not None


def test_an_upside_down_page_is_found(setup) -> None:  # type: ignore[no-untyped-def]
    ocr = page_ocr(setup)
    upright = printed_page()
    assert ocr.check_orientation(upright).turned is False
    upside_down = setup.transform.rotate(upright, 180)
    check = ocr.check_orientation(upside_down)
    assert check.decided and check.turned, check


def test_the_benchmark_runs_end_to_end_on_a_verified_printed_set(setup, tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Ground truth made from drawn text: the real engines read the truth boxes, the report holds
    real error rates (small for print) and none of the text."""
    from datetime import UTC, datetime

    from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore
    from tarn_adapters.ocr.bench import collect
    from tarn_core.domain.groundtruth import CaptureType, TruthLine, TruthPage, TruthStatus
    from tarn_core.domain.ocr import ContentClass
    from tarn_core.services.ocr.benchmark import ALL, SELECTOR, build_report
    from tarn_core.services.ocr.benchmark_report import render

    store = DirectoryGroundTruthStore(tmp_path)
    image = printed_page()
    boxes = [r.box for r in setup.layout.detect(image) if r.kind is RegionKind.TEXT_LINE]
    assert len(boxes) == 3
    for n in range(5):  # five pages: enough for intervals
        store.save(
            TruthPage(
                id=f"gen-p{n}",
                capture=CaptureType.CLEAN_SCAN,
                image=f"gen-p{n}.jpg",
                width=1000,
                height=1400,
                lines=tuple(
                    TruthLine(
                        box=box,
                        text=text,
                        content_class=ContentClass.PRINT,
                        status=TruthStatus.VERIFIED,
                    )
                    for box, text in zip(boxes, LINES, strict=True)
                ),
            ),
            image,
        )
    run = collect(setup, store, settings=Settings(), date="2026-10-04")
    report = build_report(run, lexicon=Lexicon(frozenset(), setup.word_list), now=datetime.now(UTC))
    assert report.accuracy is not None
    selector = report.accuracy.methods[SELECTOR].groups[ALL]
    assert selector.lines == 15
    assert selector.cer is not None and selector.cer <= 0.1
    assert report.accuracy.methods[SELECTOR].ci is not None
    assert report.detection.matched == report.detection.truth_lines == 15
    text = render(report)
    assert "Gradient descent" not in text
    assert all(t.page_seconds > 0 for t in run.timings)
