"""Rate limits on the public endpoints (P21, O12/O17, D148).

Sign-in, password reset, recovery codes, invitations, email verification, the Institution ID
check and registration need no account, so the per-account lockout (identity store) cannot
stop someone guessing across many accounts, enumerating Institution IDs or flooding inboxes.
Each such request counts against:

- the client's address (all accounts together), with room for a college's teachers behind one
  NAT address;
- for sign-in, recovery and forgot-password, also the (institution, email) pair, whether or not
  it exists, so the answer stays the same for real and made-up accounts.

Counters are fixed windows kept in this API process. With several instances each counts on its
own; in production Cloud Armor's rate-based rule (Terraform ``armor.tf``) sits in front and
holds across instances. Over a limit: 429 with ``Retry-After`` and one message for everyone.

The client address is the socket's peer, or, behind ``trusted_proxy_hops`` proxies (the load
balancer), the entry that many places from the end of ``X-Forwarded-For``: entries further left
are written by the client and prove nothing."""

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException, Request, status

from tarn_core.domain.tenancy import canonical_email, normalise_institution_id

TOO_MANY = "Too many attempts. Wait a few minutes and try again."


@dataclass(frozen=True, slots=True)
class Limit:
    name: str
    count: int
    window: timedelta


# Per client address.
LOGIN_IP = Limit("login-ip", 60, timedelta(minutes=5))
RECOVER_IP = Limit("recover-ip", 20, timedelta(minutes=15))
FORGOT_IP = Limit("forgot-ip", 20, timedelta(minutes=15))
TOKEN_IP = Limit("token-ip", 30, timedelta(minutes=15))  # reset, invitation, email verification
AVAILABILITY_IP = Limit("availability-ip", 30, timedelta(minutes=5))
REGISTER_IP = Limit("register-ip", 5, timedelta(hours=1))
# Per (institution, email). Sign-in locks the account after 5 wrong passwords anyway; this also
# covers the unknown and locked cases, which the lockout does not count.
LOGIN_ACCOUNT = Limit("login-account", 10, timedelta(minutes=15))
RECOVER_ACCOUNT = Limit("recover-account", 5, timedelta(minutes=15))
FORGOT_ACCOUNT = Limit("forgot-account", 3, timedelta(minutes=15))


class RateLimiter:
    """Fixed-window counters in memory, thread-safe (sync routes run in a thread pool)."""

    def __init__(self, now: Callable[[], datetime], *, enabled: bool = True) -> None:
        self._now = now
        self._enabled = enabled
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[datetime, int]] = {}
        self._checks = 0

    def hit(self, limit: Limit, key: str) -> float | None:
        """Counts one request; returns the seconds to wait when over the limit, else None."""
        if not self._enabled:
            return None
        now = self._now()
        with self._lock:
            self._checks += 1
            if self._checks % 1000 == 0:
                self._prune(now)
            slot = (limit.name, key)
            started, count = self._windows.get(slot, (now, 0))
            if now - started >= limit.window:
                started, count = now, 0
            count += 1
            self._windows[slot] = (started, count)
            if count > limit.count:
                return max(1.0, (started + limit.window - now).total_seconds())
            return None

    def _prune(self, now: datetime) -> None:
        longest = timedelta(hours=1)
        for slot, (started, _) in list(self._windows.items()):
            if now - started >= longest:
                del self._windows[slot]


def client_address(request: Request, trusted_proxy_hops: int) -> str:
    peer = request.client.host if request.client else "unknown"
    if trusted_proxy_hops <= 0:
        return peer
    forwarded = [
        part.strip()
        for header in request.headers.getlist("x-forwarded-for")
        for part in header.split(",")
        if part.strip()
    ]
    if len(forwarded) < trusted_proxy_hops:
        return peer
    return forwarded[-trusted_proxy_hops]


def limiter_of(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def _refuse(wait: float) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail=TOO_MANY,
        headers={"Retry-After": str(math.ceil(wait))},
    )


def check(request: Request, *hits: tuple[Limit, str]) -> None:
    """Counts every hit (so one request cannot dodge a later counter), then refuses if any of
    them is over its limit."""
    limiter = limiter_of(request)
    waits = [w for limit, key in hits if (w := limiter.hit(limit, key)) is not None]
    if waits:
        raise _refuse(max(waits))


def ip_of(request: Request) -> str:
    return client_address(request, request.app.state.trusted_proxy_hops)


def account_key(institution_id: str, email: str) -> str:
    return f"{normalise_institution_id(institution_id)}|{canonical_email(email)}"


def per_ip(limit: Limit) -> Callable[[Request], None]:
    """A route dependency counting ``limit`` against the client address."""

    def dependency(request: Request) -> None:
        check(request, (limit, ip_of(request)))

    return dependency


__all__ = [
    "AVAILABILITY_IP",
    "FORGOT_ACCOUNT",
    "FORGOT_IP",
    "LOGIN_ACCOUNT",
    "LOGIN_IP",
    "RECOVER_ACCOUNT",
    "RECOVER_IP",
    "REGISTER_IP",
    "TOKEN_IP",
    "Limit",
    "RateLimiter",
    "account_key",
    "check",
    "client_address",
    "ip_of",
    "per_ip",
]
