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
    #: Comma-separated origins for CORS. "*" in development only.
    cors_origins: str = "*"
    #: GET /api/v1/_debug/whoami is a throwaway plumbing probe (see
    #: api/routers/debug.py). It is registered ONLY when this is true, and
    #: this defaults to false whenever api_env != "development".
    enable_debug_endpoints: bool | None = None

    @property
    def debug_endpoints_enabled(self) -> bool:
        if self.enable_debug_endpoints is not None:
            return self.enable_debug_endpoints
        return self.api_env == "development"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


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
