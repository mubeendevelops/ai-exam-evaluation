"""
core/ocr_fallback.py — dynamic multi-engine OCR selection ("fallback OCR").

Rather than committing to one OCR engine, this runs every registered
OCREngine plugin (core/ocr_engines/) against the same cropped region and
picks the best-scoring read at runtime, per region — the same spirit as a
boosting ensemble: no single engine is best on every handwriting style, so
combine several weak/imperfect ones and let per-input scoring pick the
winner instead of betting the whole pipeline on one model's blind spots.

Two selection strategies are supported:

  - "ensemble" (default): run every engine, keep the highest-confidence
    non-empty read. Most accurate, since it always considers every engine,
    but costs one recognize() call per engine per region.
  - "cascade": try engines in priority order, return the first read whose
    confidence clears `min_confidence`. Cheaper (usually stops after the
    first engine) at the cost of occasionally settling for a "good enough"
    read from an earlier engine when a later one would have done better.

IMPORTANT CALIBRATION CAVEAT: each engine reports confidence on its own
uncalibrated scale (see core/ocr_engines/base.py) — PaddleOCR's rec_score
and Tesseract's normalized word-confidence average are not guaranteed to
mean the same thing at the same number. Comparing them directly, as
"ensemble" mode does, is a heuristic, not a proven-correct ranking.
scripts/benchmark_ocr_engines.py exists specifically to measure, on a
labeled test set, whether raw-confidence comparison actually picks the more
accurate engine per handwriting style — treat its output as the source of
truth for tuning `min_confidence` or replacing this heuristic with a
per-engine calibration offset, not this module's docstring.
"""
from __future__ import annotations

import dataclasses

from core.ocr_engines.base import OCREngine


@dataclasses.dataclass
class OCRResult:
    text: str
    confidence: float | None
    engine: str


def default_engines() -> list[OCREngine]:
    """Constructs the standard engine roster. Engines are instantiated
    (cheap — no model/binary is loaded yet, see each engine's own lazy
    __get_*/_ensure_binary loading) but not run here, so listing an engine
    whose binary/model isn't installed is safe; it only fails, and gets
    skipped, when FallbackOCR.recognize() actually calls it."""
    from core.ocr_engines.paddleocr_engine import PaddleOCREngine
    from core.ocr_engines.tesseract_engine import TesseractEngine

    return [PaddleOCREngine(), TesseractEngine()]


class FallbackOCR:
    """Drives a list of OCREngine plugins and picks a result per crop. See
    module docstring for the "ensemble" vs "cascade" strategy tradeoff."""

    def __init__(self, engines: list[OCREngine] | None = None):
        self.engines = engines if engines is not None else default_engines()

    def detect_regions(self, image) -> list[tuple[int, int, int, int]]:
        """Region-finding (WHERE the labels are) uses only the first
        registered engine, not the fallback logic below — layout detection
        on a full diagram is a different problem from reading one already-
        cropped line, and isn't the axis this framework targets (see module
        docstring: the ensemble is about recognition accuracy per
        handwriting style, not detection). Swap engine order in
        default_engines() to change which engine does detection."""
        return self.engines[0].detect_regions(image)

    def recognize(
        self,
        crop,
        strategy: str = "ensemble",
        min_confidence: float = 0.5,
    ) -> OCRResult:
        """Reads one cropped region across all registered engines and
        returns the winning (text, confidence, engine_name).

        An engine that raises (missing binary/model, decode failure, etc.)
        or returns empty text is skipped, not fatal — the whole point of
        having more than one engine is that the pipeline keeps working when
        one of them can't read a given input."""
        if strategy not in ("ensemble", "cascade"):
            raise ValueError(f"Unknown strategy {strategy!r}; expected 'ensemble' or 'cascade'")

        results: list[OCRResult] = []
        for engine in self.engines:
            try:
                text, confidence = engine.recognize(crop)
            except Exception:
                continue  # this engine can't read this input — try the next
            if not text:
                continue

            result = OCRResult(text=text, confidence=confidence, engine=engine.name)
            results.append(result)

            if strategy == "cascade" and confidence is not None and confidence >= min_confidence:
                return result

        if not results:
            return OCRResult(text="", confidence=None, engine="none")

        # "ensemble": highest self-reported confidence wins (see module
        # docstring's calibration caveat). An engine with no confidence
        # score at all ranks below any engine that reported one.
        results.sort(key=lambda r: r.confidence if r.confidence is not None else -1.0, reverse=True)
        return results[0]
