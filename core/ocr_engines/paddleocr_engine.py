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

import os

from core.ocr_engines.base import OCREngine
from core.paddle_workarounds import construct, quiet_paddle

# See core/diagram_extractor.py's original docstring (now moved here) for
# the reasoning behind every constant below — server det / mobile en rec,
# lowered detection thresholds. oneDNN is disabled by core/paddle_workarounds
# (crashes otherwise on this repo's pinned paddlepaddle — see that module).
_DET_MODEL_NAME = os.environ.get("PADDLEOCR_DET_MODEL", "PP-OCRv5_server_det")
_REC_MODEL_NAME = os.environ.get("PADDLEOCR_REC_MODEL", "en_PP-OCRv5_mobile_rec")

DETECT_THRESH = 0.2
DETECT_BOX_THRESH = 0.3
DETECT_UNCLIP_RATIO = 2.0


class PaddleOCREngine(OCREngine):
    name = "paddleocr"

    def __init__(self):
        self._detector = None
        self._recognizer = None

    def _get_detector(self):
        if self._detector is None:
            from paddleocr import TextDetection
            self._detector = construct(
                TextDetection,
                model_name=_DET_MODEL_NAME,
                thresh=DETECT_THRESH,
                box_thresh=DETECT_BOX_THRESH,
                unclip_ratio=DETECT_UNCLIP_RATIO,
            )
        return self._detector

    def _get_recognizer(self):
        if self._recognizer is None:
            from paddleocr import TextRecognition
            self._recognizer = construct(TextRecognition, model_name=_REC_MODEL_NAME)
        return self._recognizer

    def detect_regions(self, image) -> list[tuple[int, int, int, int]]:
        import numpy as np

        detector = self._get_detector()
        arr = np.array(image)  # RGB, as PaddleOCR expects

        with quiet_paddle():
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
        with quiet_paddle():
            results = list(recognizer.predict(np.array(crop)))
        if not results:
            return "", None

        result = dict(results[0])
        text = (result.get("rec_text") or "").strip()
        score = result.get("rec_score")
        confidence = round(float(score), 4) if score is not None else None
        return text, confidence
