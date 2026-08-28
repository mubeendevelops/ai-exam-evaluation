"""
core/ocr_engines/paddleocr_engine.py — PaddleOCR as an OCREngine plugin.

This is the detection+recognition logic that used to live directly in
core/diagram_extractor.py (see that module's migration-history docstring for
why PaddleOCR was chosen over EasyOCR/TrOCR). It has been moved here
unchanged, just wrapped in the OCREngine interface, so it can be selected
dynamically alongside other engines (see core/ocr_fallback.py) instead of
being the pipeline's only option.
"""
from __future__ import annotations

import contextlib
import io
import os

from core.ocr_engines.base import OCREngine

# See core/diagram_extractor.py's original docstring (now moved here) for
# the reasoning behind every constant below — server det / mobile en rec,
# lowered detection thresholds, oneDNN disabled.
_DET_MODEL_NAME = os.environ.get("PADDLEOCR_DET_MODEL", "PP-OCRv5_server_det")
_REC_MODEL_NAME = os.environ.get("PADDLEOCR_REC_MODEL", "en_PP-OCRv5_mobile_rec")

DETECT_THRESH = 0.2
DETECT_BOX_THRESH = 0.3
DETECT_UNCLIP_RATIO = 2.0

_ENABLE_MKLDNN = False

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@contextlib.contextmanager
def _quiet_paddle():
    """Suppresses PaddleOCR/paddlex/paddle's own chatter during model
    construction and inference. See the pre-refactor version of this
    function (git history, core/diagram_extractor.py before the plugin
    split) for the full rationale — unchanged here."""
    import logging
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="No ccache found")
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield
        finally:
            logging.disable(logging.NOTSET)


class PaddleOCREngine(OCREngine):
    name = "paddleocr"

    def __init__(self):
        self._detector = None
        self._recognizer = None

    def _get_detector(self):
        if self._detector is None:
            from paddleocr import TextDetection
            with _quiet_paddle():
                self._detector = TextDetection(
                    model_name=_DET_MODEL_NAME,
                    enable_mkldnn=_ENABLE_MKLDNN,
                    thresh=DETECT_THRESH,
                    box_thresh=DETECT_BOX_THRESH,
                    unclip_ratio=DETECT_UNCLIP_RATIO,
                )
        return self._detector

    def _get_recognizer(self):
        if self._recognizer is None:
            from paddleocr import TextRecognition
            with _quiet_paddle():
                self._recognizer = TextRecognition(
                    model_name=_REC_MODEL_NAME,
                    enable_mkldnn=_ENABLE_MKLDNN,
                )
        return self._recognizer

    def detect_regions(self, image) -> list[tuple[int, int, int, int]]:
        import numpy as np

        detector = self._get_detector()
        arr = np.array(image)  # RGB, as PaddleOCR expects

        with _quiet_paddle():
            results = list(detector.predict(arr))
        if not results:
            return []
        polys = dict(results[0]).get("dt_polys", [])

        boxes = []
        for poly in polys:
            xs = [int(pt[0]) for pt in poly]
            ys = [int(pt[1]) for pt in poly]
            x0, y0 = min(xs), min(ys)
            boxes.append((x0, y0, max(xs) - x0, max(ys) - y0))

        boxes.sort(key=lambda b: (b[1], b[0]))
        return boxes

    def recognize(self, crop) -> tuple[str, float | None]:
        import numpy as np

        recognizer = self._get_recognizer()
        with _quiet_paddle():
            results = list(recognizer.predict(np.array(crop)))
        if not results:
            return "", None

        result = dict(results[0])
        text = (result.get("rec_text") or "").strip()
        score = result.get("rec_score")
        confidence = round(float(score), 4) if score is not None else None
        return text, confidence
