"""PaddleOCR 3 (PP-OCRv5): text-line detection and layout for every page, and a recogniser that
reads the line crops (strongest on print; its handwriting is measured in P11). CPU only.

Layout: text pieces from the detector are joined into lines; the layout model's ``table``
boxes become tables whose cells come from the ruling lines (or, without rulings, from where the
text sits); its picture boxes (``image``, ``chart``, ``figure``) become diagrams, and the text
inside a diagram is kept as labels."""

import os
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tarn_adapters.ocr.images import crop, decode
from tarn_adapters.ocr.layout import (
    cells_from_rulings,
    cells_from_text,
    inside,
    join_into_lines,
    reading_order,
)
from tarn_core.domain.booklet import LineReading, RegionKind
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineFailedError
from tarn_core.ports.engines import DetectedRegion

NAME = "paddle"
DETECTION_MODEL = "PP-OCRv5_mobile_det"
"""Measured on a sample page (P10): mobile 0.4 s, server 7.4 s, the same 24 lines found. P11
compares their accuracy (``TARN_OCR_DETECTION_MODEL``)."""
RECOGNITION_MODEL = "PP-OCRv5_server_rec"
LAYOUT_MODEL = "PP-DocLayout-L"
DIAGRAM_LABELS = frozenset({"image", "chart", "figure"})

_models: dict[str, Any] = {}
_lock = threading.Lock()


def configure(model_dir: Path) -> None:
    """Point PaddleX at the git-ignored model folder; call before the first model is built."""
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(model_dir / "paddlex"))
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def _model(kind: str, name: str) -> Any:
    with _lock:
        if name not in _models:
            try:
                import paddleocr
            except ImportError as error:  # pragma: no cover
                raise EngineFailedError("paddleocr is not installed") from error
            factory = getattr(paddleocr, kind)
            # oneDNN (Paddle's CPU acceleration) breaks the detection models in Paddle 3.3
            # ("ConvertPirAttribute2RuntimeAttribute"); recognition works with it and is 3×
            # faster (P10: 2.4 s instead of 7.8 s for 24 lines, same scores).
            mkldnn = kind == "TextRecognition"
            _models[name] = factory(model_name=name, device="cpu", enable_mkldnn=mkldnn)
        return _models[name]


def _version() -> str:
    try:
        import paddleocr
    except ImportError:  # pragma: no cover
        return "unavailable"
    return str(paddleocr.__version__)


def _box(points: Any, width: int, height: int) -> Box | None:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    x0, y0 = max(0, int(min(xs))), max(0, int(min(ys)))
    x1, y1 = min(width, int(max(xs)) + 1), min(height, int(max(ys)) + 1)
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return Box(x0=x0, y0=y0, x1=x1, y1=y1)


class PaddleLayoutDetector:
    def __init__(
        self,
        *,
        detection_model: str = DETECTION_MODEL,
        layout_model: str | None = LAYOUT_MODEL,
    ) -> None:
        self._detection = detection_model
        self._layout = layout_model
        self.ref = EngineRef(
            name="paddle-layout",
            version=f"{_version()} {detection_model}"
            + (f" {layout_model}" if layout_model else ""),
        )

    def text_pieces(self, image: bytes) -> list[Box]:
        pixels = decode(image)
        height, width = pixels.shape[:2]
        result = next(iter(_model("TextDetection", self._detection).predict(pixels)))
        boxes = [_box(poly, width, height) for poly in result["dt_polys"]]
        return [b for b in boxes if b is not None]

    def _layout_boxes(self, pixels: Any) -> list[tuple[str, Box]]:
        if self._layout is None:
            return []
        height, width = pixels.shape[:2]
        result = next(iter(_model("LayoutDetection", self._layout).predict(pixels)))
        found = []
        for item in result["boxes"]:
            x0, y0, x1, y1 = (float(v) for v in item["coordinate"])
            box = _box([(x0, y0), (x1, y1)], width, height)
            if box is not None:
                found.append((str(item["label"]), box))
        return found

    def detect(self, image: bytes) -> Sequence[DetectedRegion]:
        pixels = decode(image)
        lines = join_into_lines(self.text_pieces(image))
        layout = self._layout_boxes(pixels)
        tables = [b for label, b in layout if label == "table"]
        diagrams = [b for label, b in layout if label in DIAGRAM_LABELS]
        regions: list[DetectedRegion] = []
        used: set[Box] = set()
        for table in tables:
            members = [line for line in lines if inside(line, table)]
            used.update(members)
            piece = crop(pixels, table)
            cells = cells_from_rulings(piece, table) if piece is not None else ()
            regions.append(
                DetectedRegion(
                    kind=RegionKind.TABLE, box=table, cells=cells or cells_from_text(members)
                )
            )
        for diagram in diagrams:
            regions.append(DetectedRegion(kind=RegionKind.DIAGRAM, box=diagram))
        for line in reading_order([line for line in lines if line not in used]):
            kind = (
                RegionKind.LABEL if any(inside(line, d) for d in diagrams) else RegionKind.TEXT_LINE
            )
            regions.append(DetectedRegion(kind=kind, box=line))
        return regions


class PaddleOcrEngine:
    """The PP-OCRv5 recogniser on our line crops."""

    def __init__(self, *, recognition_model: str = RECOGNITION_MODEL, batch: int = 8) -> None:
        self._name = recognition_model
        self._batch = batch
        self.ref = EngineRef(name=NAME, version=f"{_version()} {recognition_model}")

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        pixels = decode(image)
        crops = []
        for box in lines:
            piece = crop(pixels, box, pad=3)
            if piece is not None:
                crops.append((box, piece))
        if not crops:
            return []
        results = _model("TextRecognition", self._name).predict(
            input=[c for _, c in crops], batch_size=self._batch
        )
        readings = []
        for (box, _), result in zip(crops, results, strict=True):
            confidence = min(1.0, max(0.0, float(result["rec_score"])))
            text = str(result["rec_text"]).strip()
            readings.append(
                LineReading(
                    engine=self.ref, text=text, box=box, confidence=confidence if text else 0.0
                )
            )
        return readings
