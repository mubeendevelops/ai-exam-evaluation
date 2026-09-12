"""api/deps/ratelimit.py — the login rate limiter, and the sliding-window
counter it shares with api/deps/quota.py. In-process, no Redis.

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
════════════════════════════════════════════════════════════════════════════

`POST /auth/login` is the one endpoint an unauthenticated caller may hit
without limit, and it verifies a bcrypt hash on every call. Two different
problems follow, and this module addresses both:

  * PASSWORD GUESSING. Nothing else in the request path slows an attacker
    down; bcrypt's cost factor makes each attempt expensive, but "expensive"
    is a constant an attacker pays in parallel.
  * SELF-INFLICTED DENIAL OF SERVICE. Each attempt costs ~100ms of CPU in a
    worker process. A few hundred concurrent logins with garbage passwords
    will starve every other endpoint on the same process, without any
    authentication succeeding at all.

════════════════════════════════════════════════════════════════════════════
WHY IN-PROCESS, AND WHAT THAT HONESTLY BUYS
════════════════════════════════════════════════════════════════════════════

Redis is named in the design doc and implemented nowhere in this repo; the
job queue argued its way out of it (migrations/README.md) and this is not the
place to introduce it as a dependency, an operational surface and a second
thing to secure, for one counter.

So the state lives in this process's memory, and the limit is therefore PER
PROCESS. With N uvicorn workers an attacker gets up to N times the configured
attempts, and a restart clears every counter. Both are stated here rather
than glossed, because a limiter that reads as global and is not would be
worse than none — someone would trust it. What it does buy, and buys
completely:

  * a single client cannot mount an unbounded guessing loop against one
    account through one worker;
  * the CPU-exhaustion path above is capped;
  * the counter costs one dict lookup and no network round trip, so the
    limiter cannot itself become the thing that fails.

A deployment that needs a genuinely global limit puts it in front of the app
(nginx `limit_req`, an API gateway, a WAF) — which is where a cross-process
rate limit belongs anyway, since it can refuse the request before it reaches
Python at all. This is defence in depth, not the whole defence.

════════════════════════════════════════════════════════════════════════════
WHAT IS COUNTED, AND WHY IT IS NOT AN ENUMERATION ORACLE
════════════════════════════════════════════════════════════════════════════

Two independent counters, both incremented on FAILED attempts only:

  * by CLIENT IP     — stops one host walking a password list;
  * by EMAIL ADDRESS — stops a botnet spread across many IPs walking a
                       password list against ONE account.

The email counter counts attempts against ADDRESSES, not against accounts: an
email that has no user row is counted exactly the same as one that does, and
crossing the threshold produces the same 429 either way. Otherwise the
limiter would answer the question the login endpoint is so careful not to
("does this address exist?") by responding differently to the two cases.

A SUCCESSFUL login clears that email's counter, so a user who fumbles a
password four times and then gets it right is not locked out of their next
session. It does not clear the IP counter — a shared NAT should not become a
laundering path for guesses against many accounts.
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Deque

from fastapi import Request

#: Cap on tracked keys, so an attacker cycling a million fake addresses fills
#: memory with counters instead of being limited by them. On overflow the
#: LEAST RECENTLY TOUCHED key is dropped — which is the right eviction here:
#: the keys being actively attacked are by definition the recently touched
#: ones, so an eviction storm cannot flush an in-progress attacker's counter
#: while sparing everyone else's.
MAX_TRACKED_KEYS = 20_000


class SlidingWindowLimiter:
    """A sliding-window counter, keyed by string.

    It counts whatever its caller records, and the CALLER decides what that
    means — the two uses deliberately differ: api/routers/auth.py records
    only FAILED logins (a login limiter that also throttled successful logins
    would be a bug), while api/deps/quota.py records every ACCEPTED call.

    Sliding rather than fixed-window: a fixed window lets an attacker fire the
    full allowance in the last second of one window and again in the first
    second of the next, i.e. twice the configured rate at the boundary. The
    cost is storing one timestamp per recorded call, and those are already
    capped by the limit itself.

    Thread-safe: uvicorn runs sync endpoints in a thread pool, so two requests
    genuinely execute concurrently and a bare dict update would drop
    increments — under exactly the concurrent load this exists to bound.
    """

    def __init__(self, *, limit: int, window_seconds: int):
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}; a limit of 0 locks everyone out")
        if window_seconds < 1:
            raise ValueError(f"window_seconds must be >= 1, got {window_seconds}")
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: collections.OrderedDict[str, Deque[float]] = collections.OrderedDict()
        self._lock = threading.Lock()

    # ── the two questions an endpoint asks ──────────────────────────────────

    def retry_after(self, keys: list[str], *, now: float | None = None) -> int | None:
        """Seconds until `keys` may try again, or None if they may try now.

        Read-only: asking does not record anything. The login endpoint calls
        this BEFORE verifying a password, so a limited caller never reaches
        the bcrypt work — which is half the point (see the DoS note above).
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            waits = [w for key in keys if (w := self._retry_after_one(key, now))]
        return max(waits) if waits else None

    def record(self, keys: list[str], *, now: float | None = None) -> None:
        """Counts one call against every key."""
        now = time.monotonic() if now is None else now
        with self._lock:
            for key in keys:
                bucket = self._bucket(key)
                bucket.append(now)
                self._prune(bucket, now)
            self._evict()

    def clear(self, keys: list[str]) -> None:
        """Forgets these keys — used for the EMAIL key on a successful login.

        See the module docstring on why the IP key is deliberately not
        cleared here.
        """
        with self._lock:
            for key in keys:
                self._hits.pop(key, None)

    # ── internals ───────────────────────────────────────────────────────────

    def _bucket(self, key: str) -> Deque[float]:
        bucket = self._hits.get(key)
        if bucket is None:
            bucket = collections.deque()
            self._hits[key] = bucket
        self._hits.move_to_end(key)
        return bucket

    def _prune(self, bucket: Deque[float], now: float) -> None:
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

    def _retry_after_one(self, key: str, now: float) -> int:
        bucket = self._hits.get(key)
        if bucket is None:
            return 0
        self._prune(bucket, now)
        if len(bucket) < self.limit:
            return 0
        # The window is full: the caller may retry when its OLDEST recorded
        # attempt falls out of the window. Rounded up, and never 0 — a
        # Retry-After of 0 on a refused request invites an immediate retry.
        return max(1, int(bucket[0] + self.window_seconds - now) + 1)

    def _evict(self) -> None:
        while len(self._hits) > MAX_TRACKED_KEYS:
            self._hits.popitem(last=False)


def client_ip(request: Request) -> str:
    """The address to rate-limit by.

    `request.client.host` — the peer this process is actually talking to —
    and deliberately NOT `X-Forwarded-For`. That header is client-supplied:
    behind no proxy, an attacker sets a fresh one per request and the IP
    counter becomes decorative. Trusting it requires knowing how many proxies
    sit in front of this app and stripping exactly that many hops, which is
    deployment configuration this repo does not have. Behind a real reverse
    proxy the right fix is uvicorn's `--proxy-headers` with
    `--forwarded-allow-ips`, which rewrites `request.client` from the header
    only when the immediate peer is a trusted proxy — i.e. the same value
    read here, made correct one layer down.
    """
    return request.client.host if request.client else "unknown"
