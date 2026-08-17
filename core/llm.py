"""
core/llm.py — LLM wrapper for generative-text jobs (question rewording now;
likely paper-section drafting and answer evaluation later).

Uses Groq's API (free tier, no credit card required).
Sign up at console.groq.com, create a key. As of mid-2026: ~14,400
requests/day, 30 req/min — plenty for dev/testing.

Needs GROQ_API_KEY env var. GROQ_MODEL (default llama-3.3-70b-versatile)
to override.

Called via plain HTTPS (stdlib urllib) — no extra dependencies required.
"""
import json
import os
import urllib.error
import urllib.request

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.3-70b-versatile"


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


def _generate(prompt: str) -> str:
    """Send a prompt to Groq and return the response text."""
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", DEFAULT_MODEL)
    result = _post_json(
        GROQ_API_URL,
        {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 500},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return result["choices"][0]["message"]["content"].strip()


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
