"""api/deps/identity.py — who is making this request.

╔══════════════════════════════════════════════════════════════════════════╗
║ THIS FILE IS A STUB AND GETS REPLACED WHOLESALE WHEN REAL AUTH IS BUILT. ║
╚══════════════════════════════════════════════════════════════════════════╝

Right now identity is resolved from an `X-Debug-College-Id` request header.
That is obviously not authentication — anyone can send any college's UUID and
get that college's data. It exists so the tenant-context plumbing
(api/deps/db.py) can be built and tested before auth exists.

THE CONTRACT THAT SURVIVES THE REPLACEMENT:

  * `get_current_user()` returns a `CurrentUser` and raises HTTP 401 when it
    cannot. Everything downstream depends only on that.
  * This is the ONLY module in the entire codebase that knows the debug
    header exists. Nothing else may read `X-Debug-College-Id`, mention it in
    a signature, or default a `college_id` from a header. grep for that
    header name: if it appears anywhere outside this file and its tests, the
    replacement will not be a one-file change.
  * Endpoints ask for `CurrentUser`, never for "the college id header".

When real auth lands (JWT / session / SSO), the body of `get_current_user()`
is rewritten to verify a token and read the college_id (and eventually a
role, a user id, and permissions) from the verified claims. The signature,
the return type, and every call site stay exactly as they are.
"""
from __future__ import annotations

import dataclasses
import uuid

from fastapi import Header, HTTPException, status

#: The stub credential. Named once, here, so the eventual deletion of this
#: mechanism is a single-file change (see the module docstring).
DEBUG_COLLEGE_HEADER = "X-Debug-College-Id"


@dataclasses.dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller, as far as the rest of the API is concerned.

    Deliberately minimal. Fields are added when an endpoint actually needs
    them, not speculatively — a stub that invents a permissions model now
    would have to be un-invented when real auth arrives.

    college_id
        The tenant this request acts within. This is the value that becomes
        `app.current_college_id` for the request's transaction, so it is the
        single hinge of tenant isolation for the whole API.
    is_platform_admin
        Cross-tenant system caller. Always False under the stub — there is no
        safe way to let a plain header claim admin, and no endpoint needs it
        yet. Real auth will populate it from verified claims.
    """

    college_id: uuid.UUID
    is_platform_admin: bool = False


def get_current_user(
    x_debug_college_id: str | None = Header(
        default=None,
        alias=DEBUG_COLLEGE_HEADER,
        description=(
            "TEMPORARY stub credential — the college UUID this request acts "
            "as. Replaced by real authentication; see api/deps/identity.py."
        ),
    ),
) -> CurrentUser:
    """Resolves the calling user, or raises 401.

    Never returns a user with a missing/blank college_id. A "user" without a
    tenant would flow straight into api/deps/db.py's `SET LOCAL
    app.current_college_id`, and an RLS context set to an empty or bogus
    value fails CLOSED AND SILENTLY — every query returns zero rows and the
    endpoint reports "no data" instead of "you are not authenticated"
    (CLAUDE_CONTEXT.md §6). So the failure is raised here, loudly, at the
    edge, before any connection is opened.
    """
    if x_debug_college_id is None or not x_debug_college_id.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Missing {DEBUG_COLLEGE_HEADER} header. This API is in its "
                f"pre-auth stub phase and identifies the calling tenant from "
                f"that header (api/deps/identity.py). Without a tenant the "
                f"request cannot set app.current_college_id, and RLS would "
                f"silently return zero rows rather than error — so the "
                f"request is rejected here instead."
            ),
        )

    raw = x_debug_college_id.strip()
    try:
        college_id = uuid.UUID(raw)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"{DEBUG_COLLEGE_HEADER}={raw!r} is not a valid UUID. "
                f"colleges.college_id is a uuid column and the RLS policy "
                f"casts app.current_college_id to uuid, so a malformed value "
                f"would fail at query time inside the tenant policy rather "
                f"than here."
            ),
        )

    return CurrentUser(college_id=college_id, is_platform_admin=False)
