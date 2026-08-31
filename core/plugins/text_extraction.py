"""
core/plugins/text_extraction.py — the OCR stack as an EvaluationPlugin.

This is a THIN WRAPPER around core/ocr_fallback.py + core/ocr_engines/, not a
rewrite: the multi-engine ensemble/cascade selection, the per-engine skipping,
the confidence-comparison heuristic and its calibration caveat all still live
in core/ocr_fallback.py and are unchanged. Every existing caller
(core/diagram_extractor.py, scripts/benchmark_ocr_engines.py) keeps using
FallbackOCR directly and is unaffected — this module only gives the same
capability a second, uniform front door so the registry can drive it the way
it drives every other evaluation module.

It is deliberately the FIRST plugin: it proves the interface against code
that already exists and already works, rather than against a hypothetical.

SCOPE: extract() only. evaluate() raises NotImplementedError — scoring
extracted text against a reference answer is the next task; the machinery it
will wrap (core/evaluator.py's score_with_embeddings/score_with_llm) already
exists and is still reached through scripts/evaluate_answer.py meanwhile.
"""
from __future__ import annotations

import dataclasses
import time

from core.plugins.base import EvaluationPlugin, EvaluationResult, ExtractionResult
from core.plugins.registry import register

#: Padding added around each detected text region before cropping, so
#: descenders/ascenders clipped by a tight detection box don't cost the
#: recognizer accuracy. Same value and reasoning as
#: core/diagram_extractor.REGION_CROP_PADDING_PX.
REGION_CROP_PADDING_PX = 6


@dataclasses.dataclass(frozen=True)
class PlainText:
    """An already-digital text source — an answer_blocks row whose `content`
    column holds typed text rather than a scan behind `blob_url`.

    Exists so `extract()` never has to guess whether a bare str is a storage
    reference or the answer itself: a str is ALWAYS a blob_url (the repo's
    stable "bucket/key" form, CLAUDE_CONTEXT.md §10), and literal text is
    always wrapped in this. Extracting it is a pass-through at confidence
    1.0 — nothing was read, so nothing can have been misread.
    """

    text: str


@register
class TextExtractionPlugin(EvaluationPlugin):
    """Reads text off a scanned answer region using the fallback-OCR ensemble."""

    name = "text_extraction"

    #: 0.1.0 — extraction only, no scoring behavior to version yet. Bump the
    #: MINOR when evaluate() lands, and again whenever a change moves scores.
    version = "0.1.0"

    #: OCR selection strategy handed to core/ocr_fallback.py. "ensemble"
    #: (run every engine, keep the best-confidence read) is the accurate
    #: default; see that module's docstring for the cost tradeoff against
    #: "cascade" and for why comparing engine confidences is a heuristic.
    strategy = "ensemble"

    def __init__(self):
        # Cheap: no engine, model, or binary is touched here. The whole
        # roster is built lazily on first real use, for the same reason
        # core/diagram_extractor._get_fallback_ocr() is lazy — importing
        # paddleocr/pytesseract and loading their models is expensive, and a
        # stub run must never pay that cost.
        self._ocr = None

    def _get_ocr(self):
        if self._ocr is None:
            from core.ocr_fallback import FallbackOCR
            self._ocr = FallbackOCR()
        return self._ocr

    def supports(self, block_type: str) -> bool:
        """Handles answer_blocks.block_type='text' — a written/typed answer.
        'diagram' belongs to the diagram pipeline (core/diagram_extractor.py),
        which uses the same OCR stack but produces a graph, not a string."""
        return block_type == "text"

    def extract(self, source, *, stub: bool = False) -> ExtractionResult:
        """Reads one text source into a plain string.

        `source` may be:
          - a PlainText  — already-digital text; passed through verbatim.
          - a str        — a storage blob_url ("bucket/key"): the image is
                           fetched, every text region detected, and each
                           region read across all registered OCR engines.
          - a PIL Image  — one already-cropped region, read directly. This is
                           the "image region" entry point; nothing is fetched.

        ExtractionResult.content is the recognized text; .confidence is the
        mean of the winning per-region confidences (regions whose engine
        reported none are excluded from that mean, and counted in
        .metrics["regions_without_confidence"]).
        """
        started = time.perf_counter()

        if stub:
            return self._stub_extraction(source, started)

        if isinstance(source, PlainText):
            return ExtractionResult(
                content=source.text,
                confidence=1.0,
                metrics=self._metrics(started, mode="plain_text", region_count=0),
            )

        if isinstance(source, str):
            from core.diagram_extractor import _load_image

            return self._read_image(self._get_ocr(), _load_image(source), started)

        # Anything else is treated as a single pre-cropped PIL Image region.
        ocr = self._get_ocr()
        result = ocr.recognize(source, strategy=self.strategy)
        return ExtractionResult(
            content=result.text,
            confidence=result.confidence if result.confidence is not None else 0.0,
            metrics=self._metrics(
                started,
                mode="region",
                region_count=1,
                engines=[result.engine],
                regions_without_confidence=int(result.confidence is None),
            ),
        )

    def _read_image(self, ocr, image, started) -> ExtractionResult:
        """Full-image path: detect every text region, read each one, and join
        them in the detector's reading order (top-to-bottom, then
        left-to-right — see core/ocr_engines/base.py's detect_regions
        contract). Empty reads are dropped rather than becoming blank lines."""
        boxes = ocr.detect_regions(image)

        texts, confidences, engines = [], [], []
        for x, y, w, h in boxes:
            left = max(0, x - REGION_CROP_PADDING_PX)
            top = max(0, y - REGION_CROP_PADDING_PX)
            right = min(image.width, x + w + REGION_CROP_PADDING_PX)
            bottom = min(image.height, y + h + REGION_CROP_PADDING_PX)

            result = ocr.recognize(image.crop((left, top, right, bottom)),
                                   strategy=self.strategy)
            if not result.text:
                continue
            texts.append(result.text)
            engines.append(result.engine)
            if result.confidence is not None:
                confidences.append(result.confidence)

        return ExtractionResult(
            content="\n".join(texts),
            confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            metrics=self._metrics(
                started,
                mode="image",
                region_count=len(texts),
                engines=engines,
                regions_without_confidence=len(texts) - len(confidences),
                detected_regions=len(boxes),
            ),
        )

    def _stub_extraction(self, source, started) -> ExtractionResult:
        """Deterministic fake read — no image fetch, no engine roster, no
        model load. PlainText still passes through (there's nothing to fake
        about text that was never OCR'd); anything else yields a fixed
        "[STUB]"-prefixed string, so it's obvious in output and logs that
        this isn't a real reading of the source. Mirrors
        core/diagram_extractor.stub_extract()'s convention."""
        if isinstance(source, PlainText):
            text, confidence = source.text, 1.0
        else:
            text, confidence = "[STUB] extracted answer text", 1.0
        return ExtractionResult(
            content=text,
            confidence=confidence,
            metrics=self._metrics(started, mode="stub", region_count=0),
        )

    def _metrics(self, started, *, mode: str, region_count: int,
                 engines: list[str] | None = None,
                 regions_without_confidence: int = 0,
                 detected_regions: int | None = None) -> dict:
        """Extraction-side metrics. These are merged into the evaluation's
        metrics on the way to evaluation_results.metrics JSONB (migration
        010), so every value here stays JSON-serializable.

        "engines" records WHICH OCR engine won each region — the same
        provenance core/diagram_extractor.py stores per node as
        "ocr_engine", and the reason a low score can later be blamed on a
        specific engine rather than on the scoring model."""
        metrics = {
            "plugin": self.name,
            "plugin_version": self.version,
            "mode": mode,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "region_count": region_count,
            "regions_without_confidence": regions_without_confidence,
        }
        if engines is not None:
            metrics["ocr_strategy"] = self.strategy
            metrics["engines"] = engines
        if detected_regions is not None:
            metrics["detected_regions"] = detected_regions
        return metrics

    def evaluate(self, extracted: ExtractionResult, reference, *,
                 stub: bool = False) -> EvaluationResult:
        raise NotImplementedError(
            "text_extraction scores nothing yet — this plugin currently wraps the "
            "extraction half (core/ocr_fallback.py) only. Text scoring still runs "
            "through core/evaluator.py (score_with_embeddings / score_with_llm) via "
            "scripts/evaluate_answer.py; folding it in behind this method is the "
            "next step."
        )
