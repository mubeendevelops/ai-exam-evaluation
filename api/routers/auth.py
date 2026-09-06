"""api/routers/auth.py — login, refresh, logout, whoami.

════════════════════════════════════════════════════════════════════════════
THE FOUR ENDPOINTS, AND WHAT IS DELIBERATELY ABSENT
════════════════════════════════════════════════════════════════════════════

    POST /auth/login    email + password -> access token + refresh token
    POST /auth/refresh  refresh token    -> a NEW pair (the old one is revoked)
    POST /auth/logout   refresh token    -> 204, that session is dead
    GET  /auth/me       bearer token     -> the account behind it

THERE IS NO SIGNUP ENDPOINT, and its absence is a product decision, not a
gap. Accounts are created by an admin — through `core/users.py::create_user`,
today from `scripts/bootstrap_platform_admin.py` (the first platform admin)
and from a future admin-invite endpoint. Open self-registration on a platform
where an account IS a tenant membership would let anyone mint themselves a
login; the interesting question is not "can you prove you own this mailbox"
but "which college are you a teacher at", which only that college can answer.

THERE IS NO PASSWORD-CHANGE ENDPOINT YET either. When one is added it must
call `core/users.py::revoke_all_refresh_tokens()` in the same transaction —
a password change that leaves old sessions alive is not a password change.

════════════════════════════════════════════════════════════════════════════
WRONG PASSWORD AND UNKNOWN EMAIL MUST BE INDISTINGUISHABLE
════════════════════════════════════════════════════════════════════════════

In the RESPONSE — one status code (401), one detail string, no field naming
which half failed — and in the TIME TAKEN. The second is the one that gets
forgotten: if a nonexistent address returns before any hashing happens, an
attacker learns which addresses are real by timing alone, at any scale, and
then has a list of accounts worth attacking. So `_login()` below:

  * verifies the presented password against a DECOY HASH when no user row
    exists, paying the same bcrypt cost as a real verification;
  * checks `is_active`, and the college's status, only AFTER the hash has
    been verified, so a disabled account and a live one cost the same;
  * returns the same object from all four failure paths.

`core/users.py::lookup_user_for_login` returns inactive users for exactly
this reason, and says so in its docstring.

════════════════════════════════════════════════════════════════════════════
THE CONNECTION THESE ENDPOINTS USE
════════════════════════════════════════════════════════════════════════════

`get_auth_conn` — no tenant context and no admin bypass, because resolving
the tenant is the OUTPUT of authentication. Read that dependency's docstring
before touching anything here: it is safe only because every row this router
reaches goes through migration 017's SECURITY DEFINER `auth_*` functions,
each opening a one-row window. `/auth/me` takes NO connection at all — the
answer is already in the verified token.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

import core.users as users
from api.deps.db import get_auth_conn
from api.deps.identity import CurrentUser, create_access_token, get_current_user
from api.deps.ratelimit import LoginRateLimiter, client_ip
from api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)
from api.settings import Settings, get_settings

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

#: A bcrypt hash of a password nobody holds, verified against when the email
#: is unknown so that path costs the same as a real one. Computed once at
#: import — computing it per request would add a second bcrypt to the branch
#: it is meant to make INDISTINGUISHABLE from the one-bcrypt branch, which is
#: the same timing leak in the opposite direction.
_DECOY_HASH = users.hash_password("this password authenticates nobody")

#: The one answer every login failure gives. Built fresh per raise (an
#: HTTPException carries per-request state), but from one string, so the two
#: cases cannot drift apart in a later edit.
_LOGIN_REFUSED = "Incorrect email or password."


def _refused() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_LOGIN_REFUSED,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _settings_for(request: Request) -> Settings:
    """The settings this app instance was built with. See
    api/deps/identity.py::_settings_for — same reason, same hazard."""
    return getattr(request.app.state, "settings", None) or get_settings()


def _limiter_for(request: Request) -> LoginRateLimiter:
    """The app's login limiter, created in create_app().

    Per-app rather than module-global so that two apps in one process (every
    test module builds its own) do not share a counter — a test that logs in
    a dozen times would otherwise rate-limit an unrelated test.
    """
    return request.app.state.login_rate_limiter


def _user_response(row: dict) -> UserResponse:
    return UserResponse(
        user_id=row["user_id"],
        reviewer_id=row["reviewer_id"],
        email=str(row["email"]),
        role=row["role"],
        college_id=row["college_id"],
    )


def _issue_pair(cur, row: dict, settings: Settings) -> TokenResponse:
    """Mints an access token + refresh token for a verified account.

    Shared by login and refresh so the two cannot issue different-looking
    sessions; the only difference between them is how the account was proven.
    """
    access_token, access_expires = create_access_token(
        user_id=row["user_id"],
        reviewer_id=row["reviewer_id"],
        email=str(row["email"]),
        role=row["role"],
        college_id=row["college_id"],
        settings=settings,
    )
    refresh = users.issue_refresh_token(
        cur, row["user_id"], ttl_days=settings.refresh_token_ttl_days)

    return TokenResponse(
        access_token=access_token,
        expires_in=int(settings.access_token_ttl_minutes * 60),
        expires_at=access_expires,
        refresh_token=refresh["token"],
        refresh_expires_at=refresh["expires_at"],
        user=_user_response(row),
    )


def _college_can_act(cur, college_id) -> bool:
    """Is this account's college one the platform will act as?

    The same rule `api/deps/db.py::_require_known_college` applies per
    request, applied once at login: a suspended college is one the platform
    has deliberately switched off, and issuing its users a working session
    would mean handing out tokens whose every subsequent request 401s. A
    platform_admin has no college and skips this.

    Runs AFTER the password check, so it cannot be used to probe which
    colleges are suspended without already holding valid credentials for one.
    """
    if college_id is None:
        return True
    cur.execute("SELECT status FROM colleges WHERE college_id = %s", (str(college_id),))
    row = cur.fetchone()
    return row is not None and row[0] == "active"


# ═══════════════════════════════════ login ══════════════════════════════════

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange an email and password for an access + refresh token",
    responses={
        401: {"description": "Incorrect email or password. This is also the "
                             "answer for a disabled account and for one whose "
                             "college is suspended — see the module docstring."},
        429: {"description": "Too many failed attempts. `Retry-After` says "
                             "when to try again."},
    },
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    conn=Depends(get_auth_conn),
) -> TokenResponse:
    """Verifies a password and starts a session.

    THE ONLY UNAUTHENTICATED ENDPOINT THAT TOUCHES THE DATABASE. Everything
    it can reach is one row wide (migration 017 §5), it is rate-limited per
    IP and per address, and it answers every failure identically.
    """
    settings = _settings_for(request)
    limiter = _limiter_for(request)

    email = body.email.strip()
    # Lower-cased for the counter only: the database compares citext, so
    # "A@b.com" and "a@b.com" are one account and must share one bucket —
    # otherwise the case of the address is a way to buy more attempts.
    keys = [f"ip:{client_ip(request)}", f"email:{email.lower()}"]

    wait = limiter.retry_after(keys)
    if wait is not None:
        # Refused BEFORE the bcrypt verification, which is the point: a
        # limited caller must not be able to spend this process's CPU.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many failed sign-in attempts. Try again in {wait} "
                f"seconds."
            ),
            headers={"Retry-After": str(wait)},
        )

    with conn.cursor() as cur:
        row = users.lookup_user_for_login(cur, email)

        # Verify FIRST, decide after. Every branch below pays for exactly one
        # bcrypt verification, including the one where no account exists.
        stored = row["password_hash"] if row else _DECOY_HASH
        password_ok = users.verify_password(body.password, stored)

        if row is None or not password_ok:
            limiter.record_failure(keys)
            log.info("failed login for %r from %s", email, client_ip(request))
            raise _refused()

        if not row["is_active"]:
            limiter.record_failure(keys)
            log.info("login refused: account %s is disabled", row["user_id"])
            raise _refused()

        if not _college_can_act(cur, row["college_id"]):
            limiter.record_failure(keys)
            log.info("login refused: college %s is not active", row["college_id"])
            raise _refused()

        if users.needs_rehash(row["password_hash"]):
            # Reported, not fixed here. core/users.py's docstring explains why
            # the upgrade cannot happen on this connection: it would be a
            # silent zero-row UPDATE.
            log.warning(
                "account %s still carries a legacy password hash; it will be "
                "upgraded when the password is next changed", row["user_id"])

        tokens = _issue_pair(cur, row, settings)

    # Only a SUCCESS clears the counter, and only the email half of it — see
    # api/deps/ratelimit.py on why a shared NAT does not get its IP counter
    # reset by one user signing in.
    limiter.clear([keys[1]])
    # A token response must never be cached: a shared cache holding one would
    # hand the next caller a live session.
    response.headers["Cache-Control"] = "no-store"
    return tokens


# ══════════════════════════════════ refresh ═════════════════════════════════

@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new access + refresh token",
    responses={401: {"description": "The refresh token is unknown, expired, "
                                    "or has been revoked — one answer for all "
                                    "three."}},
)
def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    conn=Depends(get_auth_conn),
) -> TokenResponse:
    """Rotates the session.

    THE PRESENTED TOKEN IS REVOKED IN THE SAME TRANSACTION that issues its
    replacement. Rotation is what makes a stolen refresh token a bounded
    problem: the thief and the legitimate client cannot both keep using it,
    so the theft surfaces as one of them being logged out instead of as a
    permanent silent second session.

    THE ACCOUNT IS RE-READ, not copied forward from the old token's claims.
    Role, college and is_active can all change between login and refresh; a
    deactivated or demoted account must stop minting usable access tokens at
    that moment, not whenever its refresh token happens to expire. That read
    is `auth_user_by_id()` — migration 017 §5, which returns no hash.
    """
    settings = _settings_for(request)

    with conn.cursor() as cur:
        session = users.redeem_refresh_token(cur, body.refresh_token)
        if session is None:
            # Unknown / expired / revoked, indistinguishably. A holder of a
            # dead token has not earned the difference.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="That refresh token is not valid. Sign in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        row = users.lookup_user_by_id(cur, session["user_id"])
        if row is None or not row["is_active"] or not _college_can_act(
                cur, row["college_id"]):
            # The session is live but the account behind it is not. Kill every
            # session it has rather than only this one: whatever disabled the
            # account meant to end its access, and a second refresh token in
            # another browser would otherwise keep working.
            users.revoke_all_refresh_tokens(cur, session["user_id"])
            log.info("refresh refused for disabled/unusable account %s",
                     session["user_id"])
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="That refresh token is not valid. Sign in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Revoke BEFORE issuing, so a failure between the two leaves the
        # client with a dead token and a clean "sign in again" rather than
        # two live ones.
        users.revoke_refresh_token(cur, body.refresh_token)
        tokens = _issue_pair(cur, row, settings)

    response.headers["Cache-Control"] = "no-store"
    return tokens


# ══════════════════════════════════ logout ══════════════════════════════════

@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a refresh token (and optionally every other session)",
)
def logout(
    body: LogoutRequest,
    conn=Depends(get_auth_conn),
) -> Response:
    """Ends the session the refresh token represents.

    ALWAYS 204, whether the token was live, already revoked, or never
    existed. Reporting the difference would turn logout into an oracle over
    tokens the caller does not hold, and there is nothing a client could
    usefully do with the distinction anyway — either way, that token is now
    dead.

    WHAT LOGOUT CANNOT DO: kill the access token. It is a signed bearer
    credential with no server-side row, so it stays valid until it expires
    (15 minutes by default). That is the trade named in api/deps/identity.py,
    and it is why the access TTL is short. A client logging out should also
    discard its copy.

    `all_sessions` requires the presented token to be LIVE — otherwise anyone
    holding a revoked or guessed token could sign an account out everywhere.
    """
    with conn.cursor() as cur:
        if body.all_sessions:
            session = users.redeem_refresh_token(cur, body.refresh_token)
            if session is not None:
                users.revoke_all_refresh_tokens(cur, session["user_id"])
        else:
            users.revoke_refresh_token(cur, body.refresh_token)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ═══════════════════════════════════ me ═════════════════════════════════════

@router.get(
    "/me",
    response_model=UserResponse,
    summary="The account behind the presented access token",
)
def me(user: CurrentUser = Depends(get_current_user)) -> UserResponse:
    """Who the API thinks you are.

    TAKES NO DATABASE CONNECTION. Every field here is a signature-verified
    claim that `get_current_user` has already checked for internal
    consistency, so a query would only re-fetch what the token already says —
    and would put a database round trip on the endpoint a client calls on
    every page load to decide what to render.

    The consequence, stated rather than discovered: this reflects the account
    AS OF the access token's issue, so a role changed a minute ago shows here
    for up to one access-token lifetime. Requests are authorized from the same
    claims, so the answer is at least honest about what the caller can
    currently do — and /auth/refresh re-reads the row, which is where a
    changed role takes effect.
    """
    return UserResponse(
        user_id=user.user_id,
        reviewer_id=user.reviewer_id,
        email=user.email,
        role=user.role,
        college_id=user.college_id,
    )
