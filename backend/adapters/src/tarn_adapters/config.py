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

    # --- OCR (P10) -------------------------------------------------------------------------
    # Engines per content class, comma-separated, in tie-break order. Cloud engines named here
    # are used only when enabled below; engines that are not installed are skipped (logged).
    ocr_engines_print: str = "paddle,tesseract,textract,azure"
    ocr_engines_cursive: str = "trocr,paddle,textract,azure"
    ocr_engines_numeric: str = "trocr,paddle,tesseract,textract,azure"
    # Selector S = w·p̂ + alpha·agreement + beta·lexicon fit; lines whose normalised score is
    # below the threshold are flagged for the teacher. Placeholders until P11.
    ocr_alpha: float = Field(0.5, ge=0)
    ocr_beta: float = Field(0.25, ge=0)
    ocr_flag_threshold: float = Field(0.6, ge=0, le=1)
    ocr_engine_timeout_seconds: float = Field(120.0, gt=0)
    # 180° check of every page by the probe engine (D63/O34).
    ocr_orientation_check: bool = True
    ocr_orientation_margin: float = Field(0.1, ge=0, le=1)
    # TrOCR model (Hugging Face id) and line crops per batch (0 = 8 on CUDA, 4 on the CPU).
    trocr_model: str = "microsoft/trocr-base-handwritten"
    trocr_batch: int = Field(0, ge=0)
    # fp16 | fp32 | auto (time both on the GPU, take the faster: fp32 on a GTX 1650).
    trocr_precision: Literal["auto", "fp16", "fp32"] = "auto"
    # PaddleX models: line detection, recognition (the `paddle` engine), layout for tables and
    # diagrams ("" = text lines only). Mobile detection is ~18× faster than server on the CPU.
    ocr_detection_model: str = "PP-OCRv5_mobile_det"
    ocr_recognition_model: str = "PP-OCRv5_server_rec"
    ocr_layout_model: str = "PP-DocLayout-L"
    # Sentence embeddings for segmentation (P12): a sentence-transformers model, or "trigram"
    # for the model-free embedder of the core (also the fallback when the library is missing).
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Sentence embeddings for scoring (P13): a sentence-transformers model loaded from the local
    # cache only (fetch it with `tarn score models fetch`), or "trigram". Chosen by benchmark
    # (D99); student text never leaves the machine.
    scoring_embedding_model: str = "thenlper/gte-small"
    # Diagram recognition (P14): the trained shape-and-arrow detector, a folder under
    # model_dir/diagram (`tarn diagram train`); missing = diagram criteria go to the teacher.
    diagram_model: str = "shape-detector-v1"
    diagram_threshold: float = Field(0.35, gt=0, lt=1)
    # --- LLM scorer (P19): off by default --------------------------------------------------------
    # The LLM judges a criterion beside the local scorers and sends the answer text (no student
    # name or USN) to Groq. Needs this switch, a key, and, for each college, `tarn tenants llm`.
    # Development additionally needs llm_allow_in_development (like cloud OCR, D78).
    llm_scorer_enabled: bool = False
    llm_allow_in_development: bool = False
    # DEVELOPMENT KEYS: comma-separated free-tier keys, rotated with a rate limit per key.
    # Production needs ONE key of a paid plan: rotating free accounts to dodge rate limits may
    # breach the provider's terms, so production refuses more than one key.
    groq_api_keys: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("GROQ_API_KEYS", "TARN_GROQ_API_KEYS")
    )
    groq_model: str = "openai/gpt-oss-120b"
    groq_api_url: str = "https://api.groq.com/openai/v1/chat/completions"
    llm_timeout_seconds: float = Field(20.0, gt=0)
    llm_max_attempts: int = Field(3, ge=1, le=6)  # requests per criterion (transport + replies)
    llm_requests_per_minute: int = Field(30, ge=1)  # per key (the free tier's order of size)
    llm_max_wait_seconds: float = Field(15.0, ge=0)  # longest wait for a free rate-limit slot
    llm_max_answer_chars: int = Field(6000, ge=200)  # the answer is cut here before it is sent
    # Reasoning models (gpt-oss) spend output tokens on thinking before the JSON: too small a
    # budget gives an empty reply (HTTP 400 json_validate_failed). Only tokens used are billed.
    llm_max_output_tokens: int = Field(1000, ge=100)

    @property
    def groq_keys(self) -> tuple[str, ...]:
        raw = self.groq_api_keys.get_secret_value()
        return tuple(k for k in (part.strip() for part in raw.split(",")) if k)

    # Downloaded model weights (Hugging Face, PaddleX); git-ignored.
    model_dir: Path = Path("../var/models")
    english_words: Path | None = None
    """A word list (one word per line, .txt or .txt.gz); default: the bundled list."""

    # Cloud OCR engines (Textract, Azure Read) stay off in development: student data must not
    # leave the machine (design decision 4). Production needs this flag plus credentials;
    # development additionally needs cloud_ocr_allow_in_development.
    cloud_ocr_enabled: bool = False
    cloud_ocr_allow_in_development: bool = False
    aws_region: str = "ap-south-1"
    """Textract region; credentials come from the standard AWS chain (env, profile, role)."""
    azure_di_endpoint: str = ""
    azure_di_key: SecretStr = SecretStr("")

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

    # --- Upload, page cleaning and the job queue (P9) ------------------------------------------
    # A teacher may have this many booklets queued or in processing at once (design decision 12).
    max_queued_booklets_per_teacher: int = Field(5, ge=1)
    upload_max_bytes: int = Field(100 * 1024 * 1024, ge=1)
    upload_max_pages: int = Field(40, ge=1)  # image files per booklet, and PDF pages
    # Cleaned pages: long side in pixels and file size, at most.
    page_max_edge_px: int = Field(2200, ge=600)
    page_max_bytes: int = Field(800_000, ge=50_000)
    # Quality gate (placeholders until teacher-marked ground truth exists).
    quality_min_sharpness: float = 130.0
    quality_max_glare_share: float = Field(0.06, ge=0, le=1)
    quality_min_page_edge_px: int = 500
    # Job queue: attempts, lease (a worker that stops heartbeating loses its job), retry backoff.
    job_max_attempts: int = Field(3, ge=1)
    job_lease_seconds: float = Field(120.0, gt=0)
    job_backoff_seconds: float = Field(5.0, ge=0)
    worker_poll_seconds: float = Field(2.0, gt=0)

    # --- The review (P15) ----------------------------------------------------------------------
    # A booklet stays locked to the teacher who opened it until closed, or this many minutes
    # after their last write or refresh (design.md "Concurrency").
    booklet_lock_minutes: float = Field(15.0, gt=0)

    @model_validator(mode="after")
    def _llm_in_development_is_explicit(self) -> Self:
        if (
            self.llm_scorer_enabled
            and self.env != "production"
            and not self.llm_allow_in_development
        ):
            raise ValueError(
                "TARN_LLM_SCORER_ENABLED sends answer text to the LLM provider: outside "
                "production it also needs TARN_LLM_ALLOW_IN_DEVELOPMENT=true"
            )
        return self

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
        if self.llm_scorer_enabled and len(self.groq_keys) > 1:
            problems.append(
                "GROQ_API_KEYS holds several keys: rotating free accounts is for development "
                "only (provider terms); production uses one paid-plan key"
            )
        if problems:
            raise ValueError("unsafe production settings: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
