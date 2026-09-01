"""
core/llm.py — LLM wrapper for generative-text jobs (question rewording now;
likely paper-section drafting and answer evaluation later).

Uses Groq's API (free tier, no credit card required).
Sign up at console.groq.com, create a key. As of mid-2026: ~14,400
requests/day, 30 req/min — plenty for dev/testing.

Needs GROQ_API_KEY env var. GROQ_MODEL (default qwen/qwen3.6-27b)
to override.

Called via plain HTTPS (stdlib urllib) — no extra dependencies required.

RATE LIMITING (added with the booklet orchestrator, core/booklet_evaluator.py).
The free tier's ~30 requests/minute is not a theoretical ceiling: one scanned
booklet with 20 LLM-scored answers hits it inside a minute, and before this
existed the failure mode was an HTTP 429 surfacing as a RuntimeError that
aborted whichever script was running. Two independent mechanisms, both here
rather than in the orchestrator, because EVERY call site benefits — a
generate_questions.py loop over a long chapter can exhaust the same quota:

  1. A CLIENT-SIDE TOKEN BUCKET (throttle()/TokenBucket) that paces requests
     BEFORE they are sent. Sized from GROQ_MAX_RPM (default 25, deliberately
     under the documented 30 so a concurrent caller elsewhere on the same key
     has headroom), with a burst allowance so a handful of calls still go out
     back-to-back. Thread-safe, because core/booklet_evaluator.py evaluates
     with a bounded thread pool and every worker shares this one bucket.

  2. EXPONENTIAL BACKOFF WITH JITTER on the responses that mean "you are
     going too fast anyway" — HTTP 429, HTTP 5xx and transport errors. The
     bucket is a guess about the server's accounting; the retry is what
     handles the guess being wrong (another process on the same key, a
     tightened server-side limit, a token-per-minute rather than
     request-per-minute cap). Retry-After is honoured when Groq sends it,
     since it is better information than any local backoff curve. Jitter is
     "equal jitter" (half the delay fixed, half random) so N throttled
     workers do not retry in lockstep and re-collide.

Plus a per-process DAILY counter (GROQ_MAX_RPD, default 14000 under the
documented 14,400) that raises RateLimitError instead of burning the day's
remaining quota on calls that will fail. It is PER PROCESS and resets at UTC
midnight: there is no Redis or shared store in this repo (CLAUDE_CONTEXT.md
§10), so it cannot see a second script running beside this one. It is a
guard-rail against a runaway loop, not an accounting system — which is
exactly why the 429 backoff above exists as well.

Everything is configurable by env var and can be disabled entirely with
GROQ_MAX_RPM=0, which is what the test suite does; --stub-llm paths never
reach this module at all.
"""
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.6-27b"

# --- rate limiting tunables ---------------------------------------------
#
# All env-overridable so a paid tier (or a deliberately slower batch job)
# needs no code change. GROQ_MAX_RPM=0 disables client-side throttling
# entirely — the retry/backoff path stays active, since that one protects
# against the server's limits rather than guessing at them.
DEFAULT_MAX_RPM = 25          # documented free-tier limit is 30; leave headroom
DEFAULT_MAX_RPD = 14000       # documented free-tier limit is 14,400
DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_BACKOFF = 1.0    # seconds, doubled per attempt
DEFAULT_MAX_BACKOFF = 60.0    # cap: a 6th doubling would outlive most jobs

#: HTTP statuses worth retrying. 429 is the rate limit itself; 5xx are Groq
#: or its Cloudflare front-end being briefly unavailable. Everything else
#: (401 bad key, 400 malformed request, 404 unknown model) is a bug that
#: retrying only slows down the discovery of.
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class RateLimitError(RuntimeError):
    """Rate limiting gave up: retries exhausted, or the local daily budget is
    spent. Its own type so a batch runner can tell "slow down / stop for
    today" apart from a malformed prompt or a bad API key, both of which
    arrive as plain RuntimeError from _post_json and are not worth retrying.
    """


class TokenBucket:
    """Classic token bucket, thread-safe, blocking.

    `rate_per_minute` tokens refill smoothly (not in once-a-minute steps —
    a step refill lets a full burst land at each boundary, which is exactly
    the shape a sliding-window server-side limiter rejects). `capacity` is
    the burst allowance: how many requests may go out back-to-back after an
    idle period. It defaults to a fifth of the minute rate rather than the
    whole minute, so a fresh process cannot open with a 25-request burst
    against a limit it can only observe one rejection at a time.

    The bucket starts FULL, so a script's first few calls go out without a
    cold-start wait: a burst of five is well inside any 30/min window, and
    making every one-shot script (reword_question.py, a single
    evaluate_answer.py run) pay an artificial delay would be a real cost for
    no real protection.
    """

    def __init__(self, rate_per_minute: float, capacity: float | None = None):
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be > 0 (use throttle=None to disable)")
        self.rate = rate_per_minute / 60.0
        self.capacity = float(capacity if capacity is not None
                              else max(1.0, rate_per_minute / 5.0))
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = threading.Lock()
        #: Cumulative seconds callers spent blocked here. Reported in
        #: evaluation metrics so a slow booklet can be attributed to
        #: throttling rather than to a slow model.
        self.total_wait_seconds = 0.0
        self.acquired = 0

    def acquire(self, timeout: float | None = None) -> float:
        """Block until one token is available; return the seconds waited.

        Raises RateLimitError if `timeout` would be exceeded — a caller that
        cannot wait needs to hear that as an error rather than silently
        being served late. The lock is never held across a sleep, so N
        worker threads queue rather than serialize on the mutex.
        """
        started = time.monotonic()
        deadline = None if timeout is None else started + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity,
                                   self._tokens + (now - self._updated) * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    waited = now - started
                    self.total_wait_seconds += waited
                    self.acquired += 1
                    return waited
                deficit_seconds = (1.0 - self._tokens) / self.rate

            if deadline is not None and time.monotonic() + deficit_seconds > deadline:
                raise RateLimitError(
                    f"waited {time.monotonic() - started:.1f}s for a rate-limit token and "
                    f"would need {deficit_seconds:.1f}s more, exceeding the {timeout}s "
                    f"budget. Lower the concurrency, or raise GROQ_MAX_RPM if the "
                    f"account's real limit is higher than {self.rate * 60:.0f}/min."
                )
            # Cap the nap so a raised rate or a released token is noticed
            # promptly rather than after a full deficit's sleep.
            time.sleep(min(deficit_seconds, 0.25))

    def stats(self) -> dict:
        with self._lock:
            return {
                "rate_per_minute": round(self.rate * 60.0, 2),
                "burst_capacity": self.capacity,
                "acquired": self.acquired,
                "total_wait_seconds": round(self.total_wait_seconds, 2),
            }


class DailyQuota:
    """Per-process request counter that resets at UTC midnight.

    Deliberately simple and deliberately local — see the module docstring for
    why this is a guard-rail against a runaway loop rather than real quota
    accounting.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self._day = datetime.now(timezone.utc).date()
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> None:
        with self._lock:
            today = datetime.now(timezone.utc).date()
            if today != self._day:
                self._day, self._used = today, 0
            if self.limit and self._used >= self.limit:
                raise RateLimitError(
                    f"local daily budget of {self.limit} Groq requests is spent "
                    f"({self._day} UTC). This counter is per-process (no shared store "
                    f"in this repo — CLAUDE_CONTEXT.md §10), so it is a runaway-loop "
                    f"guard, not the real quota. Raise GROQ_MAX_RPD or use --stub-llm."
                )
            self._used += 1

    def stats(self) -> dict:
        with self._lock:
            return {"limit": self.limit, "used": self._used, "day": str(self._day)}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric, got {raw!r}") from exc


_LIMITER_LOCK = threading.Lock()
_BUCKET: TokenBucket | None = None
_QUOTA: DailyQuota | None = None
_LIMITER_SIGNATURE: tuple | None = None


def _limiters() -> tuple[TokenBucket | None, DailyQuota | None]:
    """The process-wide bucket and daily counter, built on first use.

    Lazy (not module-level constants) so importing core/llm.py stays free and
    so a test — or a script that sets GROQ_MAX_RPM itself before its first
    call — gets the configuration it asked for. Rebuilt if the env vars
    changed, which is what makes that possible without a reload.
    """
    global _BUCKET, _QUOTA, _LIMITER_SIGNATURE
    signature = (_env_float("GROQ_MAX_RPM", DEFAULT_MAX_RPM),
                 _env_float("GROQ_MAX_RPD", DEFAULT_MAX_RPD),
                 _env_float("GROQ_BURST", 0.0))
    with _LIMITER_LOCK:
        if signature != _LIMITER_SIGNATURE:
            rpm, rpd, burst = signature
            _BUCKET = TokenBucket(rpm, capacity=burst or None) if rpm > 0 else None
            _QUOTA = DailyQuota(int(rpd)) if rpd > 0 else None
            _LIMITER_SIGNATURE = signature
        return _BUCKET, _QUOTA


def rate_limit_stats() -> dict:
    """Snapshot of what throttling has done so far in this process — how many
    requests were paced, how long callers spent waiting, and how much of the
    local daily budget is gone. Stored in the booklet report so a slow run is
    attributable (see core/booklet_evaluator.py)."""
    bucket, quota = _limiters()
    return {
        "throttle": bucket.stats() if bucket else {"disabled": True},
        "daily": quota.stats() if quota else {"disabled": True},
        "retries": dict(_RETRY_COUNTS),
    }


#: reason -> count, for rate_limit_stats(). Not locked: these are diagnostic
#: counters where a lost increment under contention costs nothing, and taking
#: a lock on every retry to protect a display number would be the wrong
#: trade.
_RETRY_COUNTS: dict[str, int] = {"429": 0, "5xx": 0, "transport": 0}


def _retry_after_seconds(headers) -> float | None:
    """Groq's Retry-After, when present. Seconds-only form; the HTTP-date
    form is legal but Groq does not send it, and guessing at a clock skew
    would be worse than falling back to the local backoff curve."""
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(0.0, float(str(raw).strip()))
    except ValueError:
        return None


def _backoff_delay(attempt: int, retry_after: float | None) -> float:
    """Equal jitter: half the computed delay fixed, half random.

    Full jitter (uniform over [0, delay]) can schedule a retry almost
    immediately, which against a per-minute limiter just spends another
    attempt; no jitter at all makes N throttled workers retry in lockstep and
    collide again. Equal jitter keeps a guaranteed floor and still spreads
    the pack. A server-supplied Retry-After overrides the curve — it is real
    information — but still gets a small random tail for the same reason.
    """
    base = _env_float("GROQ_BASE_BACKOFF", DEFAULT_BASE_BACKOFF)
    ceiling = _env_float("GROQ_MAX_BACKOFF", DEFAULT_MAX_BACKOFF)
    if retry_after is not None:
        return min(ceiling, retry_after) + random.uniform(0.0, 0.5)
    delay = min(ceiling, base * (2 ** attempt))
    return delay / 2.0 + random.uniform(0.0, delay / 2.0)


def throttle(timeout: float | None = None) -> float:
    """Pace one outbound request: consume a daily-budget slot and block on
    the token bucket. Returns the seconds waited.

    Public because a caller that batches several prompts into one HTTP
    request (or that talks to Groq without going through _post_json) still
    has to be counted, and because the booklet orchestrator's dry runs want
    to exercise the pacing path without sending anything.
    """
    bucket, quota = _limiters()
    if quota is not None:
        quota.consume()
    return bucket.acquire(timeout=timeout) if bucket is not None else 0.0

# Mirrors the `question_style` enum in migrations/002_question_schema.sql —
# kept as a plain tuple here (not imported from the DB) since core/llm.py
# has no DB dependency by design; scripts/generate_questions.py is the
# layer that actually touches Postgres.
VALID_QUESTION_STYLES = ("long", "short", "one_word", "mcq")


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 60) -> dict:
    """POST `payload` as JSON and return the parsed response.

    Every request is paced by throttle() first, then retried with
    exponential backoff and jitter on 429/5xx/transport failures — see the
    module docstring for why both halves exist. A non-retryable HTTP error
    (401, 400, 404 ...) is raised immediately as RuntimeError with the
    response body, exactly as before this function grew a retry loop:
    retrying a bad API key only delays finding out about it.
    """
    data = json.dumps(payload).encode("utf-8")
    max_retries = int(_env_float("GROQ_MAX_RETRIES", DEFAULT_MAX_RETRIES))
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        throttle()

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
            if e.code not in RETRYABLE_STATUSES:
                raise RuntimeError(f"{url} returned HTTP {e.code}: {body}") from e
            _RETRY_COUNTS["429" if e.code == 429 else "5xx"] += 1
            last_error = RuntimeError(f"{url} returned HTTP {e.code}: {body}")
            retry_after = _retry_after_seconds(e.headers)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # A dropped connection or a read timeout is indistinguishable
            # from an overloaded endpoint from out here, and is just as
            # worth one more attempt.
            _RETRY_COUNTS["transport"] += 1
            last_error = RuntimeError(f"{url} transport error: {type(e).__name__}: {e}")
            retry_after = None

        if attempt == max_retries:
            break
        time.sleep(_backoff_delay(attempt, retry_after))

    raise RateLimitError(
        f"giving up on {url} after {max_retries + 1} attempts. Last error: {last_error}. "
        f"Rate-limit state: {rate_limit_stats()}. If this is a sustained 429, lower "
        f"GROQ_MAX_RPM (currently {_env_float('GROQ_MAX_RPM', DEFAULT_MAX_RPM):.0f}/min) "
        f"or the caller's concurrency."
    ) from last_error


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