"""api/deps/pagination.py — the ONE shared limit/offset dependency every
paginated list endpoint takes.

Before this pass, GET /api/v1/questions was the only list endpoint, and it
declared its own `limit: int = Query(default=50, ge=1, le=200)` inline. `le=200`
means FastAPI answers 422 the moment a client asks for more than the cap — a
reasonable choice, but not the one this pass standardizes on: a request for
more rows than the server hands back in one page is not malformed, it is a
client that does not know (or does not care) where the cap sits. So every
paginated endpoint added here — and /questions, brought in line with them —
takes `Pagination = Depends(get_pagination)` instead, which CLAMPS a limit
above MAX_LIMIT rather than rejecting it. `total` in the response is what
tells the client how many rows exist beyond the page it got back.

core/pagination.py::clamp_limit is the second, independent enforcement of the
same MAX_LIMIT — every core list_*() function clamps again on its own, so a
caller that reaches one directly (bypassing this dependency entirely) still
cannot pull an unbounded result set.
"""
from __future__ import annotations

import dataclasses

from fastapi import Query

from core.pagination import DEFAULT_LIMIT, MAX_LIMIT


@dataclasses.dataclass(frozen=True)
class Pagination:
    """The resolved, already-clamped limit/offset for one request."""

    limit: int
    offset: int


def get_pagination(
    limit: int = Query(
        default=DEFAULT_LIMIT,
        ge=1,
        description=(
            f"Rows per page. Values above {MAX_LIMIT} are CLAMPED to "
            f"{MAX_LIMIT}, not rejected — see this module's docstring. "
            f"`total` in the response reports the real count so a client can "
            f"tell it is seeing a partial page."
        ),
    ),
    offset: int = Query(
        default=0, ge=0, description="Rows to skip, for the next page."
    ),
) -> Pagination:
    return Pagination(limit=min(limit, MAX_LIMIT), offset=offset)
