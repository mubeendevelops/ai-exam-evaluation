"""
core/llm.py — thin, provider-agnostic wrapper for the generative-text jobs
this platform needs (question rewording now; likely paper-section drafting
and the Task-1 answer→question experiment later).

Choose a provider with the LLM_PROVIDER env var. Default is "ollama" —
fully free, runs locally, no signup, no API key, and keeps student/exam
data off any third-party server, which matters for this kind of platform.

  LLM_PROVIDER=ollama    (default) — free, local, no signup. Install
      Ollama (https://ollama.com), run `ollama pull llama3.1`, then
      `ollama serve`. No env vars required beyond LLM_PROVIDER itself.
      OLLAMA_HOST (default http://localhost:11434) / OLLAMA_MODEL
      (default llama3.1) to override.

  LLM_PROVIDER=groq      — free tier, cloud, no credit card required.
      Sign up at console.groq.com, create a key. As of mid-2026: ~14,400
      requests/day, 30 req/min, shared across all models — plenty for
      dev/testing, tight for real concurrent production traffic.
      Needs GROQ_API_KEY. GROQ_MODEL (default llama-3.3-70b-versatile)
      to override.

  LLM_PROVIDER=gemini    — free tier, cloud, no credit card required.
      Get a key at aistudio.google.com. As of mid-2026: ~1,500
      requests/day on Flash models, generous per-minute token budget.
      Note: free-tier prompts may be used by Google to improve their
      products — keep that in mind for real student answer text later.
      Needs GEMINI_API_KEY. GEMINI_MODEL (default gemini-2.5-flash)
      to override.

All three are called via plain HTTPS (stdlib urllib) — no extra
dependencies required for any of them.
"""
import json
import os
import urllib.error
import urllib.request


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    # Cloudflare (fronting Groq's and others' APIs) blocks urllib's default
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


def _call_ollama(prompt: str) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "llama3.1")
    result = _post_json(f"{host}/api/generate", {"model": model, "prompt": prompt, "stream": False})
    return result["response"].strip()


def _call_groq(prompt: str) -> str:
    api_key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    result = _post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 500},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return result["choices"][0]["message"]["content"].strip()


def _call_gemini(prompt: str) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    result = _post_json(url, {"contents": [{"parts": [{"text": prompt}]}]})
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


_PROVIDERS = {
    "ollama": _call_ollama,
    "groq": _call_groq,
    "gemini": _call_gemini,
}


def _generate(prompt: str) -> str:
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER {provider!r} — choose one of {sorted(_PROVIDERS)}")
    return _PROVIDERS[provider](prompt)


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
