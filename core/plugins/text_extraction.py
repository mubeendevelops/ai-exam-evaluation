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

evaluate() blends four independently-reported signals — semantic
(core/evaluator.py::score_with_embeddings), LLM
(core/evaluator.py::score_with_llm), keyword coverage (question_keywords,
fuzzy-matched via core/text_match.py — the same helper
core/diagram_evaluator.py uses for glossary matching), and rubric coverage
(structured criteria parsed out of the reference_answer_variant, or a
whole-answer fallback when none parse). None of the scoring machinery is
reimplemented here — this module only wraps it and combines the results.
"""
from __future__ import annotations

import dataclasses
import re
import time

from core import text_match
from core.plugins.base import EvaluationPlugin, EvaluationResult, ExtractionResult
from core.plugins.registry import register

#: Default blend weights for --method blended. Chosen so the two
#: model-backed signals (semantic, llm) dominate — they judge the whole
#: answer's meaning — while keyword/rubric coverage act as supplementary,
#: explainable corroboration rather than equal partners. Any subset of
#: these may be overridden via --weights; whichever signals are actually
#: available (e.g. a question with no keywords) have their weight
#: redistributed proportionally among the rest — see blend_scores().
DEFAULT_BLEND_WEIGHTS = {"semantic": 0.30, "llm": 0.40, "keyword": 0.15, "rubric": 0.15}

#: phrase_in_text coverage_threshold for the two NEW signals — what
#: fraction of a term/criterion's own words must each be found (fuzzy-
#: matched) somewhere in the student's text. Keywords are usually single
#: words or short, essential multi-word terms ("Address Bus"), so every
#: word is required. Rubric criteria are longer free-text fragments where
#: some wording drift is expected, so partial coverage still counts.
KEYWORD_MATCH_THRESHOLD = 1.0
RUBRIC_MATCH_THRESHOLD = 0.7

#: Below this blended confidence, the result is flagged for human review
#: (EvaluationResult.metrics["low_confidence"]) rather than trusted as-is.
LOW_CONFIDENCE_THRESHOLD = 0.6

#: The two model-backed signals whose disagreement drives confidence — see
#: derive_confidence()'s docstring for why keyword/rubric aren't part of
#: this check.
CORE_DIVERGENCE_SIGNALS = ("semantic", "llm")

KEYWORD_MODEL_NAME = "fuzzy_keyword_coverage_v1"
RUBRIC_MODEL_NAME = "fuzzy_rubric_coverage_v1"

#: Matches one bulleted/numbered rubric line, e.g. "- Explains X (2 marks)"
#: or "3) Correct formula for Y [1.5 pts]". Group 1 is everything after the
#: bullet marker.
_BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
#: Matches a trailing "(N marks)" / "[N pts]" / "(N points)" weight marker,
#: case-insensitive, allowing a decimal weight.
_RUBRIC_WEIGHT_RE = re.compile(r"[\(\[]\s*(\d+(?:\.\d+)?)\s*(?:marks?|pts?|points?)\s*[\)\]]",
                                re.IGNORECASE)

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


@dataclasses.dataclass(frozen=True)
class TextReference:
    """Everything evaluate() needs about the expected answer for one
    question — assembled by the caller (a script) from the DB. evaluate()
    itself does no DB I/O, the same division of responsibility
    core/diagram_evaluator.compare_diagrams() uses: glossary_terms is a
    plain argument there too, not something it queries for itself.

    `keywords` is the question_keywords/keywords join, pre-loaded by the
    caller: [{"term": str, "weight": float}, ...]. Empty when the question
    has no keywords defined yet — the keyword signal degrades to
    "unavailable", not "zero" (see score_keywords()).
    """

    reference_text: str
    marks_max: float
    keywords: list = dataclasses.field(default_factory=list)
    question_id: str | None = None
    variant_id: str | None = None


def parse_rubric(reference_text: str) -> list[dict] | None:
    """Parses `reference_text` into weighted rubric criteria, if it is
    structured as a bulleted/numbered list with a per-item mark weight —
    e.g. "- Explains the LIFO property (2 marks)". Returns None (never an
    empty list) when nothing parses, so callers can tell "no structured
    rubric, fall back to whole-answer scoring" apart from "a rubric that
    happens to have zero criteria" (which would be a bug, not a fallback).
    """
    criteria = []
    for line in reference_text.splitlines():
        bullet = _BULLET_RE.match(line)
        if not bullet:
            continue
        body = bullet.group(1).strip()
        weight_match = _RUBRIC_WEIGHT_RE.search(body)
        if not weight_match:
            continue
        weight = float(weight_match.group(1))
        if weight <= 0:
            continue
        criterion = (body[:weight_match.start()] + body[weight_match.end():]).strip(" -:\t")
        if not criterion:
            continue
        criteria.append({"criterion": criterion, "weight": weight})
    return criteria or None


def score_keywords(student_text: str, keywords: list[dict], *,
                    threshold: float = KEYWORD_MATCH_THRESHOLD) -> dict:
    """Keyword coverage: what fraction of the question's weighted keywords
    show up, fuzzy-matched (tolerant of OCR/typo noise), in the student's
    text. `keywords`: [{"term": str, "weight": float}, ...].

    Returns {"coverage": None, ...} — not 0.0 — when the question has no
    keywords at all, since "no signal" and "zero coverage" mean different
    things to a blend that renormalizes over available signals.
    """
    if not keywords:
        return {"coverage": None, "matched": [], "missed": [], "total_weight": 0.0}

    total_weight = sum(kw.get("weight", 1.0) for kw in keywords) or float(len(keywords))
    matched, missed = [], []
    matched_weight = 0.0
    for kw in keywords:
        weight = kw.get("weight", 1.0)
        # short_max_edits=0: disables text_match's short-string edit-distance
        # shortcut. That shortcut exists to correct OCR misreads of a single
        # glyph (diagram_evaluator's "CPU"->"CPV"); here both sides are
        # already-recognized real words, where a 1-edit-distance neighbor
        # can be a different keyword entirely (e.g. "FIFO"/"LIFO") — see
        # phrase_in_text's docstring.
        hit = text_match.phrase_in_text(kw["term"], student_text, coverage_threshold=threshold,
                                         short_max_edits=0)
        if hit:
            matched_weight += weight
            matched.append({"term": kw["term"], "weight": weight, **hit})
        else:
            missed.append({"term": kw["term"], "weight": weight})

    return {
        "coverage": round(matched_weight / total_weight, 4),
        "matched": matched,
        "missed": missed,
        "total_weight": total_weight,
    }


def score_rubric(student_text: str, reference_text: str, *,
                  threshold: float = RUBRIC_MATCH_THRESHOLD) -> dict:
    """Rubric coverage. If `reference_text` parses into structured, weighted
    criteria (parse_rubric), scores fuzzy coverage of each independently;
    otherwise falls back to a single whole-answer fuzzy-similarity score
    against the reference text (mode="whole_answer") — the "if not,
    fall back to whole-answer scoring" behavior."""
    criteria = parse_rubric(reference_text)
    if criteria:
        total_weight = sum(c["weight"] for c in criteria) or float(len(criteria))
        matched_weight = 0.0
        breakdown = []
        for c in criteria:
            hit = text_match.phrase_in_text(c["criterion"], student_text, coverage_threshold=threshold,
                                             short_max_edits=0)
            row = {"criterion": c["criterion"], "weight": c["weight"], "matched": hit is not None}
            if hit:
                matched_weight += c["weight"]
                row["word_coverage"] = hit["coverage"]
            breakdown.append(row)
        return {
            "mode": "structured",
            "coverage": round(matched_weight / total_weight, 4),
            "criteria": breakdown,
            "total_weight": total_weight,
        }

    ratio = text_match.fuzzy_ratio(student_text.strip().lower(), reference_text.strip().lower())
    return {"mode": "whole_answer", "coverage": round(ratio, 4), "criteria": []}


def blend_scores(fractions: dict, weights: dict) -> tuple[float, dict]:
    """Weighted average of 0..1 score fractions, renormalized over
    whichever signals are actually present (fractions[name] is not None).
    A question with no keywords contributes coverage=None for "keyword",
    and that weight is redistributed proportionally to the remaining
    signals rather than silently counted as a zero score.

    Returns (blended_fraction, effective_weights) — effective_weights (the
    renormalized weights actually used) is stored in metrics so a teacher
    can see exactly how the blend was computed, not just its result.
    """
    available = {name: value for name, value in fractions.items()
                 if value is not None and name in weights}
    total_weight = sum(weights[name] for name in available) or 1.0
    effective_weights = {name: round(weights[name] / total_weight, 4) for name in available}
    blended_fraction = sum(fractions[name] * effective_weights[name] for name in available)
    return blended_fraction, effective_weights


def derive_confidence(fractions: dict) -> tuple[float, bool]:
    """Confidence from agreement between signals — primarily semantic vs.
    LLM (CORE_DIVERGENCE_SIGNALS), the two independent model-backed
    judgments of the whole answer's meaning. Keyword/rubric coverage are
    cheap lexical heuristics with their own, different kind of noise (a
    paraphrased-but-correct answer legitimately scores low on them), so
    they're deliberately excluded from the primary disagreement check and
    only used as a fallback when semantic/llm aren't both available.

    Returns (confidence, low_confidence) — low_confidence is True below
    LOW_CONFIDENCE_THRESHOLD, the signal a caller uses to flag a row for
    human review.
    """
    core = [fractions[name] for name in CORE_DIVERGENCE_SIGNALS if fractions.get(name) is not None]
    if len(core) >= 2:
        divergence = max(core) - min(core)
    else:
        values = [value for value in fractions.values() if value is not None]
        divergence = (max(values) - min(values)) if len(values) >= 2 else 0.0
    confidence = round(max(0.0, 1.0 - divergence), 4)
    return confidence, confidence < LOW_CONFIDENCE_THRESHOLD


def parse_weights(spec: str) -> dict:
    """Parses a --weights flag value, e.g.
    "semantic=0.3,llm=0.4,keyword=0.15,rubric=0.15". Only the signals named
    are overridden — callers merge the result onto DEFAULT_BLEND_WEIGHTS,
    so a partial override (e.g. just "llm=0.5") leaves the rest at their
    defaults rather than requiring all four every time."""
    weights = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"invalid --weights entry {part!r}, expected name=value")
        key, _, value = part.partition("=")
        key = key.strip()
        if key not in DEFAULT_BLEND_WEIGHTS:
            raise ValueError(f"unknown weight {key!r}, expected one of {sorted(DEFAULT_BLEND_WEIGHTS)}")
        try:
            weights[key] = float(value)
        except ValueError as e:
            raise ValueError(f"invalid weight value for {key!r}: {value!r}") from e
    if not weights:
        raise ValueError("--weights must specify at least one signal")
    return weights


@register
class TextExtractionPlugin(EvaluationPlugin):
    """Reads text off a scanned answer region using the fallback-OCR ensemble."""

    name = "text_extraction"

    #: 0.1.0 was extraction-only. 0.2.0 adds evaluate()'s blended scoring —
    #: a MINOR bump since it's new capability, not a fix. Bump MINOR again
    #: for any change that moves scores (a new signal, a re-tuned default
    #: weight/threshold), PATCH for fixes that don't.
    version = "0.2.0"

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

    #: --method choices accepted by evaluate(). "blended" combines all four
    #: signals; the other three isolate one signal each — kept so
    #: scripts/evaluate_answer.py's original --method embeddings|llm still
    #: produce the same kind of single-signal score as before, plus the new
    #: --method keyword for keyword-only scoring.
    METHODS = ("blended", "embeddings", "llm", "keyword")

    #: CLI/method name -> internal signal key. "embeddings" is the
    #: pre-existing --method name (core/evaluator.py::score_with_embeddings);
    #: internally that signal is called "semantic" since it's one of four
    #: rather than the only option.
    _SIGNAL_NAME_FOR_METHOD = {"embeddings": "semantic", "llm": "llm", "keyword": "keyword"}

    def evaluate(self, extracted: ExtractionResult, reference: TextReference, *,
                 stub: bool = False, stub_llm: bool = False,
                 method: str = "blended", weights: dict | None = None) -> EvaluationResult:
        """Scores extracted student text against `reference` (a
        TextReference — see its docstring for what the caller must load
        from the DB first).

        method="blended" (default) computes all four signals — semantic,
        llm, keyword, rubric — and combines them per `weights` (defaults to
        DEFAULT_BLEND_WEIGHTS, overridable per-signal). Any other method
        computes and returns only that one signal, unblended — the
        pre-existing --method embeddings|llm behavior, plus keyword-only.

        stub=True fakes every signal touched (no embedding model load, no
        LLM call — the base-class contract). stub_llm=True fakes only the
        llm signal while the rest run for real, so blended scoring can be
        exercised without spending Groq API quota.

        EvaluationResult.metrics["signals"] carries each computed signal's
        own score/model/explanation/metrics dict — the breakdown a teacher
        needs to see WHY a score was given, not just the number.
        """
        if method not in self.METHODS:
            raise ValueError(f"method must be one of {self.METHODS}, got {method!r}")

        started = time.perf_counter()
        student_text = extracted.content or ""
        marks_max = reference.marks_max

        signals = {}
        if method in ("blended", "embeddings"):
            signals["semantic"] = self._score_semantic(student_text, reference, stub=stub)
        if method in ("blended", "llm"):
            signals["llm"] = self._score_llm(student_text, reference, stub=stub or stub_llm)
        if method in ("blended", "keyword"):
            signals["keyword"] = self._score_keyword(student_text, reference)
        if method == "blended":
            signals["rubric"] = self._score_rubric(student_text, reference)

        fractions = {
            name: (sig["score"] / marks_max if marks_max and sig.get("score") is not None else None)
            for name, sig in signals.items()
        }

        effective_weights = None
        if method == "blended":
            active_weights = dict(DEFAULT_BLEND_WEIGHTS)
            if weights:
                active_weights.update(weights)
            blended_fraction, effective_weights = blend_scores(fractions, active_weights)
            score = round(blended_fraction * marks_max, 2)
            confidence, low_confidence = derive_confidence(fractions)
            explanation = self._blended_explanation(
                signals, effective_weights, score, marks_max, low_confidence
            )
            evaluator_model = "blended(" + ",".join(sorted(signals)) + ")"
        else:
            only = signals[self._SIGNAL_NAME_FOR_METHOD[method]]
            if only.get("score") is None:
                raise ValueError(
                    f"method={method!r} has no score available ({only['explanation']}); "
                    f"use --method blended (or embeddings/llm) instead"
                )
            score = only["score"]
            confidence = only.get("confidence", 1.0)
            low_confidence = False
            explanation = only["explanation"]
            evaluator_model = only["model"]

        metrics = {
            "plugin": self.name,
            "plugin_version": self.version,
            "evaluator_model": evaluator_model,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "method": method,
            "signals": signals,
            "low_confidence": low_confidence,
        }
        if effective_weights is not None:
            metrics["weights"] = effective_weights

        return EvaluationResult(
            score=score, max_score=marks_max, confidence=confidence,
            explanation=explanation, metrics=metrics,
        )

    def _score_semantic(self, student_text: str, reference: TextReference, *, stub: bool) -> dict:
        from core import evaluator as core_evaluator

        if stub:
            result = core_evaluator.stub_score(student_text, reference.reference_text, reference.marks_max)
        else:
            result = core_evaluator.score_with_embeddings(
                student_text, reference.reference_text, reference.marks_max
            )
        return {
            "score": result["score"], "explanation": result["explanation"],
            "model": result["model"], "confidence": 1.0, "metrics": result.get("metrics", {}),
        }

    def _score_llm(self, student_text: str, reference: TextReference, *, stub: bool) -> dict:
        from core import evaluator as core_evaluator

        if stub:
            result = core_evaluator.stub_score(student_text, reference.reference_text, reference.marks_max)
        else:
            result = core_evaluator.score_with_llm(
                student_text, reference.reference_text, reference.marks_max
            )
        return {
            "score": result["score"], "explanation": result["explanation"],
            "model": result["model"], "confidence": 1.0, "metrics": result.get("metrics", {}),
        }

    def _score_keyword(self, student_text: str, reference: TextReference) -> dict:
        result = score_keywords(student_text, reference.keywords)
        coverage = result["coverage"]
        if coverage is None:
            return {
                "score": None,
                "explanation": "No keywords defined for this question — keyword signal unavailable.",
                "model": KEYWORD_MODEL_NAME, "confidence": 0.0, "metrics": result,
            }
        score = round(coverage * reference.marks_max, 2)
        explanation = (
            f"Keyword coverage: {len(result['matched'])}/{len(result['matched']) + len(result['missed'])} "
            f"matched ({coverage * 100:.1f}%)."
        )
        return {
            "score": score, "explanation": explanation,
            "model": KEYWORD_MODEL_NAME, "confidence": 1.0, "metrics": result,
        }

    def _score_rubric(self, student_text: str, reference: TextReference) -> dict:
        result = score_rubric(student_text, reference.reference_text)
        score = round(result["coverage"] * reference.marks_max, 2)
        if result["mode"] == "structured":
            explanation = (
                f"Rubric coverage: {result['coverage'] * 100:.1f}% across "
                f"{len(result['criteria'])} scored criteria."
            )
        else:
            explanation = (
                f"Rubric (whole-answer fallback — no structured criteria found in the "
                f"reference answer): {result['coverage'] * 100:.1f}% fuzzy match."
            )
        return {
            "score": score, "explanation": explanation,
            "model": RUBRIC_MODEL_NAME, "confidence": 1.0, "metrics": result,
        }

    def _blended_explanation(self, signals: dict, effective_weights: dict,
                              score: float, marks_max: float, low_confidence: bool) -> str:
        parts = []
        for name in ("semantic", "llm", "keyword", "rubric"):
            sig = signals.get(name)
            if not sig or sig.get("score") is None:
                continue
            weight = effective_weights.get(name)
            weight_note = f", w={weight}" if weight is not None else ""
            parts.append(f"{name}={sig['score']}/{marks_max}{weight_note}")

        review_note = " LOW CONFIDENCE — flagged for human review." if low_confidence else ""
        return f"Blended score {score}/{marks_max} from " + "; ".join(parts) + "." + review_note
