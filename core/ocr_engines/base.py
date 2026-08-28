"""
core/ocr_engines/base.py — the plugin interface every OCR engine implements.

core/ocr_fallback.py drives engines dynamically through this interface: it
doesn't know or care whether a given engine is PaddleOCR, Tesseract, or
something added later — it only calls detect_regions()/recognize() and
compares the confidence each engine reports. Adding a new engine means
writing one class here and registering it in core/ocr_fallback.py's
DEFAULT_ENGINES list; nothing else in the pipeline changes.
"""
from __future__ import annotations

import abc


class OCREngine(abc.ABC):
    """One pluggable OCR backend. An engine is stateful only in that it lazily
    loads its own model/binary on first use and caches it (see
    PaddleOCREngine/TesseractEngine) — callers can hold a single instance and
    reuse it across many images."""

    #: Short, stable identifier used in logs, DB records, and benchmark
    #: reports. Must be unique across registered engines.
    name: str

    @abc.abstractmethod
    def detect_regions(self, image) -> list[tuple[int, int, int, int]]:
        """Finds candidate text-label bounding boxes in a full PIL Image.
        Returns (x, y, w, h) boxes, top-to-bottom then left-to-right."""

    @abc.abstractmethod
    def recognize(self, crop) -> tuple[str, float | None]:
        """Reads the text in one cropped PIL Image region. Returns
        (text, confidence) where confidence is this engine's own
        self-reported score in [0.0, 1.0], or None if the engine doesn't
        provide one. Confidence scales are NOT calibrated to be comparable
        across engines (see core/ocr_fallback.py's module docstring) — each
        engine's score is only meaningful relative to its own other reads."""
