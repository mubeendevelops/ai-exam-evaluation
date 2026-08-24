"""
core/llm.py — LLM wrapper for generative-text jobs (question rewording now;
likely paper-section drafting and answer evaluation later).

Uses Groq's API (free tier, no credit card required).
Sign up at console.groq.com, create a key. As of mid-2026: ~14,400
requests/day, 30 req/min — plenty for dev/testing.

Needs GROQ_API_KEY env var. GROQ_MODEL (default qwen/qwen3.6-27b)
to override.

Called via plain HTTPS (stdlib urllib) — no extra dependencies required.
"""
import json
import os
import re
import urllib.error
import urllib.request

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.6-27b"

# Mirrors the `question_style` enum in migrations/002_question_schema.sql —
# kept as a plain tuple here (not imported from the DB) since core/llm.py
# has no DB dependency by design; scripts/generate_questions.py is the
# layer that actually touches Postgres.
VALID_QUESTION_STYLES = ("long", "short", "one_word", "mcq")


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    # Cloudflare (fronting Groq's API) blocks urllib's default
    # "Python-urllib/x.y" User-Agent outright (HTTP 403, Cloudflare error
    # code 1010 — a bot-signature block, unrelated to the API key or
    # request body). A normal-looking User-Agent avoids it.
    req.add_header("User-Agent", "exam-platform-backend/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{url} returned HTTP {e.code}: {body}") from e


def _generate(prompt: str, return_metrics: bool = False):
    """Send a prompt to Groq and return the response text.
    If return_metrics is True, returns a tuple of (text, usage_dict)."""
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    result = _post_json(
        GROQ_API_URL,
        {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 4096},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    text = result["choices"][0]["message"]["content"]
    # Some Groq models (e.g. QwQ, DeepSeek-R1) return <think>...</think>
    # reasoning blocks before the actual answer. Strip them so only the
    # final intended output remains.
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Fallback: strip an unclosed <think> block if the response was still
    # truncated (anything from <think> to end of string).
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL).strip()
    
    if return_metrics:
        return cleaned, result.get("usage", {})
    return cleaned


def reword_question_text(question_text: str, instruction: str | None = None,
                          style: str | None = None) -> str:
    """Returns a reworded version of question_text. Raises on failure —
    callers should not silently fall back to the original text, since a
    silent no-op reword would be indistinguishable from a real one in the
    DB."""
    guidance = []
    if instruction:
        guidance.append(f"Specific instruction from the requester: {instruction}")
    if style:
        guidance.append(f"The reworded question should suit a '{style}' style answer "
                         f"(one of: long, short, one_word, mcq).")
    guidance_text = "\n".join(guidance) if guidance else (
        "No specific instruction given — just rephrase for clarity and freshness "
        "while keeping it a fair test of the same concept."
    )

    prompt = f"""You are rewording an exam question for a question bank. Keep the \
underlying concept, difficulty, and marks-worthiness the same — only change the \
phrasing, so it reads as a distinct question rather than a copy.

Original question:
\"\"\"{question_text}\"\"\"

{guidance_text}

Output ONLY the reworded question text. No preamble, no explanation, no quotes \
around it."""

    return _generate(prompt)


def generate_questions_from_content(paragraph_content: str, count: int = 3,
                                     style: str | None = None,
                                     intent_hint: str | None = None) -> list[dict]:
    """Generates `count` exam questions from a piece of source content
    (currently always a `paragraphs.content` value — Task 1 only operates
    at paragraph granularity for now).

    Returns a list of dicts: {"content": str, "style": str, "marks_max": float}.
    Raises ValueError on malformed/invalid LLM output — callers must not
    silently fall back to empty results, since that would look like "the
    paragraph had nothing question-worthy" rather than "the LLM call or
    parse failed" (same reasoning as reword_question_text above).

    This function is deliberately the ONLY place that knows how the source
    content is turned into a prompt. Task 1 compares three ways of doing
    that (plain content-only, pgvector-RAG-augmented, teacher intent-hinted)
    — but all three ultimately just change what goes into `prompt` below.
    Swapping in RAG or web-search context later means editing this function
    only; scripts/generate_questions.py and the DB layer stay untouched.
    This first cut implements the plain content-only path, with an already
    surfaced --intent-hint escape hatch corresponding to the intent-hinted
    approach so callers aren't blocked on the other two being designed.
    """
    if count < 1:
        raise ValueError("count must be >= 1")
    if style is not None and style not in VALID_QUESTION_STYLES:
        raise ValueError(f"style must be one of {VALID_QUESTION_STYLES}, got {style!r}")

    style_instruction = (
        f'Every question must suit a "{style}" style answer (one of: long, short, '
        f'one_word, mcq).' if style else
        "Choose whichever of long / short / one_word / mcq best fits each question "
        "individually — vary it if a mix makes sense for the content."
    )
    hint_instruction = (
        f"Teacher's guidance on focus/difficulty: {intent_hint}" if intent_hint else ""
    )

    prompt = f"""You are generating exam questions for a college question bank, from a \
single source paragraph. Only generate questions that can be answered using \
information actually present in the paragraph below — do not invent facts or \
require outside knowledge.

Paragraph:
\"\"\"{paragraph_content}\"\"\"

Generate exactly {count} distinct question(s) from this paragraph, each testing a \
different point rather than repeating the same fact.

{style_instruction}
{hint_instruction}

For each question, also suggest marks_max as a realistic mark value for a college \
exam (typical range: 1 for one_word/mcq, 2-5 for short, 5-10 for long).

Output ONLY a JSON array, no preamble, no markdown code fences, no explanation. \
Each element must have exactly these keys:
[{{"content": "<question text>", "style": "<long|short|one_word|mcq>", "marks_max": <number>}}]"""

    raw = _generate(prompt)

    # Tolerate the model wrapping the array in a code fence despite the
    # instruction not to — strip it rather than failing on something this
    # cosmetic.
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM did not return valid JSON: {e}\nRaw output: {raw!r}") from e

    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"Expected a non-empty JSON array, got: {parsed!r}")

    results = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict) or not {"content", "style", "marks_max"} <= item.keys():
            raise ValueError(
                f"Item {i} missing required keys (content/style/marks_max): {item!r}"
            )
        item_style = item["style"]
        if item_style not in VALID_QUESTION_STYLES:
            raise ValueError(
                f"Item {i} has invalid style {item_style!r}; must be one of {VALID_QUESTION_STYLES}"
            )
        try:
            marks_max = float(item["marks_max"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"Item {i} has non-numeric marks_max: {item['marks_max']!r}") from e
        if marks_max <= 0:
            raise ValueError(f"Item {i} has non-positive marks_max: {marks_max}")
        content = str(item["content"]).strip()
        if not content:
            raise ValueError(f"Item {i} has empty content")

        results.append({"content": content, "style": item_style, "marks_max": marks_max})

    return results