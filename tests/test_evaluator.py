"""Characterization tests for core/evaluator.py — freeze CURRENT behavior."""
from __future__ import annotations

import pytest

from core import evaluator
from core import llm as core_llm


# ---------------------------------------------------------------------------
# stub_score
# ---------------------------------------------------------------------------

def test_stub_score_exact_shape():
    result = evaluator.stub_score("anything", "reference", 10)
    assert result["score"] == round(10 * 0.7, 2)
    assert result["model"] == "stub"
    assert result["metrics"] == {"stub": True, "latency_ms": 0}
    assert result["explanation"].startswith("[STUB]")


def test_stub_score_scales_with_marks_max():
    result = evaluator.stub_score("x", "y", 7.5)
    assert result["score"] == round(7.5 * 0.7, 2)


# ---------------------------------------------------------------------------
# score_with_llm — happy path
# ---------------------------------------------------------------------------

def test_score_with_llm_happy_path(stub_llm, monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    stub_llm.text = '{"score": 4, "explanation": "Covers the key point."}'
    stub_llm.usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    result = evaluator.score_with_llm("student answer", "reference answer", 5)

    assert result["score"] == 4.0
    assert result["explanation"] == "Covers the key point."
    assert result["model"] == core_llm.DEFAULT_MODEL
    assert result["metrics"]["usage"] == stub_llm.usage
    assert isinstance(result["metrics"]["latency_ms"], int)
    assert result["metrics"]["latency_ms"] >= 0


def test_score_with_llm_strips_json_code_fence(stub_llm):
    stub_llm.text = '```json\n{"score": 3, "explanation": "ok"}\n```'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["score"] == 3.0
    assert result["explanation"] == "ok"


def test_score_with_llm_strips_bare_code_fence(stub_llm):
    stub_llm.text = '```\n{"score": 2, "explanation": "ok"}\n```'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["score"] == 2.0


def test_score_with_llm_clamps_above_marks_max(stub_llm):
    stub_llm.text = '{"score": 999, "explanation": "too high"}'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["score"] == 5.0


def test_score_with_llm_clamps_below_zero(stub_llm):
    stub_llm.text = '{"score": -50, "explanation": "negative"}'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["score"] == 0.0


def test_score_with_llm_rounds_to_2dp(stub_llm):
    stub_llm.text = '{"score": 3.14159, "explanation": "pi-ish"}'
    result = evaluator.score_with_llm("s", "r", 10)
    assert result["score"] == 3.14


def test_score_with_llm_explanation_falls_back_when_absent(stub_llm):
    stub_llm.text = '{"score": 1}'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["explanation"] == "No explanation provided."


def test_score_with_llm_explanation_falls_back_when_empty(stub_llm):
    stub_llm.text = '{"score": 1, "explanation": "   "}'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["explanation"] == "No explanation provided."


def test_score_with_llm_model_honors_groq_model_env(stub_llm, monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "some/custom-model")
    stub_llm.text = '{"score": 1, "explanation": "ok"}'
    result = evaluator.score_with_llm("s", "r", 5)
    assert result["model"] == "some/custom-model"


def test_score_with_llm_invalid_json_raises_value_error(stub_llm):
    stub_llm.text = "not json at all"
    with pytest.raises(ValueError, match="LLM did not return valid JSON"):
        evaluator.score_with_llm("s", "r", 5)


def test_score_with_llm_missing_score_key_raises_value_error(stub_llm):
    stub_llm.text = '{"explanation": "no score field"}'
    with pytest.raises(ValueError, match="missing 'score' key"):
        evaluator.score_with_llm("s", "r", 5)


def test_score_with_llm_non_numeric_score_raises_value_error(stub_llm):
    stub_llm.text = '{"score": "not-a-number", "explanation": "bad"}'
    with pytest.raises(ValueError, match="Non-numeric score"):
        evaluator.score_with_llm("s", "r", 5)


# ---------------------------------------------------------------------------
# score_with_embeddings — real sentence-transformers model
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_score_with_embeddings_identical_strings_top_of_range():
    result = evaluator.score_with_embeddings(
        "The mitochondria is the powerhouse of the cell.",
        "The mitochondria is the powerhouse of the cell.",
        10,
    )
    assert set(result.keys()) == {"score", "explanation", "model", "metrics"}
    assert result["model"] == "all-MiniLM-L6-v2"
    # Observed: identical strings encode to cosine similarity 1.0 exactly
    # (self-similarity), i.e. the very top of the range.
    assert result["score"] == pytest.approx(10.0, abs=1e-6)


@pytest.mark.slow
def test_score_with_embeddings_unrelated_strings_score_lower():
    identical = evaluator.score_with_embeddings(
        "Photosynthesis converts light energy into chemical energy in plants.",
        "Photosynthesis converts light energy into chemical energy in plants.",
        10,
    )
    unrelated = evaluator.score_with_embeddings(
        "Photosynthesis converts light energy into chemical energy in plants.",
        "The stock market closed lower today amid inflation concerns.",
        10,
    )
    assert identical["score"] == pytest.approx(10.0, abs=1e-6)
    # Observed: this particular unrelated pair clamps all the way to 0.0
    # (raw cosine similarity was negative and got clamped by the [0,1] floor).
    assert unrelated["score"] == pytest.approx(0.0, abs=1e-6)
    assert unrelated["score"] < identical["score"]


@pytest.mark.slow
def test_score_with_embeddings_clamp_holds():
    # Observed score for this pair: 1.23/5 (cosine similarity ~0.2455).
    result = evaluator.score_with_embeddings("gibberish qzx", "totally different wvyk", 5)
    assert result["score"] == pytest.approx(1.23, abs=0.01)
    assert 0.0 <= result["score"] <= 5.0
