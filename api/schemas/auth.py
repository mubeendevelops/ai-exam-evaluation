"""api/schemas/auth.py — request/response models for /api/v1/auth.

TWO TOKENS, TWO JOBS, AND THE RESPONSE SHAPE THAT KEEPS THEM APART

  access_token   A signed JWT, short-lived (Settings.access_token_ttl_minutes,
                 15 by default), sent on every subsequent request as
                 `Authorization: Bearer <token>`. It is NOT revocable — there
                 is no per-request database lookup to revoke it with — so its
                 lifetime is its blast radius.
  refresh_token  An opaque random string, long-lived, sent ONLY to
                 /auth/refresh and /auth/logout. It has a row in the database
                 (migration 017 §2), so it CAN be revoked, and revoking it is
                 what logout means.

The client stores both and treats them differently, so the API returns them
as two named fields rather than one bundle. `expires_in` is included beside
`access_token` because a client that has to parse the JWT to discover its own
expiry will eventually parse it wrong.

WHAT IS NOT IN HERE: no `college_id` on the way IN, ever. The tenant is an
output of authentication, never an input to a request — see
api/deps/identity.py.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """POST /api/v1/auth/login.

    `email` is a plain `str`, NOT pydantic's `EmailStr`, and that is a
    decision rather than an omission. `EmailStr` needs the `email-validator`
    package, which this repo would be adding for one field; and it would make
    a syntactically invalid address answer 422 while a valid-but-unknown one
    answers 401. Two shapes of "no" on the login endpoint is precisely what
    the rest of this router works to avoid, so every address that is not blank
    takes the same path and gets the same answer at the same speed. The
    database is the authority on the identifier anyway (citext UNIQUE, plus
    users_email_not_blank).
    """

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(
        min_length=1,
        # No max_length matching bcrypt's 72-byte truncation point: a length
        # limit here would be a hint about the hashing scheme, and
        # core/users.py::hash_password refuses over-long secrets at the one
        # place that can do it without guessing at encodings.
        description="The account password. Never logged, never echoed.",
    )


class RefreshRequest(BaseModel):
    """POST /api/v1/auth/refresh — exchange a refresh token for a new pair."""

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    """POST /api/v1/auth/logout.

    Takes the refresh token in the body rather than reading the access token,
    because the access token is not what logout acts on: revoking the refresh
    token is the only part of a session the server can actually end.
    """

    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)
    all_sessions: bool = Field(
        default=False,
        description="Revoke every live session for this account, not just "
                    "this one — the 'sign out everywhere' button. Requires "
                    "the presented refresh token to be live, so it cannot be "
                    "used to log someone else out.",
    )


class UserResponse(BaseModel):
    """The authenticated account. `GET /api/v1/auth/me`, and echoed on login.

    Carries `reviewer_id` deliberately and prominently: it is the id every
    provenance row this API writes is attributed to, and a client that shows
    "reviewed by" needs to be able to recognise itself in that history. It is
    NOT a field a client may send back — see RE-4 in api/deps/identity.py.
    """

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    reviewer_id: uuid.UUID
    email: str
    role: Literal["teacher", "admin", "platform_admin"]
    college_id: uuid.UUID | None = Field(
        default=None,
        description="The tenant this account acts within. Null if and only if "
                    "the role is platform_admin (migration 017's CHECK), "
                    "which is a cross-tenant account with no college of its "
                    "own — not an account whose college is unknown.",
    )


class TokenResponse(BaseModel):
    """What login and refresh both return."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(
        description="Seconds until `access_token` expires. Present so a "
                    "client never has to decode the JWT to schedule its own "
                    "refresh.",
    )
    expires_at: dt.datetime
    refresh_token: str = Field(
        description="Opaque, long-lived, and ROTATED on every refresh: the "
                    "token used to obtain this response is revoked as part of "
                    "issuing it, so a stolen refresh token stops working as "
                    "soon as the legitimate client refreshes with it.",
    )
    refresh_expires_at: dt.datetime
    user: UserResponse
