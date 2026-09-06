"""users.py — password hashing and the login/bootstrap reads over migration 017.

WHAT LIVES HERE AND WHAT DOES NOT

This module owns exactly two things: how a password becomes a stored string,
and the handful of statements that touch `users` / `refresh_tokens`. It does
NOT own sessions, tokens, headers, or HTTP status codes — api/deps/identity.py
keeps that job, and it will call into here rather than growing its own SQL
(CLAUDE_CONTEXT.md §11 rule 1: scripts/ and api/ are two front doors onto one
core/ library).

WHY THE READS GO THROUGH auth_lookup_user() AND NOT A SELECT

Migration 017 gives the application role column-level SELECT on `users` that
EXCLUDES password_hash. `SELECT * FROM users` does not return a hash — it
raises "permission denied for column password_hash". That is deliberate: the
hash is only ever a return value of the SECURITY DEFINER function, which
opens the row-level window for exactly one email address and closes it again.
So `lookup_user_for_login()` below is not a convenience wrapper; it is the
only read path that exists.

WHICH HASH, AND WHY THERE ARE TWO

New passwords are hashed with **bcrypt**, through passlib's CryptContext
(`passlib[bcrypt]`, pinned in requirements.txt). Stored hashes are
self-describing (`$2b$12$...`), so the scheme is a property of each row rather
than of the codebase, and passlib's `deprecated="auto"` reports when a row was
written by an older scheme.

`verify_password()` ALSO still verifies the **scrypt** format this module used
before authentication landed — `scrypt$n$r$p$salt$hash`, written by
hashlib.scrypt. That is not indecision: `scripts/bootstrap_platform_admin.py`
may already have created the platform admin account before this change, and a
change that invalidates the one account nobody can re-create through the API
is not a migration, it is a lockout. So verification DISPATCHES ON THE STORED
PREFIX, and the same mechanism carries a future move to argon2.

WHAT `needs_rehash()` DOES NOT DO, AND WHY

It reports; it does not upgrade. The obvious move — re-hash a legacy row with
bcrypt during a successful login, when the plaintext is briefly in hand — is
not available, and the reason is worth stating so nobody adds it back:

    THE LOGIN PATH RUNS ON A CONNECTION WITH NO TENANT CONTEXT
    (api/deps/db.py::get_auth_conn). Under it, `users`' tenant_isolation
    policy matches nothing and its two auth policies are FOR SELECT, so an
    `UPDATE users SET password_hash = ...` there would match ZERO ROWS AND
    REPORT SUCCESS — a silent no-op on the security-critical path, i.e.
    exactly the §6 failure mode this codebase keeps fencing off.

Making it work would mean either a write policy on `users` reachable from an
unauthenticated connection, or a SECURITY DEFINER function that sets any
account's hash by id — a primitive whose safety would rest entirely on the
application remembering to verify a password first. Neither is worth a
transparent format upgrade. So legacy rows are upgraded by a password CHANGE
(where the caller is authenticated and the connection is tenant-scoped), and
`api/routers/auth.py` logs a warning naming the account so an operator can
ask for one.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import uuid
from typing import Any, Optional

from passlib.context import CryptContext

# passlib 1.7.4 reads `bcrypt.__about__.__version__` to identify its backend.
# bcrypt 4.1 removed that attribute, so passlib logs a full TRACEBACK at
# WARNING ("(trapped) error reading bcrypt version") the first time it hashes
# — on stderr, in every process, including scripts/bootstrap_platform_admin.py
# where it lands in the middle of an operator's output and reads like a
# failure. Hashing works fine; only the version PROBE fails.
#
# Silenced narrowly: this one logger, and only at WARNING and below, so a real
# error from passlib's bcrypt handler still surfaces. Delete this when passlib
# ships a release that supports bcrypt 4.1+ (at which point the requirements.txt
# pin `bcrypt<5` comes off too).
logging.getLogger("passlib.handlers.bcrypt").setLevel(logging.ERROR)

#: bcrypt via passlib. `deprecated="auto"` marks every scheme but the first
#: as needing a rehash, which is exactly what carries the pre-auth scrypt rows
#: (verified below by hand, since passlib has no handler for that format) into
#: bcrypt on their owners' next successful login.
_PASSLIB = CryptContext(schemes=["bcrypt"], deprecated="auto")

#: The LEGACY format, still verified, never written. See the module docstring.
#: Parameters are read back out of each stored string rather than assumed, so
#: a row written with different ones keeps verifying.
_LEGACY_SCHEME = "scrypt"

VALID_ROLES = ("teacher", "admin", "platform_admin")

#: bcrypt hashes at most this many BYTES of a secret. See hash_password().
MAX_PASSWORD_BYTES = 72


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str) -> str:
    """Returns the string to store in users.password_hash.

    A passlib bcrypt hash (`$2b$12$...`), which carries its own scheme and
    cost factor. Never store, log, or return the password itself.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    # bcrypt silently ignores everything past 72 BYTES, and a caller who
    # supplies a longer passphrase would otherwise be authenticated by its
    # first 72 bytes without ever being told. Refuse instead of truncating:
    # truncation is the failure that makes two different passwords equal.
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password is longer than bcrypt's {MAX_PASSWORD_BYTES}-byte limit; "
            f"everything past it would be ignored, so it is refused rather "
            f"than silently truncated")
    return _PASSLIB.hash(password)


def needs_rehash(stored: Optional[str]) -> bool:
    """True when `stored` was written by a scheme we no longer write.

    Called by the login endpoint AFTER a successful verification — that is the
    only moment the plaintext exists and a new hash can be computed. Every
    legacy scrypt row reports True (passlib does not recognise the format at
    all), which is how the pre-auth hashes drain away.
    """
    if not stored:
        return False
    if stored.startswith(_LEGACY_SCHEME + "$"):
        return True
    try:
        return _PASSLIB.needs_update(stored)
    except ValueError:
        # Unrecognised entirely. Not something to rehash on the way past — a
        # hash nothing can verify is a data problem for a healthcheck to find.
        return False


def verify_password(password: str, stored: Optional[str]) -> bool:
    """Constant-time check of a candidate password against a stored hash.

    Returns False rather than raising for a malformed or missing hash: the
    caller is a login endpoint, and every failure mode there must produce the
    same answer in the same shape. A malformed hash is a data problem to find
    in a healthcheck, not a 500 that tells an attacker their email exists.

    Dispatches on the stored string's own format, so bcrypt (what we write
    now) and the legacy scrypt rows both verify. Adding argon2 later is one
    more entry in the CryptContext, not a flag day — see the module docstring.
    """
    if not password or not stored:
        return False

    if stored.startswith(_LEGACY_SCHEME + "$"):
        return _verify_legacy_scrypt(password, stored)

    try:
        return _PASSLIB.verify(password, stored)
    except (ValueError, TypeError):
        # passlib raises on a hash it cannot parse, and on a secret longer
        # than bcrypt accepts. Neither is an authentication success, and
        # neither may become a 500 on the login path.
        return False


def _verify_legacy_scrypt(password: str, stored: str) -> bool:
    """The pre-auth format: ``scrypt$<n>$<r>$<p>$<b64 salt>$<b64 key>``.

    Verified, never written. Parameters come out of the stored string rather
    than from a constant, so a row written with different ones still verifies.
    """
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != _LEGACY_SCHEME:
        return False

    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError):
        return False

    try:
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt, n=n, r=r, p=p, dklen=len(expected),
        )
    except ValueError:
        # Nonsensical parameters recorded in the hash string.
        return False

    return hmac.compare_digest(candidate, expected)


#: Columns auth_lookup_user() returns, in order.
_LOGIN_COLUMNS = (
    "user_id", "reviewer_id", "email", "password_hash",
    "role", "college_id", "is_active",
)


def lookup_user_for_login(cur, email: str) -> Optional[dict]:
    """Resolves one email to its credential row, or None.

    Runs BEFORE any tenant context exists — that is the whole reason migration
    017 exposes this as a SECURITY DEFINER function instead of a plain SELECT.
    Do NOT "simplify" this into `SELECT ... FROM users WHERE email = %s`: the
    application role cannot read password_hash, and setting
    app.is_platform_admin to get around that would hand an unauthenticated
    request cross-tenant read on all nine RLS-protected tables for the
    duration of the transaction.

    Returns the row INCLUDING is_active and including inactive users. The
    caller must verify the password FIRST and only then reject on is_active,
    so that a disabled or nonexistent account costs the same wall-clock time
    as a live one — otherwise login is a user-enumeration oracle.
    """
    if not email or not email.strip():
        return None

    cur.execute("SELECT * FROM auth_lookup_user(%s::citext)", (email.strip(),))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(_LOGIN_COLUMNS, row))


#: Columns auth_user_by_id() returns, in order. Note the ABSENCE of
#: password_hash — see the function's comment in migration 017 §5.
_SESSION_COLUMNS = ("user_id", "reviewer_id", "email", "role", "college_id",
                    "is_active")


def lookup_user_by_id(cur, user_id) -> Optional[dict]:
    """Re-reads one account by id, without its hash. The refresh path's read.

    POST /auth/refresh starts from a refresh token, which resolves to a
    user_id and nothing else, and it must build the new access token from the
    account's CURRENT role, reviewer_id and college — not from the claims of
    the token being replaced. An account demoted, moved, or deactivated after
    it logged in would otherwise keep minting working access tokens for as
    long as its refresh token lived, which is the window revocation exists to
    close.

    Like `lookup_user_for_login`, this is not a wrapper around a SELECT that
    would also work: with no tenant context the `users` policies match
    nothing, and the row is reachable only through the SECURITY DEFINER
    function this calls.
    """
    if user_id is None:
        return None
    cur.execute("SELECT * FROM auth_user_by_id(%s::uuid)", (str(user_id),))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(_SESSION_COLUMNS, row))


def platform_admin_exists(cur) -> bool:
    """True if any platform_admin account is present.

    Needs a platform-admin RLS context to be meaningful: platform_admin rows
    have college_id IS NULL, so `tenant_isolation` never matches them and a
    tenant-scoped connection would always answer False.
    """
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM users WHERE role = 'platform_admin')")
    return bool(cur.fetchone()[0])


def create_user(
    cur,
    *,
    name: str,
    email: str,
    password: str,
    role: str,
    college_id: Optional[Any] = None,
    reviewer_role: str = "admin",
    reviewer_id: Optional[Any] = None,
) -> dict:
    """Creates the `reviewers` row and its `users` row in ONE transaction.

    Both, together, always. A users row without its reviewer cannot exist
    (reviewer_id is NOT NULL), and a reviewer created without its user in the
    same transaction is an orphan actor that nobody can log in as. Pass an
    existing `reviewer_id` to attach a login to a reviewer who already exists
    — the UNIQUE constraint refuses a second login for the same person.

    college_id must be None for role='platform_admin' and set otherwise
    (users_platform_admin_has_no_college), and must equal the reviewer's
    college_id (trg_users_reviewer_college_agrees) — which is why this
    function creates the reviewer with the SAME college_id rather than letting
    the two drift apart.

    Returns {"user_id", "reviewer_id"}. Does not commit; the caller owns the
    transaction (and therefore --dry-run).
    """
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {VALID_ROLES}, got {role!r}")
    if role == "platform_admin" and college_id is not None:
        raise ValueError(
            "a platform_admin has no college — college_id must be None "
            "(users_platform_admin_has_no_college enforces this in the DB too)")
    if role != "platform_admin" and college_id is None:
        raise ValueError(
            f"role {role!r} requires a college_id; only platform_admin may "
            f"have none")
    if not email or not email.strip():
        raise ValueError("email is required")

    email = email.strip()

    if reviewer_id is None:
        cur.execute(
            """
            INSERT INTO reviewers (reviewer_id, name, role, email, college_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING reviewer_id
            """,
            (str(uuid.uuid4()), name, reviewer_role, email, college_id),
        )
        reviewer_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO users (user_id, reviewer_id, email, password_hash, role,
                           college_id, is_active)
        VALUES (%s, %s, %s::citext, %s, %s, %s, true)
        RETURNING user_id
        """,
        (str(uuid.uuid4()), reviewer_id, email, hash_password(password),
         role, college_id),
    )
    user_id = cur.fetchone()[0]

    return {"user_id": user_id, "reviewer_id": reviewer_id}


# ═══════════════════════════ sessions (refresh_tokens) ══════════════════════
#
# Every function below calls one of migration 017 §5's SECURITY DEFINER
# `auth_*` functions and NEVER touches the table directly — it cannot: §4
# revokes the application role's every privilege on `refresh_tokens`, so a
# hand-written `SELECT ... FROM refresh_tokens` raises "permission denied"
# rather than quietly working. That is deliberate. Each auth_* function opens
# a one-row window (by token hash, or by user id), runs one statement, and
# closes it again; a direct query would need a wider door.
#
# The liveness test (`revoked_at IS NULL AND expires_at > now()`) lives inside
# auth_redeem_refresh_token, not in any caller here, so no call site can
# forget it. Forgetting it once is what would make logout cosmetic.


def hash_refresh_token(token: str) -> str:
    """The value stored in refresh_tokens.token_hash.

    A plain SHA-256, NOT a password KDF, and deliberately so: a refresh token
    is 256 bits of `secrets.token_urlsafe` output, not a guessable human
    secret, so there is nothing for a slow hash to defend against — and the
    redemption path runs on every token refresh, where scrypt's cost would be
    paid for no benefit. What matters is that the database never holds a
    usable token.
    """
    if not token:
        raise ValueError("token must be a non-empty string")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    """A fresh opaque refresh token. Returned to the client ONCE; only its
    hash is ever stored (see hash_refresh_token)."""
    return secrets.token_urlsafe(32)


def issue_refresh_token(cur, user_id, *, ttl_days: int) -> dict:
    """Mints a refresh token for `user_id` and records its hash.

    Returns ``{"token", "token_id", "expires_at"}``. The plaintext `token` is
    the ONLY copy that will ever exist — it is returned to the client here and
    nowhere else, and cannot be recovered from the row afterwards.

    `expires_at` is computed by POSTGRES (`now() + make_interval(...)`), not
    by Python. The expiry is compared against `now()` inside
    auth_redeem_refresh_token on the database's clock, so it must be written
    on the same clock; an API host running a few minutes fast would otherwise
    issue tokens that outlive or under-live their stated lifetime.
    """
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")

    token = new_refresh_token()
    cur.execute(
        """
        SELECT auth_issue_refresh_token(
                   %s::uuid, %s, now() + make_interval(days => %s)),
               now() + make_interval(days => %s)
        """,
        (str(user_id), hash_refresh_token(token), ttl_days, ttl_days),
    )
    token_id, expires_at = cur.fetchone()
    return {"token": token, "token_id": token_id, "expires_at": expires_at}


#: Columns auth_redeem_refresh_token() returns, in order.
_REDEEM_COLUMNS = ("token_id", "user_id", "college_id", "expires_at")


def redeem_refresh_token(cur, token: str) -> Optional[dict]:
    """Resolves a refresh token to its session row, or None.

    None covers every failure indistinguishably — unknown token, revoked
    token, expired token — because the caller is an unauthenticated endpoint
    and the difference between "never existed" and "you revoked this" is not
    information a holder of a bad token has earned.

    Does NOT revoke the row. Rotation is the caller's decision and is done
    explicitly (api/routers/auth.py revokes the presented token in the same
    transaction that issues its replacement), so that this function stays
    usable for a future "is this session still live?" check.
    """
    if not token or not token.strip():
        return None

    cur.execute(
        "SELECT * FROM auth_redeem_refresh_token(%s)",
        (hash_refresh_token(token.strip()),),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip(_REDEEM_COLUMNS, row))


def revoke_refresh_token(cur, token: str) -> bool:
    """Logout. True if THIS call revoked a live token.

    False for an unknown, already-revoked or expired token. The endpoint
    answers 204 either way — telling a caller "that token was already dead"
    is an oracle over tokens they do not hold — but the boolean is here so
    that a caller which wants to log the difference can.
    """
    if not token or not token.strip():
        return False
    cur.execute(
        "SELECT auth_revoke_refresh_token(%s)", (hash_refresh_token(token.strip()),))
    return bool(cur.fetchone()[0])


def revoke_all_refresh_tokens(cur, user_id) -> int:
    """Logout everywhere. Returns how many live sessions were revoked.

    The password-change / account-disabled lever. Bounded to one user by the
    definer function, which is the only reason it is allowed to touch more
    than one row.
    """
    if user_id is None:
        return 0
    cur.execute("SELECT auth_revoke_all_refresh_tokens(%s::uuid)", (str(user_id),))
    return int(cur.fetchone()[0])
