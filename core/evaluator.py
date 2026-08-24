"""
core/evaluator.py — semantic answer scoring (Task 3).

Two scoring methods, identical return shape:
    {"score": float, "explanation": str, "model": str}

1. score_with_embeddings — cosine similarity via sentence-transformers (CPU).
2. score_with_llm        — structured LLM prompt via core/llm.

Both scale the raw similarity/judgement to [0, marks_max].

The sentence-transformers import is deferred to first call (same pattern as
boto3 in core/storage.py) so scripts using --method llm don't need the
package installed.
"""

import json
import os
import re
import time

# Lazy-loaded on first embeddings call
_embedding_model = None
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"


def _get_embedding_model():
    """Load the sentence-transformer model once, on demand."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _embedding_model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    return _embedding_model


def score_with_embeddings(student_text: str, reference_text: str,
                          marks_max: float) -> dict:
    """Cosine similarity between student and reference embeddings,
    scaled to [0, marks_max]."""
    from sentence_transformers import util as st_util  # type: ignore

    start_t = time.perf_counter()
    model = _get_embedding_model()
    emb_student = model.encode(student_text, convert_to_tensor=True)
    emb_reference = model.encode(reference_text, convert_to_tensor=True)

    cosine_sim = float(st_util.cos_sim(emb_student, emb_reference)[0][0])
    # Clamp to [0, 1] — cosine similarity can go slightly negative for
    # completely unrelated texts; we treat that as zero.
    cosine_sim = max(0.0, min(1.0, cosine_sim))
    score = round(cosine_sim * marks_max, 2)
    end_t = time.perf_counter()

    return {
        "score": score,
        "explanation": (
            f"Cosine similarity: {cosine_sim:.4f} "
            f"({cosine_sim * 100:.1f}% match). "
            f"Score: {score}/{marks_max}."
        ),
        "model": _EMBEDDING_MODEL_NAME,
        "metrics": {"latency_ms": int((end_t - start_t) * 1000)},
    }


def score_with_llm(student_text: str, reference_text: str,
                    marks_max: float) -> dict:
    """Ask the LLM to score the student answer against the reference,
    returning a structured JSON verdict."""
    # Import here to keep core/evaluator.py usable without GROQ_API_KEY
    # when only embeddings are needed.
    from core.llm import _generate, DEFAULT_MODEL

    model_name = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)

    prompt = f"""You are an exam evaluator. Score a student's answer against a reference answer.

Reference answer (what a correct response should cover):
\"\"\"{reference_text}\"\"\"

Student's answer:
\"\"\"{student_text}\"\"\"

Maximum marks: {marks_max}

Instructions:
- Award marks proportionally based on how much of the reference content the student covered.
- Consider semantic equivalence — different wording expressing the same idea should receive full credit.
- Partial credit for partially correct or incomplete answers.
- Zero for completely irrelevant or empty answers.
- Be fair but strict: do not give marks for padding or repetition that adds no substance.

Output ONLY a JSON object with exactly these keys (no preamble, no code fences):
{{"score": <number between 0 and {marks_max}>, "explanation": "<brief justification>"}}"""

    start_t = time.perf_counter()
    raw, usage = _generate(prompt, return_metrics=True)
    end_t = time.perf_counter()
    
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}\nRaw: {raw!r}") from e

    if not isinstance(parsed, dict) or "score" not in parsed:
        raise ValueError(f"LLM response missing 'score' key: {parsed!r}")

    try:
        score = float(parsed["score"])
    except (TypeError, ValueError) as e:
        raise ValueError(f"Non-numeric score: {parsed['score']!r}") from e

    score = round(max(0.0, min(marks_max, score)), 2)
    explanation = str(parsed.get("explanation", "")).strip() or "No explanation provided."

    return {
        "score": score,
        "explanation": explanation,
        "model": model_name,
        "metrics": {
            "latency_ms": int((end_t - start_t) * 1000),
            "usage": usage,
        },
    }


def stub_score(student_text: str, reference_text: str,
               marks_max: float) -> dict:
    """Deterministic fake for --stub, matching the --stub-llm convention
    in other scripts. Returns 70% of marks_max with a fixed explanation."""
    score = round(marks_max * 0.7, 2)
    return {
        "score": score,
        "explanation": f"[STUB] Deterministic score: {score}/{marks_max} (70%).",
        "model": "stub",
        "metrics": {"stub": True, "latency_ms": 0},
    }
