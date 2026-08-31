"""Characterization tests for core/ocr_fallback.py — fast, no real OCR.

FakeEngine subclasses implement the core.ocr_engines.base.OCREngine
interface with scripted returns and call counters, injected directly via
FallbackOCR(engines=[...]) so no PaddleOCR/Tesseract model or binary is
ever loaded.
"""
from __future__ import annotations

import pytest

from core.ocr_engines.base import OCREngine
from core.ocr_fallback import FallbackOCR, OCRResult


class FakeEngine(OCREngine):
    """Scripted OCREngine: recognize() returns a fixed (text, confidence)
    or raises, and counts how many times it was called."""

    def __init__(self, name, text="", confidence=None, raises=False):
        self.name = name
        self._text = text
        self._confidence = confidence
        self._raises = raises
        self.call_count = 0
        self.detect_calls = 0

    def detect_regions(self, image):
        self.detect_calls += 1
        return [(0, 0, 10, 10)]

    def recognize(self, crop):
        self.call_count += 1
        if self._raises:
            raise RuntimeError(f"{self.name} blew up")
        return self._text, self._confidence


# ---------------------------------------------------------------------------
# ensemble strategy
# ---------------------------------------------------------------------------

def test_ensemble_highest_confidence_wins():
    low = FakeEngine("low", text="low text", confidence=0.3)
    high = FakeEngine("high", text="high text", confidence=0.9)
    mid = FakeEngine("mid", text="mid text", confidence=0.5)

    fb = FallbackOCR(engines=[low, high, mid])
    result = fb.recognize(None, strategy="ensemble")

    assert result == OCRResult(text="high text", confidence=0.9, engine="high")
    # ensemble calls every engine
    assert low.call_count == 1
    assert high.call_count == 1
    assert mid.call_count == 1


def test_ensemble_none_confidence_ranks_below_scored_engine():
    unscored = FakeEngine("unscored", text="unscored text", confidence=None)
    scored = FakeEngine("scored", text="scored text", confidence=0.01)

    fb = FallbackOCR(engines=[unscored, scored])
    result = fb.recognize(None, strategy="ensemble")

    assert result.engine == "scored"
    assert result.confidence == 0.01


def test_ensemble_raising_engine_is_skipped_not_fatal():
    bad = FakeEngine("bad", raises=True)
    good = FakeEngine("good", text="good text", confidence=0.7)

    fb = FallbackOCR(engines=[bad, good])
    result = fb.recognize(None, strategy="ensemble")

    assert result == OCRResult(text="good text", confidence=0.7, engine="good")


def test_ensemble_empty_text_read_is_skipped():
    empty = FakeEngine("empty", text="", confidence=0.99)
    real = FakeEngine("real", text="real text", confidence=0.1)

    fb = FallbackOCR(engines=[empty, real])
    result = fb.recognize(None, strategy="ensemble")

    assert result == OCRResult(text="real text", confidence=0.1, engine="real")


# ---------------------------------------------------------------------------
# cascade strategy
# ---------------------------------------------------------------------------

def test_cascade_returns_first_engine_clearing_threshold_without_calling_later():
    first = FakeEngine("first", text="first text", confidence=0.8)
    second = FakeEngine("second", text="second text", confidence=0.95)

    fb = FallbackOCR(engines=[first, second])
    result = fb.recognize(None, strategy="cascade", min_confidence=0.5)

    assert result == OCRResult(text="first text", confidence=0.8, engine="first")
    assert first.call_count == 1
    assert second.call_count == 0  # never reached


def test_cascade_falls_through_to_confidence_sort_when_nothing_clears_threshold():
    weak1 = FakeEngine("weak1", text="weak1 text", confidence=0.2)
    weak2 = FakeEngine("weak2", text="weak2 text", confidence=0.4)

    fb = FallbackOCR(engines=[weak1, weak2])
    result = fb.recognize(None, strategy="cascade", min_confidence=0.9)

    # both engines get called since neither clears the threshold
    assert weak1.call_count == 1
    assert weak2.call_count == 1
    # falls back to confidence sort over what it collected
    assert result == OCRResult(text="weak2 text", confidence=0.4, engine="weak2")


def test_cascade_skips_raising_and_empty_engines_then_returns_first_qualifying():
    bad = FakeEngine("bad", raises=True)
    empty = FakeEngine("empty", text="", confidence=0.99)
    qualifies = FakeEngine("qualifies", text="qualifies text", confidence=0.6)

    fb = FallbackOCR(engines=[bad, empty, qualifies])
    result = fb.recognize(None, strategy="cascade", min_confidence=0.5)

    assert result == OCRResult(text="qualifies text", confidence=0.6, engine="qualifies")


# ---------------------------------------------------------------------------
# strategy validation
# ---------------------------------------------------------------------------

def test_unknown_strategy_raises_value_error():
    fb = FallbackOCR(engines=[FakeEngine("e", text="t", confidence=0.5)])
    with pytest.raises(ValueError, match="Unknown strategy"):
        fb.recognize(None, strategy="best")


# ---------------------------------------------------------------------------
# all engines fail
# ---------------------------------------------------------------------------

def test_all_engines_fail_returns_none_result():
    fb = FallbackOCR(engines=[
        FakeEngine("raiser", raises=True),
        FakeEngine("empty", text="", confidence=0.9),
    ])
    result = fb.recognize(None, strategy="ensemble")
    assert result == OCRResult(text="", confidence=None, engine="none")


# ---------------------------------------------------------------------------
# detect_regions
# ---------------------------------------------------------------------------

def test_detect_regions_delegates_to_first_engine_only():
    first = FakeEngine("first", text="x", confidence=0.5)
    second = FakeEngine("second", text="y", confidence=0.5)

    fb = FallbackOCR(engines=[first, second])
    regions = fb.detect_regions("fake-image")

    assert regions == [(0, 0, 10, 10)]
    assert first.detect_calls == 1
    assert second.detect_calls == 0
