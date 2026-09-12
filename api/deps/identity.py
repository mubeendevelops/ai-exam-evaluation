"""api/deps/identity.py — who is making this request.

════════════════════════════════════════════════════════════════════════════
THIS IS THE ONLY FILE THAT KNOWS HOW A CALLER IS IDENTIFIED
════════════════════════════════════════════════════════════════════════════

It was a stub reading an `X-Debug-College-Id` header until 2026-09-06. It is
now real: a signed, short-lived JWT access token carried in
`Authorization: Bearer <token>`. The header, and the module that read it, are
gone — `grep -rn "X-Debug-College-Id" .` finds nothing outside historical
notes, which was the acceptance test for that replacement.

The replacement was a ONE-FILE change to the API's identity story precisely
because of the contract this module has kept from the beginning, and which
still holds:

  * `get_current_user()` returns a `CurrentUser` and raises 401 when it
    cannot. Every endpoint depends on that and on nothing else. No router,
    service or schema knows what a token is, and none may learn.
  * Endpoints ask for `CurrentUser`, never for "the credential".
  * A caller whose college does not exist or is not active is ALSO a 401,
    raised by api/deps/db.py::get_tenant_conn through `unknown_tenant_error()`
    below. That check needs a database connection, which is why it happens
    there; the wording stays here, so the whole vocabulary of "who are you and
    why were you refused" lives in one place.

════════════════════════════════════════════════════════════════════════════
WHAT IS IN THE TOKEN, AND WHY EACH CLAIM IS THERE
════════════════════════════════════════════════════════════════════════════

    sub    users.user_id        the account
    rid    users.reviewer_id    the ACTOR. Every provenance row in this schema
                                (answer_reviews.reviewer_id,
                                question_status_history.changed_by, …) is
                                attributed to a reviewer, and after this pass
                                that attribution comes from HERE and never
                                from a request body. See RE-4 below.
    email  users.email          for `GET /auth/me` and for logs; never a key.
    role   teacher|admin|platform_admin
    cid    users.college_id     the tenant, NULL iff role = platform_admin
                                (migration 017's biconditional CHECK).
    typ    "access"             so that some other signed thing this key ever
                                produces cannot be presented as a login.
    jti                         a unique id per token, so a specific token can
                                be named in a log without logging the token.

`cid` IS THE HINGE OF TENANT ISOLATION. It comes out of a signature-verified
claim, and api/deps/db.py turns it into `app.current_college_id`. It is never
read from a body, a query parameter or a path — RLS will faithfully scope a
request to whatever college id it is handed, including one an attacker typed.

════════════════════════════════════════════════════════════════════════════
ACCESS TOKENS ARE NOT REVOCABLE. THAT IS WHY THEY ARE SHORT.
════════════════════════════════════════════════════════════════════════════

There is no per-request database lookup here, on purpose: adding one would
put a query in front of every endpoint and would still not be a revocation
list unless it were consulted before every request anyway. So an access token
is valid until it expires (15 minutes by default,
`Settings.access_token_ttl_minutes`), and REVOCATION LIVES ON THE REFRESH
TOKEN, which does have a row and can be killed (migration 017 §2,
`core/users.py::revoke_refresh_token`). Logout revokes the refresh token; the
access token dies of old age minutes later. A deployment that needs
immediate revocation shortens the access TTL — it does not add a lookup here.

════════════════════════════════════════════════════════════════════════════
RE-4 — REVIEWER IDENTITY IS NOT A REQUEST FIELD
════════════════════════════════════════════════════════════════════════════

`OverrideRequest`, `ReviewRequest` and `PromoteRequest` each used to carry a
`reviewer_id`, because identity could not say who was calling. A client could
therefore attribute a review to any reviewer in its own college — migration
003's cross-college trigger refused only reviewers from OTHER colleges. Those
fields are deleted; `CurrentUser.reviewer_id` is what the endpoints pass to
the services now. An audit trail whose subject is chosen by the caller is not
an audit trail.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from api.settings import Settings, get_settings

log = logging.getLogger("api.auth")

#: Roles a token may carry. Mirrors migration 017's users_role_check; a token
#: naming anything else is refused rather than treated as "some other role"
#: with no privileges — an unknown role is a bug or an attack, not a state.
ROLES = ("teacher", "admin", "platform_admin")

#: Roles that act INSIDE one college. The complement of platform_admin, named
#: rather than written out at each call site (see require_college_user).
COLLEGE_ROLES = ("teacher", "admin")

#: `typ` claim on an access token. Checked on every request.
ACCESS_TOKEN_TYPE = "access"

#: Sends `WWW-Authenticate: Bearer` on failures and puts the Authorize button
#: in /docs. auto_error=False so that a MISSING header reaches our own handler
#: and produces the same shaped 401 as a malformed or expired one.
bearer_scheme = HTTPBearer(auto_error=False, description=(
    "A JWT access token from POST /api/v1/auth/login (or /auth/refresh). "
    "Send it as `Authorization: Bearer <token>`."
))


@dataclasses.dataclass(frozen=True)
class CurrentUser:
    """The authenticated caller, as far as the rest of the API is concerned.

    Every field comes from a signature-verified claim. Nothing here is
    supplied by the request body.

    user_id
        The account. Identifies the login, not the human's role in the
        workflow — that is `reviewer_id`.
    reviewer_id
        The ACTOR, `reviewers.reviewer_id`. Every attributed row this API
        writes (answer_reviews, question_reviews, *_status_history) carries
        this value. See RE-4 in the module docstring.
    college_id
        The tenant this request acts within, and the value that becomes
        `app.current_college_id`. None IF AND ONLY IF the caller is a
        platform_admin (migration 017's biconditional CHECK enforces the same
        thing in the database).
    is_platform_admin
        A cross-tenant system caller. Derived from `role`, not carried
        separately, so the two cannot disagree.
    """

    user_id: uuid.UUID
    reviewer_id: uuid.UUID
    email: str
    role: str
    college_id: uuid.UUID | None

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"


# ════════════════════════════ minting a token ═══════════════════════════════

def create_access_token(
    *,
    user_id,
    reviewer_id,
    email: str,
    role: str,
    college_id,
    settings: Settings,
    now: dt.datetime | None = None,
) -> tuple[str, dt.datetime]:
    """Signs an access token for one account. Returns (token, expires_at).

    Called by api/routers/auth.py and by nothing else — an endpoint that could
    mint a token for a user it chose would be a login endpoint wearing a
    different name.

    `now` is a parameter only so tests can sign an ALREADY-EXPIRED token
    without sleeping through a TTL. It defaults to real UTC and no caller in
    api/ passes it.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    if (role == "platform_admin") != (college_id is None):
        # The same biconditional migration 017 enforces on the row. Checked
        # again at signing time because a token is trusted afterwards: a
        # platform_admin pinned to one college, or a teacher with no college,
        # would be a contradiction that get_current_user has to guess about.
        raise ValueError(
            f"college_id must be None for platform_admin and set otherwise; "
            f"got role={role!r}, college_id={college_id!r}")

    issued = now or dt.datetime.now(dt.timezone.utc)
    expires = issued + dt.timedelta(minutes=settings.access_token_ttl_minutes)

    claims = {
        "sub": str(user_id),
        "rid": str(reviewer_id),
        "email": email,
        "role": role,
        "cid": str(college_id) if college_id is not None else None,
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": str(uuid.uuid4()),
    }
    token = jwt.encode(claims, settings.jwt_signing_key,
                       algorithm=settings.jwt_algorithm)
    return token, expires


# ═══════════════════════════ reading a token ════════════════════════════════

def _unauthenticated(detail: str) -> HTTPException:
    """Every 401 from this module, in one shape.

    `WWW-Authenticate: Bearer` is not decoration — it is what tells a client
    (and /docs) that this endpoint takes a bearer token, and it is what
    distinguishes "you sent nothing" from "you may not do this" (403).

    The DETAIL never says whether a token was well-formed-but-for-someone-else,
    or which claim was wrong in a way that identifies an account. It says what
    the caller must do next, and the specifics go to the log.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _settings_for(request: Request) -> Settings:
    """The settings the app was BUILT with, not a fresh read of .env.

    `create_app(settings)` exists so tests (and a future multi-instance
    deployment) can run apps with different configuration in one process. If
    this called `get_settings()` it would verify tokens with a different
    signing key than `api/routers/auth.py` signed them with whenever those two
    differed — an intermittent 401 with nothing wrong in the token.
    """
    return getattr(request.app.state, "settings", None) or get_settings()


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verifies the signature and expiry and returns the claims.

    Raises 401 on anything wrong. python-jose checks `exp` itself and raises
    ExpiredSignatureError, which is a JWTError — so an expired token takes the
    same path as a forged one, and produces the same answer.
    """
    try:
        return jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=[settings.jwt_algorithm],
            # Pinning the algorithm list is the whole defence against the
            # classic `alg: none` / HS-for-RS confusion attack: a token gets
            # verified with the algorithm WE named, never the one it asked for.
            options={"require_exp": True, "require_sub": True},
        )
    except JWTError as exc:
        log.info("rejected access token: %s", exc)
        raise _unauthenticated(
            "Invalid or expired access token. Obtain a new one from "
            "POST /api/v1/auth/login, or exchange your refresh token at "
            "POST /api/v1/auth/refresh."
        )


def user_from_claims(claims: dict[str, Any]) -> CurrentUser:
    """Claims -> CurrentUser, refusing every internally inconsistent token.

    THIS IS WHERE "fails loudly, not silently empty" IS ENFORCED. A token
    whose role is not platform_admin and which carries no `cid` is the exact
    shape that would otherwise flow into api/deps/db.py, set no tenant
    context, and make every RLS-protected read return zero rows — a 200 with
    an empty list, reported to the caller as "you have no data"
    (CLAUDE_CONTEXT.md §6). It is refused here instead, at the edge, before
    any connection is opened.
    """
    role = claims.get("role")
    if role not in ROLES:
        log.warning("token carries unknown role %r", role)
        raise _unauthenticated("Access token carries no usable role.")

    raw_cid = claims.get("cid")
    if role == "platform_admin":
        if raw_cid is not None:
            # A platform_admin scoped to one college is a contradiction: the
            # role says cross-tenant, the claim says one tenant. Refusing is
            # the only answer that does not require choosing which half to
            # believe.
            raise _unauthenticated(
                "Access token claims platform_admin and a college at once.")
        college_id = None
    else:
        if raw_cid is None:
            raise _unauthenticated(
                f"Access token for role {role!r} carries no college. Such a "
                f"request cannot establish a tenant context, and every "
                f"row-level-security policy would then match nothing — the "
                f"request would look successful and return no data. It is "
                f"refused instead."
            )
        try:
            college_id = uuid.UUID(str(raw_cid))
        except ValueError:
            raise _unauthenticated("Access token carries a malformed college id.")

    try:
        user_id = uuid.UUID(str(claims["sub"]))
        reviewer_id = uuid.UUID(str(claims["rid"]))
    except (KeyError, ValueError):
        raise _unauthenticated("Access token is missing its account identity.")

    return CurrentUser(
        user_id=user_id,
        reviewer_id=reviewer_id,
        email=str(claims.get("email") or ""),
        role=role,
        college_id=college_id,
    )


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    """Resolves the calling user from the bearer token, or raises 401.

    Never returns a user with a missing college_id unless that user is a
    platform_admin — see `user_from_claims`, and CLAUDE_CONTEXT.md §6 for why
    that distinction is a security property and not a nicety.
    """
    if credentials is None or not (credentials.credentials or "").strip():
        raise _unauthenticated(
            "Missing bearer token. Send `Authorization: Bearer <token>` with "
            "an access token from POST /api/v1/auth/login."
        )

    settings = _settings_for(request)
    claims = decode_access_token(credentials.credentials.strip(), settings)

    if claims.get("typ") != ACCESS_TOKEN_TYPE:
        # A refresh token is opaque and could never decode here, but this API
        # may one day sign something else with the same key (an invite, a
        # download link). `typ` is what stops that thing being presented as a
        # login the day it exists, rather than after.
        raise _unauthenticated("That token is not an access token.")

    return user_from_claims(claims)


# ══════════════════════════════ authorization ═══════════════════════════════

def require_role(*roles: str):
    """Dependency factory: 403 unless the caller holds one of `roles`.

    403, NOT 404, and that does not contradict the 404-never-403 rule.
    That rule is about ANOTHER TENANT'S RESOURCE, where a 403 would confirm
    that a row exists under an id the caller was guessing at. Here nothing
    about a resource is being revealed: the caller is authenticated, we know
    exactly who they are, and the answer is about THEM. "You may not do this"
    is both true and the only useful thing to say.

    Usage:

        @router.post("/thing", dependencies=[Depends(require_role("admin"))])

    or as a parameter when the endpoint also wants the user object:

        user: CurrentUser = Depends(require_role("admin"))
    """
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        raise ValueError(
            f"require_role({unknown!r}) names roles that cannot exist; valid "
            f"roles are {ROLES}. A dependency guarding against a role no token "
            f"can carry would silently allow nobody, or worse, be deleted as "
            f"dead code.")

    allowed = frozenset(roles)

    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This endpoint requires one of the roles "
                    f"{sorted(allowed)}; you are {user.role!r}."
                ),
            )
        return user

    dependency.__name__ = f"require_role_{'_or_'.join(sorted(allowed))}"
    return dependency


def require_college_user(
    user: CurrentUser = Depends(require_role(*COLLEGE_ROLES)),
) -> CurrentUser:
    """The caller must act INSIDE a college — i.e. must not be a platform_admin.

    Not an arbitrary restriction, and not a statement that platform admins are
    less trusted. It is a consequence of what these endpoints do: they read or
    write rows that carry a `college_id`, and they pass `user.college_id` into
    the explicit tenant predicates that CLAUDE_CONTEXT.md §6 keeps alongside
    RLS. A platform_admin has no college — by CHECK constraint, not by
    accident — so those predicates would compare against NULL, match nothing,
    and the endpoint would answer 404 or `[]` for data that plainly exists.
    That is the fails-closed-looks-like-no-data failure mode, arriving through
    the front door.

    A platform admin who needs a specific college's answers gets a purpose-
    built cross-tenant endpoint on `get_admin_conn`, where the tenant is an
    explicit argument and the response says which college it came from. There
    is no such endpoint yet, and inventing one here would be inventing product.
    """
    return user


# ═════════════════════════ shared refusal vocabulary ════════════════════════

def unknown_tenant_error(college_id, *, reason: str) -> HTTPException:
    """The 401 for a verified token naming a tenant that cannot act.

    Lives here, not at the call site, for the reason the module docstring
    gives: this file owns the vocabulary of identity and refusal.
    api/deps/db.py raises it (that is where the database connection to check
    against exists).

    Returned rather than raised so the call site's `raise` keeps control flow
    visible in the module that decides to reject.

    A 401 and not a 403 or a 404: the token is validly signed, but the tenant
    it names is not one the platform will act as — a deleted or suspended
    college. The caller is not authenticated *as anyone the platform
    recognises*, and every access token they hold for that college has the
    same problem, so "re-authenticate" is the honest instruction. 403 would
    say "we know who you are, you may not do this", which is a different and
    untrue statement.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=(
            f"Your access token names college {college_id}, but {reason}. The "
            f"request is refused here rather than allowed to establish a "
            f"tenant context that owns nothing — under such a context every "
            f"read returns zero rows (which reads as 'no data') and every "
            f"write fails on a college_id foreign key deep inside an "
            f"endpoint. See api/deps/db.py::get_tenant_conn."
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )
