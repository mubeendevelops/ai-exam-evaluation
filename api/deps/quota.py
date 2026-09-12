"""api/deps/quota.py — per-college request quotas for the endpoints that
spend real Groq tokens, real CPU, or write real data on every call.

════════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
════════════════════════════════════════════════════════════════════════════

Nothing limited the API before this pass. core/llm.py's token bucket
(GROQ_MAX_RPM, §9) paces OUTBOUND Groq calls one process makes — it says
nothing about how many callers are asking this process to make them. A caller
that fires 1,000 POST /evaluate requests in a few seconds queues 1,000 jobs;
the token bucket only slows the WORKER down once it starts draining that
backlog, by which point the Groq daily quota (~14,400/day) is already
committed to somebody's flood.

Two independent limits, for two different failure shapes:

  1. RATE — api/deps/ratelimit.py::SlidingWindowLimiter, one sliding-window counter per named
     endpoint class, keyed by the caller's college (or, for a platform_admin,
     their user id — there is no college to key by). Applied to POST
     /evaluate, /questions/generate, /papers/generate and /upload via the
     `rate_limit(name)` dependency factory. This stops one caller's BURST.
  2. BACKLOG — `check_concurrent_job_cap()`. A burst limit does not stop many
     small, slow bursts spread over hours from queueing more work than a
     worker fleet can ever drain, and THAT is what actually threatens the
     Groq daily quota: it does not matter how slowly a college queued 500
     jobs, 500 queued jobs is 500 jobs a worker will eventually run, each
     spending real Groq calls. So POST /evaluate also refuses to queue a NEW
     job once the calling college already has
     `Settings.max_queued_jobs_per_college` jobs sitting `queued` or
     `running` in evaluation_jobs — read from the same table every job
     already lives in, via core/jobs.py::count_jobs. No new store.

════════════════════════════════════════════════════════════════════════════
WHY IN-PROCESS FOR (1), POSTGRES FOR (2) — NOT SLOWAPI, NOT REDIS
════════════════════════════════════════════════════════════════════════════

Redis is rejected for this codebase already (CLAUDE_CONTEXT.md §10,
migrations/README.md) — the job queue argued its way out of it once, and nothing
here reopens that argument for a request counter.

slowapi's default backend is an in-memory counter, the same shape this module
would otherwise hand-write — and this repo already has a hand-written,
TESTED one: api/deps/ratelimit.py::SlidingWindowLimiter, which the login
endpoint already uses (OrderedDict + deque, thread-safe, bounded key count,
evict-oldest). This module uses that SAME class — one mechanism, two uses:
the login endpoint records only FAILED attempts, `rate_limit()` below records
every ACCEPTED call. What gets counted is decided at the call site, not by
the class. Taking on a dependency (and its own key-function API, its own
storage-backend config surface) to get the same few dozen lines back is not
worth it.

(2) COULD NOT be an in-memory counter at all. The number that matters —
how many jobs does this college have outstanding RIGHT NOW — is mutated by a
worker process (or several) this API process shares no memory with.
evaluation_jobs already holds the true count; asking it is one indexed
`COUNT(*)`, not a new table or a new migration.

════════════════════════════════════════════════════════════════════════════
WHAT THIS HONESTLY BUYS
════════════════════════════════════════════════════════════════════════════

(1) is PER PROCESS, exactly like api/deps/ratelimit.py: with N uvicorn
workers a determined caller gets up to N times the configured rate, and a
restart clears every counter. (2) has no such gap — it reads the shared
database, so it is correct no matter how many API processes or workers are
running. Together they are defence in depth, not a global gateway-level rate
limiter; a deployment that needs one puts it in front of the app, same
argument api/deps/ratelimit.py already makes.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

import core.jobs
from api.deps.identity import CurrentUser, get_current_user
from api.deps.ratelimit import SlidingWindowLimiter


def _quota_key(user: CurrentUser) -> str:
    """The identity a quota is charged against — NEVER the client IP and
    NEVER a body field, per this pass's requirement: a caller cannot buy a
    higher limit by rotating IPs, and cannot spend another college's quota by
    naming it in a request.

    A platform_admin has no college_id (migration 017's biconditional CHECK),
    so it is charged against its own user id instead. That is correct rather
    than a gap: platform_admin has no reachable path to /evaluate or /upload
    at all (require_college_user, §11), and on /questions/generate and
    /papers/generate — which it CAN reach, the bank being shared — a
    platform_admin acts as one caller, not as a college.
    """
    if user.college_id is not None:
        return f"college:{user.college_id}"
    return f"user:{user.user_id}"


def rate_limit(name: str):
    """Dependency factory: 429 + Retry-After once `name`'s limiter is full
    for the caller's identity.

    `name` looks up the limiter app.state.rate_limiters[name] — built once in
    api/main.py::create_app from the matching Settings fields, so every app
    instance (including each test's own) gets its own counters and its own
    configured limit/window.

    Used as a route-level dependency (`dependencies=[Depends(rate_limit(...))]`)
    rather than a parameter, because nothing here needs its return value —
    the endpoint already resolves its own `user` separately.
    """

    def dependency(
        request: Request, user: CurrentUser = Depends(get_current_user),
    ) -> None:
        limiter: SlidingWindowLimiter = request.app.state.rate_limiters[name]
        key = _quota_key(user)

        # Read-only check first; the call is recorded only once it is
        # actually being let through.
        wait = limiter.retry_after([key])
        if wait is not None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded for this endpoint. Try again in "
                    f"{wait} seconds."
                ),
                headers={"Retry-After": str(wait)},
            )

        limiter.record([key])

    dependency.__name__ = f"rate_limit_{name}"
    return dependency


def check_concurrent_job_cap(cur, *, college_id, cap: int) -> None:
    """Refuses a new evaluation job once this college already has `cap` of
    its own sitting 'queued' or 'running'.

    This is the limit that actually protects the shared Groq daily quota —
    see the module docstring's BACKLOG argument. Called from inside
    POST /evaluate's own transaction, so the count it reads is exactly the
    state the request is about to add one more job to.

    `Retry-After` here is an honest hint, not a promise: unlike the RATE
    limiter above, there is no fixed window after which a slot is guaranteed
    free — a slot opens when SOME job the worker is already processing
    finishes, which could be sooner or later than any fixed number. 60
    seconds is offered as "check back roughly this often", matching the scale
    booklet evaluation actually runs at (minutes per job, §7D) rather than
    the sub-second cadence the RATE limiter's Retry-After implies.
    """
    outstanding = (
        core.jobs.count_jobs(cur, college_id=college_id, status="queued")
        + core.jobs.count_jobs(cur, college_id=college_id, status="running")
    )
    if outstanding >= cap:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"This college already has {outstanding} evaluation job(s) "
                f"queued or running, at the configured cap of {cap} "
                f"(MAX_QUEUED_JOBS_PER_COLLEGE). Wait for some of them to "
                f"finish before queueing more — GET /api/v1/jobs?status=queued "
                f"shows the backlog. This cap exists to protect the shared "
                f"Groq daily quota: a college's OWN outstanding jobs are the "
                f"thing that eventually spends it, regardless of how slowly "
                f"they were queued."
            ),
            headers={"Retry-After": "60"},
        )
