"""
core/ocr_engines/tesseract_engine.py — Tesseract as an OCREngine plugin.

Added alongside PaddleOCR (see core/ocr_fallback.py) so the pipeline isn't
betting everything on one engine's handwriting model: Tesseract's LSTM
recognizer was trained mostly on printed text and is generally weaker than
PaddleOCR's handwriting-tuned checkpoint on real handwriting, but it
occasionally reads a region PaddleOCR misses (and vice versa) — exactly the
per-input variance the fallback framework is built to exploit. Actual
head-to-head numbers per handwriting style live in
scripts/benchmark_ocr_engines.py's output, not asserted here.

Requires the `tesseract-ocr` system binary (not just the `pytesseract` pip
package, which is a thin wrapper that shells out to it) — see
requirements.txt / README for the install command. Raises a clear
RuntimeError instead of a cryptic pytesseract error if the binary is
missing.
"""
from __future__ import annotations

from core.ocr_engines.base import OCREngine

# Tesseract's own confidence scores are 0-100 (or -1 for "no confidence"),
# not PaddleOCR's 0.0-1.0 — normalized to match the OCREngine contract's
# [0.0, 1.0] convention so callers (core/ocr_fallback.py) can compare engine
# confidences without special-casing each engine's native scale. See the
# base.py note: even after normalizing the scale, the underlying meaning of
# "confidence" still isn't calibrated to be cross-engine comparable.
_MAX_TESSERACT_CONFIDENCE = 100.0

# Page-segmentation mode: 7 = "treat the image as a single text line" — the
# right mode for a pre-cropped label/line region (this codebase's actual
# input to recognize()), not a full page. 11 ("sparse text") is used for
# detect_regions() instead, since that call gets a full diagram image with
# scattered handwritten labels rather than one line of running text.
_RECOGNIZE_CONFIG = "--psm 7"
_DETECT_CONFIG = "--psm 11"


class TesseractEngine(OCREngine):
    name = "tesseract"

    def __init__(self):
        self._checked_binary = False

    def _ensure_binary(self):
        """Fails fast with an actionable message if the tesseract-ocr system
        package isn't installed, rather than letting pytesseract raise its
        own less-obvious TesseractNotFoundError deep inside predict()."""
        if self._checked_binary:
            return
        import shutil
        if shutil.which("tesseract") is None:
            raise RuntimeError(
                "TesseractEngine requires the 'tesseract-ocr' system package "
                "(the 'tesseract' binary), which is not on PATH. Install it "
                "with, e.g., 'sudo apt-get install -y tesseract-ocr' (Debian/"
                "Ubuntu) — the 'pytesseract' pip package alone only wraps "
                "the binary, it does not include it."
            )
        self._checked_binary = True

    def detect_regions(self, image) -> list[tuple[int, int, int, int]]:
        import pytesseract

        self._ensure_binary()
        data = pytesseract.image_to_data(
            image, config=_DETECT_CONFIG, output_type=pytesseract.Output.DICT
        )

        boxes = []
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text:
                continue
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            boxes.append((x, y, w, h))

        boxes.sort(key=lambda b: (b[1], b[0]))
        return boxes

    def recognize(self, crop) -> tuple[str, float | None]:
        import pytesseract

        self._ensure_binary()
        data = pytesseract.image_to_data(
            crop, config=_RECOGNIZE_CONFIG, output_type=pytesseract.Output.DICT
        )

        words, confidences = [], []
        n = len(data.get("text", []))
        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            words.append(word)
            conf = data["conf"][i]
            try:
                conf = float(conf)
            except (TypeError, ValueError):
                conf = -1.0
            if conf >= 0:
                confidences.append(conf)

        text = " ".join(words).strip()
        if not text:
            return "", None

        confidence = (
            round(sum(confidences) / len(confidences) / _MAX_TESSERACT_CONFIDENCE, 4)
            if confidences else None
        )
        return text, confidence
