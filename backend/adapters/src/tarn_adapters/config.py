"""Runtime settings, read from ``TARN_*`` environment variables (and ``.env`` in development)."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Every variable is listed, with its default, in ``.env.example``."""

    # Later files win; missing files are ignored. ``../.env`` is the repo root when the
    # process runs from ``backend/`` (``make`` targets do), ``.env`` when run from the root.
    model_config = SettingsConfigDict(
        env_prefix="TARN_", env_file=("../.env", ".env"), extra="ignore"
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    database_url: str = "postgresql://tarn:tarn_dev_password@localhost:5432/tarn"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
