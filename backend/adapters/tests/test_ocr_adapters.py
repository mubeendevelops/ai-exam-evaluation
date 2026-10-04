"""OCR adapters without models or network: the cloud engines on recorded-format responses, the
rules that keep them off, the time limit, the GPU slot and TrOCR's fallbacks, layout geometry,
the word list and the page transform."""

import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from tarn_adapters.compute import DeviceInfo, ModelSlot
from tarn_adapters.config import Settings
from tarn_adapters.ocr import trocr, wiring
from tarn_adapters.ocr.azure_read import AzureReadEngine
from tarn_adapters.ocr.images import decode, encode_jpeg
from tarn_adapters.ocr.layout import (
    cells_from_rulings,
    cells_from_text,
    join_into_lines,
    reading_order,
)
from tarn_adapters.ocr.tesseract import words_to_line
from tarn_adapters.ocr.textract import TextractEngine
from tarn_adapters.ocr.timeout import TimedEngine
from tarn_adapters.ocr.transform import OpenCvPageTransform
from tarn_adapters.ocr.words import FileWordList
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.ocr import ContentClass
from tarn_core.errors import EngineFailedError, EngineTimeoutError
from tarn_core.ports.engines import OcrEngine
from tarn_core.testing import ScriptedLayoutDetector

DATA = Path(__file__).parent / "data" / "ocr"
CPU = DeviceInfo("cpu", "cpu", None, "test")
CUDA = DeviceInfo("cuda", "fake gpu", 4096, "test")


def page(width: int = 1000, height: int = 500) -> bytes:
    return encode_jpeg(np.full((height, width, 3), 255, np.uint8))


# --- cloud engines on recorded-format responses ---------------------------------------------


def test_textract_words_become_pixel_readings() -> None:
    response = json.loads((DATA / "textract-detect-document-text.json").read_text())
    engine = TextractEngine(lambda image: response)
    readings = engine.read(page(1000, 500), [])
    assert [r.text for r in readings] == ["Gradient", "descent", "42.5"]  # words only, no blanks
    assert readings[0].box == Box(x0=100, y0=50, x1=300, y1=75)  # polygon × page size
    assert readings[1].box == Box(x0=320, y0=50, x1=500, y1=75)  # bounding box when no polygon
    assert readings[0].confidence == pytest.approx(0.985)
    assert all(r.engine.name == "textract" for r in readings)


def test_azure_words_scale_from_the_page_unit() -> None:
    result = json.loads((DATA / "azure-read.json").read_text())
    engine = AzureReadEngine(lambda image: result)
    readings = engine.read(page(1000, 500), [])  # the service measured a 2000 × 1000 page
    assert [r.text for r in readings] == ["Gradient", "descent", "42.5"]
    assert readings[0].box == Box(x0=100, y0=50, x1=300, y1=75)
    assert readings[1].box == Box(x0=320, y0=49, x1=501, y1=76)
    assert readings[1].confidence == pytest.approx(0.87)
    assert AzureReadEngine(lambda image: {"pages": []}).read(page(), []) == []


def test_cloud_failures_become_engine_failures_without_the_message() -> None:
    def boom(image: bytes) -> Any:
        raise RuntimeError("request id 123, account 4567, secret detail")

    for engine in (TextractEngine(boom), AzureReadEngine(boom)):
        with pytest.raises(EngineFailedError) as raised:
            engine.read(page(), [])
        assert "secret" not in str(raised.value) and "RuntimeError" in str(raised.value)


# --- which engines run ------------------------------------------------------------------------


class _Fake:
    def __init__(self, name: str) -> None:
        self.ref = EngineRef(name=name, version="1")

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        return []


def _factories(fail: set[str] = frozenset()) -> dict[str, wiring.Factory]:  # type: ignore[assignment]
    def make(name: str) -> wiring.Factory:
        def factory(settings: Settings, device: DeviceInfo) -> OcrEngine:
            if name in fail:
                raise EngineFailedError(f"{name} is not available here")
            return _Fake(name)

        return factory

    return {n: make(n) for n in ("trocr", "paddle", "tesseract", "textract", "azure")}


def _settings(tmp_path: Path, **values: Any) -> Settings:
    return Settings(model_dir=tmp_path, _env_file=None, **values)


def _build(settings: Settings, **kw: Any) -> wiring.OcrSetup:
    return wiring.build_ocr(
        settings, device=CPU, factories=_factories(**kw), layout=ScriptedLayoutDetector([])
    )


def test_cloud_engines_are_off_by_default(tmp_path: Path) -> None:
    setup = _build(_settings(tmp_path))
    assert list(setup.engines) == ["paddle", "tesseract", "trocr"]
    assert "TARN_CLOUD_OCR_ENABLED" in setup.skipped["textract"]
    assert "TARN_CLOUD_OCR_ENABLED" in setup.skipped["azure"]


def test_development_needs_a_second_switch_for_the_cloud(tmp_path: Path) -> None:
    setup = _build(_settings(tmp_path, cloud_ocr_enabled=True))
    assert "textract" not in setup.engines
    assert "ALLOW_IN_DEVELOPMENT" in setup.skipped["textract"]
    allowed = _build(
        _settings(tmp_path, cloud_ocr_enabled=True, cloud_ocr_allow_in_development=True)
    )
    assert {"textract", "azure"} <= set(allowed.engines)


def test_missing_credentials_or_engines_are_reported_not_hidden(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cloud_ocr_enabled=True, cloud_ocr_allow_in_development=True)
    setup = wiring.build_ocr(
        settings,
        device=CPU,
        layout=ScriptedLayoutDetector([]),
        factories={**_factories(fail={"tesseract"}), "azure": wiring.FACTORIES["azure"]},
    )
    assert "TARN_AZURE_DI_ENDPOINT" in setup.skipped["azure"]
    assert setup.skipped["tesseract"] == "tesseract is not available here"
    odd = _build(_settings(tmp_path, ocr_engines_print="paddle,mystery"))
    assert odd.skipped["mystery"] == "unknown engine"


def test_engine_sets_and_selector_settings_come_from_settings(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        ocr_engines_cursive=" TrOCR , paddle,trocr ",
        ocr_alpha=0.3,
        ocr_beta=0.1,
        ocr_flag_threshold=0.7,
    )
    selector = wiring.selector_settings(settings)
    assert selector.engine_sets[ContentClass.CURSIVE] == ("trocr", "paddle")
    assert (selector.alpha, selector.beta, selector.flag_threshold) == (0.3, 0.1, 0.7)
    setup = _build(settings)
    assert all(isinstance(e, TimedEngine) for e in setup.engines.values())
    assert len(setup.word_list) > 50_000


# --- time limit ---------------------------------------------------------------------------------


class _Slow:
    ref = EngineRef(name="slow", version="1")

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        time.sleep(self.seconds)
        return []


def test_a_slow_engine_times_out_and_a_fast_one_does_not() -> None:
    with pytest.raises(EngineTimeoutError):
        TimedEngine(_Slow(0.5), 0.05).read(b"", [])
    assert TimedEngine(_Slow(0.0), 1.0).read(b"", []) == []
    assert TimedEngine(_Slow(0.0), 1.0).ref.name == "slow"


def test_an_engine_error_passes_through_the_time_limit() -> None:
    class Broken(_Slow):
        def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
            raise EngineFailedError("down")

    with pytest.raises(EngineFailedError):
        TimedEngine(Broken(0), 1.0).read(b"", [])


# --- GPU slot and TrOCR fallbacks -----------------------------------------------------------


def test_the_gpu_slot_holds_one_model_at_a_time() -> None:
    slot = ModelSlot()
    released: list[str] = []
    first = slot.acquire("a", lambda: "model-a", released.append)
    assert slot.acquire("a", lambda: "other", released.append) == first  # loaded once
    slot.acquire("b", lambda: "model-b", released.append)
    assert released == ["model-a"] and slot.holder == "b"
    slot.free()
    assert released == ["model-a", "model-b"] and slot.holder is None


def test_the_gpu_slot_is_safe_across_threads() -> None:
    slot = ModelSlot()
    loads: list[int] = []

    def load() -> str:
        loads.append(1)
        time.sleep(0.01)
        return "m"

    threads = [
        threading.Thread(target=lambda: slot.acquire("m", load, lambda m: None)) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(loads) == 1


class _FakeTensor:
    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows

    def __getitem__(self, key: int | tuple[int, slice]) -> "_FakeTensor":
        if isinstance(key, int):
            return _FakeTensor([self.rows[key]])
        row, cols = key
        return _FakeTensor([self.rows[row][cols]])

    def tolist(self) -> list[Any]:
        return self.rows[0] if len(self.rows) == 1 else self.rows

    def to(self, *args: Any, **kwargs: Any) -> "_FakeTensor":
        return self


class _FakeModel:
    """Generates token 5 for each crop; the first ``ooms`` calls run out of memory."""

    def __init__(self, device: str, ooms: list[int]) -> None:
        self.device = device
        self.ooms = ooms
        self.batches: list[int] = []

    def to(self, device: str) -> "_FakeModel":
        return self

    def generate(self, inputs: Any, **kwargs: Any) -> Any:
        size = len(inputs.rows)
        if self.device != "cpu" and self.ooms and size >= self.ooms[0]:
            self.ooms.pop(0)
            raise RuntimeError("CUDA out of memory. Tried to allocate 20 MiB")
        self.batches.append(size)

        class Out:
            sequences = _FakeTensor([[0, 5, 2] for _ in range(size)])
            scores = None

        return Out()

    def compute_transition_scores(self, seqs: Any, scores: Any, normalize_logits: bool) -> Any:
        return _FakeTensor([[-0.1, -0.1] for _ in seqs.rows])


class _FakeProcessor:
    class tokenizer:  # noqa: N801  (mirrors the transformers attribute)
        pad_token_id = 1
        eos_token_id = 2

    def __call__(self, images: list[Any], return_tensors: str) -> Any:
        class Batch:
            pixel_values = _FakeTensor([[0] for _ in images])

        return Batch()

    def batch_decode(self, seqs: Any, skip_special_tokens: bool) -> list[str]:
        return ["word"] * len(seqs.rows)


@pytest.fixture
def fake_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cuda:
        @staticmethod
        def empty_cache() -> None: ...

        @staticmethod
        def is_available() -> bool:
            return False

    class Torch:
        float16, float32 = "fp16", "fp32"
        cuda = Cuda

        class inference_mode:  # noqa: N801
            def __enter__(self) -> None: ...

            def __exit__(self, *args: object) -> None: ...

    monkeypatch.setattr(trocr, "_torch", lambda: Torch)


def _lines(n: int) -> list[Box]:
    return [Box(x0=10, y0=10 + i * 40, x1=500, y1=40 + i * 40) for i in range(n)]


def test_trocr_reads_in_batches_on_the_gpu(fake_torch: None) -> None:
    models: dict[str, _FakeModel] = {}

    def loader(model_id: str, device: str, cache: Path | None, precision: str) -> trocr._Loaded:
        assert precision == "fp32"
        models[device] = _FakeModel(device, [])
        return trocr._Loaded(
            processor=_FakeProcessor(), model=models[device], device=device, dtype="fp16"
        )

    engine = trocr.TrOcrEngine(
        model="m", device=CUDA, precision="fp32", slot=ModelSlot(), loader=loader
    )
    assert engine.batch == 8 and engine.device == "cuda:0"
    readings = engine.read(page(), _lines(10))
    assert models["cuda:0"].batches == [8, 2]
    assert [r.text for r in readings] == ["word"] * 10
    assert readings[0].confidence == pytest.approx(np.exp(-0.1))
    assert trocr.TrOcrEngine(model="m", device=CPU, loader=loader).batch == 4


def test_trocr_halves_the_batch_then_falls_back_to_the_cpu(fake_torch: None) -> None:
    models: dict[str, _FakeModel] = {}

    def loader(model_id: str, device: str, cache: Path | None, precision: str) -> trocr._Loaded:
        models[device] = _FakeModel(device, [3, 2, 1, 1])  # out of memory at every size
        return trocr._Loaded(
            processor=_FakeProcessor(), model=models[device], device=device, dtype="x"
        )

    engine = trocr.TrOcrEngine(
        model="m", device=CUDA, precision="fp32", slot=ModelSlot(), loader=loader
    )
    readings = engine.read(page(), _lines(3))
    assert engine.device == "cpu"
    assert engine.fallbacks == [
        "out of memory: batch 4",
        "out of memory: batch 2",
        "out of memory: batch 1",
        "out of memory at batch 1: CPU",
    ]
    assert len(readings) == 3 and models["cpu"].batches == [1, 1, 1]


def test_trocr_does_not_hide_other_errors(fake_torch: None) -> None:
    class Broken(_FakeModel):
        def generate(self, inputs: Any, **kwargs: Any) -> Any:
            raise ValueError("bad input")

    def loader(model_id: str, device: str, cache: Path | None, precision: str) -> trocr._Loaded:
        return trocr._Loaded(
            processor=_FakeProcessor(), model=Broken(device, []), device=device, dtype="x"
        )

    with pytest.raises(ValueError, match="bad input"):
        trocr.TrOcrEngine(
            model="m", device=CUDA, precision="fp32", slot=ModelSlot(), loader=loader
        ).read(page(), _lines(1))


# --- layout geometry ------------------------------------------------------------------------


def test_words_side_by_side_join_into_lines_in_reading_order() -> None:
    pieces = [
        Box(x0=300, y0=105, x1=500, y1=150),  # second word of line 1
        Box(x0=100, y0=100, x1=280, y1=148),  # first word of line 1
        Box(x0=100, y0=200, x1=400, y1=245),  # line 2
        Box(x0=1500, y0=100, x1=1700, y1=150),  # far right: a separate line
    ]
    assert join_into_lines(pieces) == [
        Box(x0=100, y0=100, x1=500, y1=150),
        Box(x0=1500, y0=100, x1=1700, y1=150),
        Box(x0=100, y0=200, x1=400, y1=245),
    ]
    assert reading_order([]) == []


def test_unruled_table_cells_from_where_the_text_sits() -> None:
    lines = [
        Box(x0=100, y0=100, x1=200, y1=130),
        Box(x0=400, y0=102, x1=520, y1=131),
        Box(x0=101, y0=160, x1=180, y1=190),
        Box(x0=402, y0=161, x1=500, y1=190),
    ]
    cells = cells_from_text(lines)
    assert [(c.row, c.col) for c in cells] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert cells[1].box == lines[1]
    assert cells_from_text([]) == ()


def test_ruled_table_cells_from_its_lines() -> None:
    table = np.full((300, 600, 3), 255, np.uint8)
    for y in (10, 150, 290):
        cv2.line(table, (5, y), (595, y), (0, 0, 0), 2)
    for x in (5, 300, 595):
        cv2.line(table, (x, 10), (x, 290), (0, 0, 0), 2)
    origin = Box(x0=1000, y0=2000, x1=1600, y1=2300)
    cells = cells_from_rulings(table, origin)
    assert [(c.row, c.col) for c in cells] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    first = cells[0].box
    assert 1000 <= first.x0 <= 1010 and 2000 <= first.y0 <= 2015 and 1290 <= first.x1 <= 1300
    assert cells_from_rulings(np.full((100, 100, 3), 255, np.uint8), origin) == ()


# --- tesseract word list, words, transform -----------------------------------------------------


def test_tesseract_words_become_a_line() -> None:
    text, confidence = words_to_line(["", "Answer", "all", " "], [-1, 90, 60, -1])
    assert text == "Answer all"
    assert confidence == pytest.approx((6 * 0.9 + 3 * 0.6) / 9)
    assert words_to_line(["", ""], [-1, -1]) == ("", 0.0)


def test_the_bundled_word_list() -> None:
    words = FileWordList.load()
    for word in ("network", "Gradient", "india", "algorithm", "network's"):
        assert words.contains(word), word
    assert not words.contains("qzxwv")


def test_a_custom_word_list(tmp_path: Path) -> None:
    path = tmp_path / "words.txt"
    path.write_text("Alpha\nbeta\n\n")
    words = FileWordList.load(path)
    assert len(words) == 2 and words.contains("ALPHA")


def test_the_page_transform_turns_and_shrinks() -> None:
    image = np.zeros((100, 200, 3), np.uint8)
    image[:10, :10] = 255  # a white corner, top left
    transform = OpenCvPageTransform()
    turned = decode(transform.rotate(encode_jpeg(image), 180))
    assert turned.shape[:2] == (100, 200)
    assert turned[-5, -5].mean() > 200 and turned[5, 5].mean() < 50
    quarter = decode(transform.rotate(encode_jpeg(image), 90))
    assert quarter.shape[:2] == (200, 100)
    small = decode(transform.downscale(encode_jpeg(image), 50))
    assert max(small.shape[:2]) == 50
    same = encode_jpeg(image)
    assert transform.downscale(same, 500) == same
    with pytest.raises(ValueError):
        transform.rotate(same, 45)


def test_engines_whose_libraries_are_missing_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    real = importlib.util.find_spec
    gone = {"torch", "transformers"}
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name, *a: None if name in gone else real(name, *a)
    )
    setup = wiring.build_ocr(
        _settings(tmp_path),
        device=CPU,
        layout=ScriptedLayoutDetector([]),
        factories={**_factories(), "trocr": wiring.FACTORIES["trocr"]},
    )
    assert "trocr" not in setup.engines
    assert (
        setup.skipped["trocr"] == "torch, transformers not installed (the `ocr` dependency group)"
    )


def test_without_the_layout_library_ocr_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: None if name == "paddleocr" else real(name, *a),
    )
    with pytest.raises(EngineFailedError, match="paddleocr not installed"):
        wiring.build_ocr(_settings(tmp_path), device=CPU, factories=_factories())


def test_trocr_precision_is_fp32_on_the_cpu_and_measured_on_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert trocr.resolve_precision("cpu", "fp16") == "fp32"
    assert trocr.resolve_precision("cuda:0", "fp16") == "fp16"
    monkeypatch.setattr(trocr, "_fp16_is_faster", lambda device: False)  # a GTX 1650
    assert trocr.resolve_precision("cuda:0", "auto") == "fp32"
    monkeypatch.setattr(trocr, "_fp16_is_faster", lambda device: True)  # tensor cores
    assert trocr.resolve_precision("cuda:0", "auto") == "fp16"


def test_trocr_keeps_the_decoder_cache_on(fake_torch: None) -> None:
    seen: dict[str, Any] = {}

    class Recording(_FakeModel):
        def generate(self, inputs: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return super().generate(inputs, **kwargs)

    def loader(model_id: str, device: str, cache: Path | None, precision: str) -> trocr._Loaded:
        return trocr._Loaded(
            processor=_FakeProcessor(), model=Recording(device, []), device=device, dtype="x"
        )

    trocr.TrOcrEngine(model="m", device=CPU, loader=loader).read(page(), _lines(1))
    assert seen["use_cache"] is True and seen["num_beams"] == 1


def test_the_page_transform_reports_the_size() -> None:
    image = encode_jpeg(np.zeros((120, 300, 3), np.uint8))
    assert OpenCvPageTransform().size(image) == (300, 120)
