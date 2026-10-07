"""The Groq chat client (OpenAI-compatible endpoint): timeout, retries with backoff, token
accounting, and a rate-limited rotation over several keys.

DEVELOPMENT KEYS ONLY. Rotating several free-tier accounts to stay under the rate limits is a
development convenience: it may breach the provider's terms, so ``Settings`` refuses more than
one key in production, where one key of a paid plan is the way (CLAUDE.md, design.md "LLM phase").

Never logged or stored here: keys (only a key's position in the list), prompts, replies."""

import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import structlog

from tarn_core.domain.scoring import LlmUsage
from tarn_core.errors import ScorerUnavailableError

log = structlog.get_logger("tarn_adapters.llm")

_RETRY_AFTER_MAX = 60.0
_BACKOFF_MAX = 8.0


# --- the wire ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: int
    body: bytes
    headers: Mapping[str, str]


class TransportError(Exception):
    """No HTTP answer: timeout, refused connection, TLS failure. Carries no URL or key."""


class Transport(Protocol):
    """What the client needs from HTTP; tests play recorded responses through it."""

    def post(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout: float
    ) -> HttpResult: ...


class UrllibTransport:
    """Standard library HTTPS: no extra dependency in the production image."""

    def post(self, url: str, headers: Mapping[str, str], body: bytes, timeout: float) -> HttpResult:
        if urlparse(url).scheme != "https":
            raise TransportError("the LLM endpoint must be https")
        # https only, checked above (S310 worries about file: and custom schemes).
        request = urllib.request.Request(  # noqa: S310
            url, data=body, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return HttpResult(response.status, response.read(), dict(response.headers))
        except urllib.error.HTTPError as error:
            return HttpResult(error.code, error.read(), dict(error.headers))
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TransportError("no answer from the LLM endpoint") from None


# --- keys ---------------------------------------------------------------------------------------


class KeyPool:
    """Round-robin over the keys, at most ``per_minute`` requests per key in any 60 seconds. A
    key that was refused (429) rests until its ``Retry-After``; one that was rejected (401/403)
    is dropped. When every key is busy, ``acquire`` waits for the first free slot, up to
    ``max_wait`` seconds, then gives up."""

    def __init__(
        self,
        keys: Sequence[str],
        *,
        per_minute: int,
        max_wait: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not keys:
            raise ValueError("no LLM key configured")
        self._keys = tuple(keys)
        self._per_minute = per_minute
        self._max_wait = max_wait
        self._clock = clock
        self._sleep = sleep
        self._used: list[deque[float]] = [deque() for _ in keys]
        self._rests_until = [0.0] * len(keys)
        self._dropped: set[int] = set()
        self._next = 0
        self._lock = threading.Lock()

    def __repr__(self) -> str:  # keys never appear in a log line or a traceback
        return f"KeyPool({len(self._keys)} keys, {len(self._dropped)} dropped)"

    def acquire(self) -> tuple[int, str]:
        """A key with a free slot: its position and the key. Raises ``ScorerUnavailableError``
        when none frees up in time or every key was dropped."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                live = [i for i in range(len(self._keys)) if i not in self._dropped]
                if not live:
                    raise ScorerUnavailableError("every LLM key was rejected")
                for offset in range(len(self._keys)):
                    i = (self._next + offset) % len(self._keys)
                    if i in self._dropped or self._rests_until[i] > now:
                        continue
                    window = self._used[i]
                    while window and now - window[0] >= 60.0:
                        window.popleft()
                    if len(window) < self._per_minute:
                        window.append(now)
                        self._next = (i + 1) % len(self._keys)
                        return i, self._keys[i]
                free_at = min(
                    max(self._rests_until[i], self._used[i][0] + 60.0 if self._used[i] else 0.0)
                    for i in live
                )
                wait = max(free_at - now, 0.05)
            if waited + wait > self._max_wait:
                raise ScorerUnavailableError("the LLM rate limit leaves no free slot in time")
            self._sleep(wait)
            waited += wait

    def rest(self, index: int, seconds: float) -> None:
        with self._lock:
            self._rests_until[index] = self._clock() + min(max(seconds, 1.0), _RETRY_AFTER_MAX)

    def drop(self, index: int) -> None:
        with self._lock:
            self._dropped.add(index)


# --- the client ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChatReply:
    text: str
    usage: LlmUsage
    """Requests made for this reply (retries included) and the tokens the provider counted."""


class GroqClient:
    def __init__(
        self,
        pool: KeyPool,
        *,
        model: str,
        url: str,
        timeout: float,
        max_attempts: int,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        backoff: float = 0.5,
        max_tokens: int = 200,
    ) -> None:
        self._pool = pool
        self._model = model
        self._url = url
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._transport = transport or UrllibTransport()
        self._sleep = sleep
        self._backoff = backoff
        self._max_tokens = max_tokens
        self._total = LlmUsage()
        self._lock = threading.Lock()

    @property
    def total(self) -> LlmUsage:
        """Everything this client has asked and been charged since it started."""
        with self._lock:
            return self._total

    def chat(self, messages: Sequence[Mapping[str, str]]) -> ChatReply:
        """One completion as JSON text. Retries timeouts, 5xx and rate limits (next key) up to
        ``max_attempts`` requests; fails at once on a refusal that retrying cannot fix."""
        body = json.dumps(
            {
                "model": self._model,
                "messages": list(messages),
                "temperature": 0,
                "max_tokens": self._max_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode()
        calls = 0
        while calls < self._max_attempts:
            index, key = self._pool.acquire()
            calls += 1
            started = time.perf_counter()
            try:
                result = self._transport.post(
                    self._url,
                    {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    body,
                    self._timeout,
                )
            except TransportError:
                self._note(calls, index, "no_answer", started)
                self._pause(calls)
                continue
            status = result.status
            if status == 200:
                reply = self._read(result.body, calls)
                self._note(calls, index, "ok", started, reply.usage)
                return reply
            self._note(calls, index, f"http_{status}", started)
            if status == 429:
                self._pool.rest(index, _retry_after(result.headers))
            elif status in (401, 403):
                self._pool.drop(index)
            elif status == 408 or status >= 500:
                self._pause(calls)
            else:
                self._count(LlmUsage(calls=calls))
                raise ScorerUnavailableError(f"the LLM provider refused the request ({status})")
        self._count(LlmUsage(calls=calls))
        raise ScorerUnavailableError("the LLM provider gave no answer after its retries")

    def _read(self, raw: bytes, calls: int) -> ChatReply:
        try:
            data = json.loads(raw)
            text = data["choices"][0]["message"]["content"]
            used = data.get("usage") or {}
            usage = LlmUsage(
                calls=calls,
                input_tokens=int(used.get("prompt_tokens", 0)),
                output_tokens=int(used.get("completion_tokens", 0)),
            )
        except (ValueError, KeyError, IndexError, TypeError):
            self._count(LlmUsage(calls=calls))
            raise ScorerUnavailableError("the LLM provider's answer has an unknown shape") from None
        if not isinstance(text, str):
            self._count(LlmUsage(calls=calls))
            raise ScorerUnavailableError("the LLM provider's answer has no text")
        self._count(usage)
        return ChatReply(text=text, usage=usage)

    def _count(self, usage: LlmUsage) -> None:
        with self._lock:
            self._total += usage

    def _pause(self, attempt: int) -> None:
        if attempt < self._max_attempts:
            self._sleep(min(self._backoff * 2 ** (attempt - 1), _BACKOFF_MAX))

    def _note(
        self,
        attempt: int,
        key_index: int,
        outcome: str,
        started: float,
        usage: LlmUsage | None = None,
    ) -> None:
        log.info(
            "llm.request",
            model=self._model,
            attempt=attempt,
            key=key_index,
            outcome=outcome,
            seconds=round(time.perf_counter() - started, 2),
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
        )


def _retry_after(headers: Mapping[str, str]) -> float:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                return float(value)
            except ValueError:
                break
    return _RETRY_AFTER_MAX
