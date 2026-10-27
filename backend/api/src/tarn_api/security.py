"""Access tokens, the refresh cookie and the signed-in principal.

- Access token: a short-lived JWT (HS256, signing key from the secret store) in the
  ``Authorization: Bearer`` header; claims ``sub`` (user), ``cid`` (college), ``sid``
  (session). The role is *not* trusted from the token: every request reloads the user.
- Refresh token: opaque, in an HttpOnly, SameSite=Strict cookie limited to ``/api/v1/auth``;
  rotated on every refresh. "Remember session" makes the cookie persistent and extends the
  session (``AuthSettings.remembered_session``).

Every authenticated request resolves user -> college and opens that college's session, so
row-level security applies (``Unit``)."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tarn_api.backends import Backends, Unit
from tarn_core.domain.identity import AuthSession
from tarn_core.domain.personas import CAPABILITIES, Capability
from tarn_core.domain.tenancy import User
from tarn_core.errors import NotFoundError
from tarn_core.ids import AuthSessionId, BookletId, CollegeId, UserId

REFRESH_COOKIE = "tarn_refresh"
REFRESH_PATH = "/api/v1/auth"
ISSUER = "tarn"
_ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False, description="Access token from /api/v1/auth/login")


def backends_of(request: Request) -> Backends:
    backends: Backends = request.app.state.backends
    return backends


BackendsDep = Annotated[Backends, Depends(backends_of)]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UserId
    college_id: CollegeId
    session_id: AuthSessionId


def issue_access_token(backends: Backends, user: User, session: AuthSession) -> tuple[str, int]:
    now = backends.clock.now()
    ttl = timedelta(minutes=backends.access_token_minutes)
    claims = {
        "iss": ISSUER,
        "sub": str(user.id),
        "cid": str(user.college_id),
        "sid": str(session.id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(claims, backends.signing_key, algorithm=_ALGORITHM), int(ttl.total_seconds())


def _unauthorized(detail: str = "Sign in again.") -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def principal(
    backends: BackendsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise _unauthorized("Sign in first.")
    try:
        claims = jwt.decode(
            credentials.credentials,
            backends.signing_key,
            algorithms=[_ALGORITHM],
            issuer=ISSUER,
            # Expiry is checked against the backends' clock below (tests use a fixed clock).
            options={
                "require": ["exp", "iat", "sub", "cid", "sid"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        if int(claims["exp"]) <= backends.clock.now().timestamp():
            raise _unauthorized()
        return Principal(
            user_id=UserId(UUID(claims["sub"])),
            college_id=CollegeId(UUID(claims["cid"])),
            session_id=AuthSessionId(UUID(claims["sid"])),
        )
    except (jwt.InvalidTokenError, ValueError, KeyError):
        raise _unauthorized() from None


PrincipalDep = Annotated[Principal, Depends(principal)]


ROLE_REFUSED = "Your role cannot do this."


def current_user(unit: Unit, who: Principal, *capabilities: Capability) -> User:
    """The signed-in user, reloaded in this request's college session. 401 if the user or
    session is gone or disabled; 403 unless the persona has one of ``capabilities`` (when
    given; the table is ``tarn_core.domain.personas.CAPABILITIES``, D177)."""
    try:
        user = unit.scope.users.get(who.college_id, who.user_id)
    except NotFoundError:
        raise _unauthorized() from None
    if not user.active or not unit.auth.session_active(who.college_id, who.session_id):
        raise _unauthorized()
    if capabilities and not CAPABILITIES[user.role] & set(capabilities):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=ROLE_REFUSED)
    return user


def booklet_user(unit: Unit, who: Principal, capability: Capability, booklet_id: UUID) -> User:
    """``current_user`` for a route on one booklet: the booklet must be in the user's reach
    (its paper's subject is one of the SME's, D178), otherwise it is not found (404)."""
    user = current_user(unit, who, capability)
    unit.access.booklet(who.college_id, unit.reach(user), BookletId(booklet_id))
    return user


def set_refresh_cookie(
    response: Response, backends: Backends, token: str, session: AuthSession
) -> None:
    max_age = None
    if session.remember:
        max_age = int((session.expires_at - backends.clock.now()).total_seconds())
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=max_age,
        path=REFRESH_PATH,
        httponly=True,
        secure=backends.secure_cookies,
        samesite="strict",
    )


def clear_refresh_cookie(response: Response, backends: Backends) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_PATH,
        httponly=True,
        secure=backends.secure_cookies,
        samesite="strict",
    )
