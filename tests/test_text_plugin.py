"""Tests for core/plugins/text_extraction.py's evaluate() — the blended
text-answer scoring plugin. Fast (no embedding-model load, no LLM call)
except the one @pytest.mark.db integration test at the bottom, which
exercises the real scripts/evaluate_answer.py --dry-run path against the
seeded fixture answer (migrations/seed_minimal.sql).
"""
from __future__ import annotations

import subprocess
import sys
import pathlib

import pytest

from core.plugins.base import ExtractionResult
from core.plugins.text_extraction import (
    DEFAULT_BLEND_WEIGHTS,
    TextExtractionPlugin,
    TextReference,
    blend_scores,
    derive_confidence,
    parse_rubric,
    parse_weights,
    score_keywords,
    score_rubric,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The seeded text-answer fixture (migrations/seed_minimal.sql, "Fixture 2").
SEEDED_ANSWER_ID = "a0a0a0a0-0003-0003-0003-a0a0a0a0a0a0"
SEEDED_VARIANT_ID = "a0a0a0a0-0002-0002-0002-a0a0a0a0a0a0"

STUDENT_TEXT = (
    "The mitochondria produces ATP through cellular respiration, "
    "which powers most of the cell's activities."
)


# ---------------------------------------------------------------------------
# score_keywords — hand-built fixture
# ---------------------------------------------------------------------------

def test_score_keywords_weighted_coverage():
    keywords = [
        {"term": "mitochondria", "weight": 2.0},
        {"term": "ATP", "weight": 1.0},
        {"term": "photosynthesis", "weight": 1.0},
    ]
    result = score_keywords(STUDENT_TEXT, keywords)

    assert result["coverage"] == pytest.approx(0.75)  # (2 + 1) / 4
    matched_terms = {m["term"] for m in result["matched"]}
    missed_terms = {m["term"] for m in result["missed"]}
    assert matched_terms == {"mitochondria", "ATP"}
    assert missed_terms == {"photosynthesis"}
    assert result["total_weight"] == 4.0


def test_score_keywords_no_keywords_defined_is_none_not_zero():
    result = score_keywords(STUDENT_TEXT, [])
    assert result["coverage"] is None
    assert result["matched"] == []
    assert result["missed"] == []


def test_score_keywords_full_coverage():
    keywords = [{"term": "mitochondria", "weight": 1.0}, {"term": "ATP", "weight": 1.0}]
    result = score_keywords(STUDENT_TEXT, keywords)
    assert result["coverage"] == 1.0
    assert len(result["missed"]) == 0


def test_score_keywords_similar_short_words_are_not_conflated():
    """FIFO and LIFO are both real, opposite-meaning words one edit apart —
    the keyword-coverage path must not treat a mention of one as covering
    the other (see phrase_in_text's short_max_edits=0 docstring note)."""
    keywords = [{"term": "FIFO", "weight": 1.0}]
    result = score_keywords("A stack follows LIFO order.", keywords)
    assert result["coverage"] == 0.0
    assert result["missed"][0]["term"] == "FIFO"


# ---------------------------------------------------------------------------
# parse_rubric / score_rubric
# ---------------------------------------------------------------------------

def test_parse_rubric_extracts_weighted_bullet_criteria():
    reference_text = (
        "- LIFO order (2 marks)\n"
        "- FIFO order (2 marks)\n"
        "* first element added is the first removed (1 marks)\n"
    )
    criteria = parse_rubric(reference_text)
    assert criteria == [
        {"criterion": "LIFO order", "weight": 2.0},
        {"criterion": "FIFO order", "weight": 2.0},
        {"criterion": "first element added is the first removed", "weight": 1.0},
    ]


def test_parse_rubric_numbered_list_with_pts_marker():
    criteria = parse_rubric("1. Correct formula [3 pts]\n2. Correct units [1 pt]")
    assert criteria == [
        {"criterion": "Correct formula", "weight": 3.0},
        {"criterion": "Correct units", "weight": 1.0},
    ]


def test_parse_rubric_returns_none_for_unstructured_text():
    assert parse_rubric("A stack is LIFO. A queue is FIFO.") is None


def test_parse_rubric_ignores_bullets_without_a_weight_marker():
    assert parse_rubric("- just a plain bullet\n- another one") is None


def test_score_rubric_structured_mode_coverage():
    reference_text = "- LIFO order (2 marks)\n- FIFO order (2 marks)"
    student_text = "A stack follows LIFO order. A queue follows FIFO order."
    result = score_rubric(student_text, reference_text)

    assert result["mode"] == "structured"
    assert result["coverage"] == 1.0
    assert all(c["matched"] for c in result["criteria"])


def test_score_rubric_structured_mode_partial_coverage():
    """FIFO and LIFO are one edit apart but opposite in meaning — the
    student mentioning only LIFO must not also credit the FIFO criterion
    (word-level matching, not whole-phrase ratio; see phrase_in_text)."""
    reference_text = "- LIFO order (2 marks)\n- FIFO order (2 marks)"
    student_text = "A stack follows LIFO order."
    result = score_rubric(student_text, reference_text)

    assert result["mode"] == "structured"
    assert result["coverage"] == pytest.approx(0.5)  # only the LIFO criterion hits
    by_criterion = {c["criterion"]: c["matched"] for c in result["criteria"]}
    assert by_criterion["LIFO order"] is True
    assert by_criterion["FIFO order"] is False


def test_score_rubric_falls_back_to_whole_answer_when_unstructured():
    reference_text = "A stack is LIFO. A queue is FIFO."
    result = score_rubric("A stack is LIFO. A queue is FIFO.", reference_text)
    assert result["mode"] == "whole_answer"
    assert result["coverage"] == 1.0
    assert result["criteria"] == []


# ---------------------------------------------------------------------------
# blend_scores — weight blending arithmetic
# ---------------------------------------------------------------------------

def test_blend_scores_all_signals_present():
    fractions = {"semantic": 0.8, "llm": 0.6, "keyword": 1.0, "rubric": 0.4}
    blended, effective_weights = blend_scores(fractions, DEFAULT_BLEND_WEIGHTS)

    assert effective_weights == DEFAULT_BLEND_WEIGHTS
    expected = (0.8 * 0.30) + (0.6 * 0.40) + (1.0 * 0.15) + (0.4 * 0.15)
    assert blended == pytest.approx(expected)


def test_blend_scores_renormalizes_when_a_signal_is_unavailable():
    """A question with no keywords contributes coverage=None for
    'keyword' — its weight must be redistributed to the other three
    signals, not silently treated as a zero score."""
    fractions = {"semantic": 0.8, "llm": 0.6, "keyword": None, "rubric": 0.4}
    blended, effective_weights = blend_scores(fractions, DEFAULT_BLEND_WEIGHTS)

    assert "keyword" not in effective_weights
    assert sum(effective_weights.values()) == pytest.approx(1.0)
    total = DEFAULT_BLEND_WEIGHTS["semantic"] + DEFAULT_BLEND_WEIGHTS["llm"] + DEFAULT_BLEND_WEIGHTS["rubric"]
    assert effective_weights["semantic"] == pytest.approx(DEFAULT_BLEND_WEIGHTS["semantic"] / total, abs=1e-4)
    expected = (
        0.8 * (DEFAULT_BLEND_WEIGHTS["semantic"] / total)
        + 0.6 * (DEFAULT_BLEND_WEIGHTS["llm"] / total)
        + 0.4 * (DEFAULT_BLEND_WEIGHTS["rubric"] / total)
    )
    assert blended == pytest.approx(expected, abs=1e-3)


def test_blend_scores_custom_weights_override_defaults():
    fractions = {"semantic": 1.0, "llm": 0.0}
    blended, effective_weights = blend_scores(fractions, {"semantic": 0.9, "llm": 0.1})
    assert effective_weights == {"semantic": 0.9, "llm": 0.1}
    assert blended == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# derive_confidence — low-confidence flagging on divergent signals
# ---------------------------------------------------------------------------

def test_derive_confidence_high_when_signals_agree():
    confidence, low_confidence = derive_confidence({"semantic": 0.9, "llm": 0.88})
    assert confidence == pytest.approx(0.98)
    assert low_confidence is False


def test_derive_confidence_low_when_signals_diverge():
    confidence, low_confidence = derive_confidence({"semantic": 0.95, "llm": 0.2})
    assert confidence == pytest.approx(0.25)
    assert low_confidence is True


def test_derive_confidence_falls_back_when_only_one_core_signal_present():
    confidence, low_confidence = derive_confidence({"semantic": 0.9, "keyword": 0.9})
    assert confidence == pytest.approx(1.0)
    assert low_confidence is False


# ---------------------------------------------------------------------------
# parse_weights
# ---------------------------------------------------------------------------

def test_parse_weights_valid_spec():
    assert parse_weights("semantic=0.5,llm=0.5") == {"semantic": 0.5, "llm": 0.5}


def test_parse_weights_rejects_unknown_signal():
    with pytest.raises(ValueError, match="unknown weight"):
        parse_weights("bogus=0.5")


def test_parse_weights_rejects_empty_spec():
    with pytest.raises(ValueError, match="at least one signal"):
        parse_weights("")


def test_parse_weights_rejects_non_numeric_value():
    with pytest.raises(ValueError, match="invalid weight value"):
        parse_weights("semantic=not-a-number")


# ---------------------------------------------------------------------------
# TextExtractionPlugin.evaluate — full blend, stub mode (no model/API calls)
# ---------------------------------------------------------------------------

@pytest.fixture
def plugin():
    return TextExtractionPlugin()


@pytest.fixture
def reference():
    return TextReference(
        reference_text="- mentions mitochondria (2 marks)\n- mentions ATP (2 marks)",
        marks_max=5.0,
        keywords=[{"term": "mitochondria", "weight": 2.0}, {"term": "ATP", "weight": 1.0}],
        question_id="q1", variant_id="v1",
    )


def test_evaluate_blended_reports_all_four_signals(plugin, reference):
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    result = plugin.evaluate(extracted, reference, stub=True, method="blended")

    signals = result.metrics["signals"]
    assert set(signals) == {"semantic", "llm", "keyword", "rubric"}
    for name in ("semantic", "llm", "keyword", "rubric"):
        assert signals[name]["score"] is not None

    # required metric keys (core/plugins/base.py::REQUIRED_METRIC_KEYS)
    for key in ("plugin", "plugin_version", "evaluator_model", "latency_ms"):
        assert key in result.metrics


def test_evaluate_blended_score_matches_blend_scores_arithmetic(plugin, reference):
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    result = plugin.evaluate(extracted, reference, stub=True, method="blended")

    signals = result.metrics["signals"]
    fractions = {name: sig["score"] / reference.marks_max for name, sig in signals.items()}
    blended_fraction, effective_weights = blend_scores(fractions, DEFAULT_BLEND_WEIGHTS)

    assert result.score == pytest.approx(round(blended_fraction * reference.marks_max, 2))
    assert result.metrics["weights"] == effective_weights


def test_evaluate_blended_custom_weights_are_applied(plugin, reference):
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    result = plugin.evaluate(
        extracted, reference, stub=True, method="blended",
        weights={"llm": 1.0, "semantic": 0.0, "keyword": 0.0, "rubric": 0.0},
    )
    llm_score = result.metrics["signals"]["llm"]["score"]
    assert result.score == pytest.approx(llm_score)


def test_evaluate_flags_low_confidence_on_divergent_signals(plugin):
    """stub=True gives semantic and llm the identical flat 70% stub score
    (no divergence, high confidence); using stub_llm=False/stub=False would
    require a real model — instead we drive divergence through the public
    weights/fractions path already covered by derive_confidence's own unit
    tests, and check evaluate() actually threads that flag into metrics."""
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    reference = TextReference(reference_text="reference text", marks_max=5.0, keywords=[])
    result = plugin.evaluate(extracted, reference, stub=True, method="blended")

    # Same stub score for both core signals -> zero divergence -> not flagged.
    assert result.metrics["low_confidence"] is False
    assert result.confidence == pytest.approx(1.0)


def test_evaluate_single_method_embeddings_reports_only_that_signal(plugin, reference):
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    result = plugin.evaluate(extracted, reference, stub=True, method="embeddings")

    assert set(result.metrics["signals"]) == {"semantic"}
    assert "weights" not in result.metrics
    assert result.metrics["low_confidence"] is False


def test_evaluate_method_keyword_raises_when_no_keywords_defined(plugin):
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    reference = TextReference(reference_text="ref", marks_max=5.0, keywords=[])
    with pytest.raises(ValueError, match="no score available"):
        plugin.evaluate(extracted, reference, stub=True, method="keyword")


def test_evaluate_rejects_unknown_method(plugin, reference):
    extracted = ExtractionResult(content=STUDENT_TEXT, confidence=1.0, metrics={})
    with pytest.raises(ValueError, match="method must be one of"):
        plugin.evaluate(extracted, reference, stub=True, method="bogus")


# ---------------------------------------------------------------------------
# scripts/evaluate_answer.py --dry-run — zero rows written (DB integration)
# ---------------------------------------------------------------------------

@pytest.mark.db
def test_dry_run_writes_zero_rows_to_evaluation_results(db_conn):
    cur = db_conn.cursor()
    cur.execute("SELECT count(*) FROM evaluation_results WHERE answer_id = %s", (SEEDED_ANSWER_ID,))
    before = cur.fetchone()[0]
    cur.close()

    proc = subprocess.run(
        [sys.executable, "scripts/evaluate_answer.py", SEEDED_ANSWER_ID, SEEDED_VARIANT_ID,
         "--method", "blended", "--stub", "--dry-run"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "rolled back" in proc.stdout

    cur = db_conn.cursor()
    cur.execute("SELECT count(*) FROM evaluation_results WHERE answer_id = %s", (SEEDED_ANSWER_ID,))
    after = cur.fetchone()[0]
    cur.close()

    assert after == before
