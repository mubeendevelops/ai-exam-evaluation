"""api/settings.py — typed access to the SAME .env keys the CLI scripts
already use (CLAUDE_CONTEXT.md §8): PG*, MINIO_*, GROQ_API_KEY, GROQ_MODEL.

Deliberately NOT a second source of configuration. core/db.py, core/storage.py
and core/llm.py read os.environ directly and keep doing so — they are shared
with scripts/, which have no FastAPI in scope. This module reads the same
variables so the API can validate configuration at startup and expose it to
request handlers, and `load_dotenv_once()` populates os.environ from .env so
that core/ sees the same values (previously only the shell wrappers and
tests/conftest.py did that).

Adding a key here without adding it to .env.example is a bug: .env.example is
the documented setup contract.
"""
from __future__ import annotations

import functools
import pathlib

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"

#: The origin Vite's dev server binds by default. Only ever used as the
#: CORS_ORIGINS fallback in `api_env == "development"` — see
#: `Settings.cors_origin_list`.
_DEV_DEFAULT_CORS_ORIGIN = "http://localhost:5173"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Postgres (core/db.py) ───────────────────────────────────────────
    pghost: str = "localhost"
    pgport: int = 5432
    pgdatabase: str = "ai_evaluation"
    pguser: str = "postgres"
    pgpassword: str = ""

    # ── MinIO / S3 (core/storage.py); only needed for real storage ──────
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "ai_evaluation"

    # ── Groq LLM (core/llm.py) ──────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = ""

    # ── API-only knobs (no CLI equivalent) ──────────────────────────────
    api_env: str = "development"
    #: Which core/storage.py path POST /api/v1/upload writes through:
    #: "dummy" (deterministic placeholder ref, no MinIO, no boto3) or "minio"
    #: (real upload). Mirrors the CLI scripts' --storage flag and its default,
    #: so the API and the scripts behave the same on a fresh checkout.
    storage_mode: str = "dummy"
    #: Hard cap on an uploaded booklet, in bytes. Enforced while STREAMING the
    #: body, not from the Content-Length header — a client controls that header
    #: and a limit that trusts it is not a limit. 50 MB comfortably exceeds a
    #: scanned booklet (media/booklets/sample_booklet.pdf is a few hundred KB).
    max_upload_bytes: int = 50 * 1024 * 1024
    #: Comma-separated allowed CORS origins. Empty (the default) means
    #: "not configured" — see `cors_origin_list` below for what that resolves
    #: to, which depends on `api_env` and is NOT "*" outside development.
    cors_origins: str = ""
    #: GET /api/v1/_debug/whoami is a throwaway plumbing probe (see
    #: api/routers/debug.py). It is registered ONLY when this is true, and
    #: this defaults to false whenever api_env != "development".
    enable_debug_endpoints: bool | None = None

    # ── Authentication (api/deps/identity.py, api/routers/auth.py) ──────
    #: HMAC signing key for the ACCESS token. There is no default and there
    #: must not be one: a committed fallback secret is a signing key every
    #: checkout of this repo shares, and anyone holding it can mint a token
    #: for any college. `jwt_signing_key` below refuses to run without it
    #: outside development.
    jwt_secret: str = ""
    #: HS256 — one symmetric key, held by the one process that both signs and
    #: verifies. RS256 becomes worth the key management the day something
    #: OTHER than this API needs to verify a token (the job worker, a second
    #: service); python-jose[cryptography] already supports it, so that is a
    #: config change plus a keypair, not a rewrite.
    jwt_algorithm: str = "HS256"
    #: Access-token lifetime. SHORT on purpose: an access token is a bearer
    #: credential that cannot be revoked before it expires (there is no
    #: per-request database lookup to revoke it with), so its blast radius is
    #: exactly this window. Revocation lives on the refresh token, which does
    #: have a row — see migration 017 §2.
    access_token_ttl_minutes: int = 15
    #: Refresh-token lifetime, i.e. how long a client can stay signed in
    #: without re-entering a password. Revocable at any point inside it.
    refresh_token_ttl_days: int = 14
    #: POST /auth/login rate limit: this many attempts per window, per client
    #: IP and per email address, counted in-process (no Redis — see
    #: api/deps/ratelimit.py for what that does and does not buy).
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300

    # ── per-college API rate limits (api/deps/quota.py) ──────────────────
    #: In-process, no Redis — same limitation as LOGIN_RATE_LIMIT_* (per
    #: worker, reset by a restart; see api/deps/quota.py's module docstring
    #: for why that is still worth having and what a real gateway-level limit
    #: would add on top). Keyed by the caller's college (or user id for a
    #: platform_admin), never by IP and never by a body field.
    #:
    #: /evaluate and /questions/generate are the tightest: both spend real
    #: Groq tokens (§9's ~14,400/day, 30/min free-tier ceiling), so a limit
    #: here is what stops one college's burst from starving every other
    #: college's share before core/llm.py's OUTBOUND token bucket even sees
    #: the requests. /papers/generate and /upload do no LLM work but still
    #: write real rows on every call.
    evaluate_rate_limit_attempts: int = 10
    evaluate_rate_limit_window_seconds: int = 60
    questions_generate_rate_limit_attempts: int = 10
    questions_generate_rate_limit_window_seconds: int = 60
    papers_generate_rate_limit_attempts: int = 30
    papers_generate_rate_limit_window_seconds: int = 60
    upload_rate_limit_attempts: int = 20
    upload_rate_limit_window_seconds: int = 60
    #: How many of a college's OWN evaluation_jobs may sit 'queued' or
    #: 'running' at once (api/deps/quota.py::check_concurrent_job_cap). THIS,
    #: not the per-minute limit above, is what actually protects the shared
    #: Groq daily quota: core/llm.py's counter is per-process and resets on
    #: restart, so it cannot see a backlog building slowly across many
    #: requests over hours. Read live from evaluation_jobs on every
    #: POST /evaluate, so it is correct across every API process and worker.
    max_queued_jobs_per_college: int = 5

    @property
    def jwt_signing_key(self) -> str:
        """The key used to sign and verify access tokens.

        In development ONLY, an unset JWT_SECRET falls back to a key generated
        fresh for this process: the API is usable on a bare checkout, and the
        cost is that every restart invalidates outstanding access tokens
        (clients just refresh). Anywhere else, an unset secret raises at
        startup rather than being invented — a per-process key in a multi-
        worker deployment would mean a token signed by worker 1 failing
        verification on worker 2, i.e. intermittent 401s under load, which is
        a far worse day than a refused boot.
        """
        if self.jwt_secret.strip():
            return self.jwt_secret.strip()
        if self.api_env == "development":
            return _development_jwt_secret()
        raise RuntimeError(
            "JWT_SECRET is not set and API_ENV is not 'development'. Access "
            "tokens are signed with it; there is deliberately no default, "
            "because a committed fallback would be a signing key shared by "
            "every checkout of this repo. Generate one with "
            "`python -c \'import secrets; print(secrets.token_urlsafe(48))\'` "
            "and put it in the environment."
        )

    @property
    def debug_endpoints_enabled(self) -> bool:
        if self.enable_debug_endpoints is not None:
            return self.enable_debug_endpoints
        return self.api_env == "development"

    @property
    def cors_origin_list(self) -> list[str]:
        """The origins CORSMiddleware is configured with. NOT just a parse of
        CORS_ORIGINS — this is where "unset" is resolved, and the resolution
        depends on `api_env`.

        Before this pass, an unset CORS_ORIGINS defaulted to "*" in every
        environment, including production — a permissive fallback nobody had
        to opt into. `_configure_cors` (api/main.py) already refuses to pair
        "*" with credentials, which closed the CSRF hole (Hardening pass
        2026-09-05); it never asked whether "*" should be the DEFAULT at all.
        It should not be: a deployment that forgets to set CORS_ORIGINS is a
        deployment that forgot to configure CORS, and "silently permissive"
        is the wrong way to fail that.

          * unset + development -> the local Vite dev server origin. Usable
            on a bare checkout with the default frontend tooling, exactly
            like JWT_SECRET's development fallback.
          * unset + anything else -> raises at STARTUP (via `_configure_cors`
            reading this property while building the app), same shape as
            `jwt_signing_key` above. A production process that will not admit
            a single browser origin is a broken deployment, not a locked-down
            one; better to refuse to boot than to serve with no configured
            CORS policy and no error anywhere.
          * set (to "*" or an explicit list) -> parsed as before, in every
            environment. Setting CORS_ORIGINS=* in production remains
            possible — it is a decision an operator can make explicitly —
            just no longer the unannounced default.
        """
        raw = self.cors_origins.strip()
        if raw:
            return [o.strip() for o in raw.split(",") if o.strip()]

        if self.api_env == "development":
            return [_DEV_DEFAULT_CORS_ORIGIN]

        raise RuntimeError(
            "CORS_ORIGINS is not set and API_ENV is not 'development'. There "
            "is deliberately no permissive fallback outside development: an "
            "unconfigured CORS policy used to default to '*', which is "
            "silently permissive rather than a deployment asking for it. Set "
            "CORS_ORIGINS to a comma-separated list of allowed origins (or "
            "explicitly to '*' if that is really the intended policy)."
        )


@functools.lru_cache(maxsize=1)
def _development_jwt_secret() -> str:
    """One random key per process, for development with no JWT_SECRET set.

    Cached so that every call within a process returns the SAME key —
    otherwise a token would be signed with one secret and verified with
    another, and every authenticated request would 401.
    """
    import logging
    import secrets

    logging.getLogger("api").warning(
        "JWT_SECRET is unset; signing access tokens with a key generated for "
        "this process. Every restart invalidates outstanding access tokens. "
        "Set JWT_SECRET in .env to stop this.")
    return secrets.token_urlsafe(48)


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached so a request handler can depend on it without re-reading .env
    per request. Tests that mutate the environment must call
    `get_settings.cache_clear()`."""
    return Settings()


def load_dotenv_once() -> None:
    """Copies the resolved settings back into os.environ under the names
    core/ expects.

    core/db.py reads os.environ["PGHOST"] etc. directly. Without this, running
    the API from a shell that never sourced .env would give core/ its
    fallback defaults (localhost/postgres/no password) while the API's own
    Settings object showed the configured values — the two disagreeing
    silently. setdefault, not assignment: a real environment variable (CI,
    docker, systemd) must always win over the repo's .env file, which is the
    same precedence tests/conftest.py uses.
    """
    import os

    settings = get_settings()
    for key, value in (
        ("PGHOST", settings.pghost),
        ("PGPORT", str(settings.pgport)),
        ("PGDATABASE", settings.pgdatabase),
        ("PGUSER", settings.pguser),
        ("PGPASSWORD", settings.pgpassword),
        ("MINIO_ENDPOINT", settings.minio_endpoint),
        ("MINIO_ACCESS_KEY", settings.minio_access_key),
        ("MINIO_SECRET_KEY", settings.minio_secret_key),
        ("MINIO_BUCKET", settings.minio_bucket),
        ("GROQ_API_KEY", settings.groq_api_key),
        ("GROQ_MODEL", settings.groq_model),
    ):
        if value != "":
            os.environ.setdefault(key, value)
