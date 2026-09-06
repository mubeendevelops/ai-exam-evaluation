"""core/pagination.py — shared constants and helpers for every paginated
read in core/, plus the cap-and-flag helper for nested collections.

Two different things share the name "pagination" in this codebase:

  1. LIST ENDPOINTS (GET /jobs, /results, /uploads, /exams, /students,
     /questions, /papers) — the caller controls limit/offset via
     api/deps/pagination.py's shared FastAPI dependency, and every core
     list_*()/count_*() pair enforces the same MAX_LIMIT independently
     (defense in depth: a future caller that reaches these functions without
     going through the API — a CLI flag, a batch script — must not be able to
     pull an unbounded result set either).
  2. NESTED COLLECTIONS inside a SINGLE-RESOURCE response — GET
     /results/{id}'s ledger history, its review log, its per-region
     components. The caller did not ask for a page of these; the resource
     just happens to contain a list with no inherent bound (an append-only
     ledger gains one row per re-score; an answer can accumulate any number
     of reviews over its lifetime). `cap()` below is for that case: no
     limit/offset request, just "here are the first N, and whether that is
     all of them."

MAX_LIMIT=200 is the platform-wide ceiling named across every new endpoint in
this pass. It lived first as a local constant in core/question_bank.py; it is
centralized here so every module enforces the same number rather than six
modules each hardcoding 200 and drifting the day one of them changes.
"""
from __future__ import annotations

from typing import Any, Sequence

#: Hard ceiling on rows per page, across every paginated list endpoint.
MAX_LIMIT = 200

#: What a list endpoint returns when the caller does not specify a limit.
DEFAULT_LIMIT = 50

#: Cap on a NESTED collection embedded in a single-resource response (case 2
#: above). Deliberately smaller than MAX_LIMIT: this is not a page the caller
#: asked for by size, it is a safety valve on an unbounded list buried inside
#: one resource's report, and 50 already exceeds what a human reviewer reads
#: in one sitting.
NESTED_MAX = 50


def clamp_limit(limit: int, *, max_limit: int = MAX_LIMIT) -> int:
    """Enforces `max_limit` by CLAMPING, not rejecting.

    A request for more rows than the server hands back in one page is not a
    malformed request — it is a client that does not know, or does not care,
    where the cap is. Clamping does the obviously-right thing and lets
    `total` in the response tell the client how many more pages exist; a 422
    here would only make every such client retry with the max value anyway.
    api/deps/pagination.py::get_pagination does the same clamp at the API
    edge — this is the second, independent enforcement described in the
    module docstring.

    Still raises for `limit < 1`: that is not "too big", it is a request for
    a non-positive page, which has no sensible clamp.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    return min(limit, max_limit)


def cap(items: Sequence[Any], *, max_items: int = NESTED_MAX) -> dict[str, Any]:
    """Caps a nested collection and reports the truth about what was cut.

    Returns {"items": ..., "total": ..., "truncated": ...}. `total` is ALWAYS
    the full count of `items` as passed in, never the length of what is
    returned — so a client that only reads `items` still gets a correct
    `total` to compare it against, and can tell "you're seeing everything"
    apart from "there is more you are not".

    Only correct when `items` really is the complete set. If a caller already
    bounded the query that produced `items` (e.g. a SQL `LIMIT`), it must
    pair that with a real `SELECT count(*)` for `total` rather than feeding
    the already-capped rows through here — see core/jobs.py::list_jobs and
    friends, which fetch limit/offset pages plus a separate count for exactly
    that reason. `cap()` itself is for the case where the whole collection is
    already in memory (e.g. a JSONB array pulled from one already-fetched
    row) and only display needs bounding.
    """
    items = list(items)
    total = len(items)
    return {
        "items": items[:max_items],
        "total": total,
        "truncated": total > max_items,
    }
