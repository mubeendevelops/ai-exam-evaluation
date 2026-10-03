"""Runtime settings, read from ``TARN_*`` environment variables (and ``.env`` in development)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Development defaults that must never reach production (checked below).
DEV_PEPPER = "dev-only-pepper-not-for-production"
DEV_SIGNING_KEY = "dev-only-signing-key-not-for-production"


class Settings(BaseSettings):
    """Every variable is listed, with its default, in ``.env.example``."""

    # Later files win; missing files are ignored. ``../.env`` is the repo root when the
    # process runs from ``backend/`` (``make`` targets do), ``.env`` when run from the root.
    model_config = SettingsConfigDict(
        env_prefix="TARN_", env_file=("../.env", ".env"), extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Owner role: runs migrations and owns the tables. Never used to serve requests.
    database_url: str = "postgresql://tarn:tarn_dev_password@localhost:5432/tarn"
    # Application role (tarn_app): no superuser, no BYPASSRLS, owns nothing; RLS applies to it.
    app_database_url: str = "postgresql://tarn_app:tarn_app_dev_password@localhost:5432/tarn"

    # S3-compatible object store: MinIO in development, Cloud Storage in production.
    blob_endpoint: str = "localhost:9000"
    blob_access_key: str = "tarn_dev_minio"
    blob_secret_key: SecretStr = SecretStr("tarn_dev_minio_password")
    blob_secure: bool = False
    blob_bucket: str = "tarn"

    # "auto" uses CUDA when the GPU and torch are present, otherwise the CPU.
    device: Literal["auto", "cpu", "cuda"] = "auto"

    # Cloud OCR engines (Textract, Azure Read, Document AI) stay off in development.
    cloud_ocr_enabled: bool = False

    # --- Identity store (P4): a separate database with its own roles --------------------
    # Owner role of the identity database: migrations only.
    identity_database_url: str = "postgresql://tarn:tarn_dev_password@localhost:5432/tarn_identity"
    # Application role tarn_auth: no superuser, no BYPASSRLS; RLS applies to it.
    identity_app_database_url: str = (
        "postgresql://tarn_auth:tarn_auth_dev_password@localhost:5432/tarn_identity"
    )

    # Envelope encryption. Key references: "local:<name>" (development file key under
    # local_key_dir) or "gcp-kms:projects/<p>/locations/<l>/keyRings/<r>/cryptoKeys/<k>".
    kms_key_ref: str = "local:tarn-dev"
    local_key_dir: Path = Path("../var/keys")

    # Secrets: "settings" reads the two values below (development); "gcp" reads Secret
    # Manager secrets tarn-password-pepper and tarn-token-signing-key in gcp_project.
    secrets_backend: Literal["settings", "gcp"] = "settings"
    gcp_project: str = ""
    password_pepper: SecretStr = SecretStr(DEV_PEPPER)
    token_signing_key: SecretStr = SecretStr(DEV_SIGNING_KEY)

    # Sessions: short access tokens; the refresh cookie lasts a working day, or 30 days with
    # "Remember session".
    access_token_minutes: int = 15
    session_hours: int = 12
    remember_session_days: int = 30
    # The public web address, for links in emails.
    public_url: str = "http://localhost:5173"
    # Development only; production needs a real mail adapter (P20).
    mailer: Literal["console"] = "console"
    # A Tarn operator approves new tenants (`tarn tenants approve`). Also read without the
    # TARN_ prefix, as the build plan names it.
    tenant_signup_requires_approval: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "TARN_TENANT_SIGNUP_REQUIRES_APPROVAL", "TENANT_SIGNUP_REQUIRES_APPROVAL"
        ),
    )

    # Encrypted identity backups, written by the worker every interval (0 = off).
    identity_backup_dir: Path = Path("../var/backups/identity")
    identity_backup_interval_hours: float = 24.0
    identity_backup_keep: int = 14

    # The worker answers GET /health on this port (0 = off); the API reports it in /api/v1/health
    # by calling ``worker_health_url`` (empty = not configured, shown as "unknown").
    worker_health_host: str = "127.0.0.1"
    worker_health_port: int = 8001
    worker_health_url: str = ""

    @model_validator(mode="after")
    def _production_is_not_development(self) -> Self:
        if self.env != "production":
            return self
        problems = []
        if self.kms_key_ref.startswith("local:"):
            problems.append("TARN_KMS_KEY_REF must name a cloud KMS key")
        if self.secrets_backend == "settings" and (
            self.password_pepper.get_secret_value() == DEV_PEPPER
            or self.token_signing_key.get_secret_value() == DEV_SIGNING_KEY
        ):
            problems.append("the development pepper and signing key are not allowed")
        if problems:
            raise ValueError("unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
