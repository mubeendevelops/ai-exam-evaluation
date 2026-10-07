"""Builds the LLM scorer from the settings, or nothing: it is off unless switched on."""

import structlog

from tarn_adapters.auth.secrets import GROQ_KEY, SecretSource, secret_source
from tarn_adapters.config import Settings
from tarn_adapters.llm.groq import GroqClient, KeyPool
from tarn_adapters.llm.scorer import LlmScorer

log = structlog.get_logger("tarn_adapters.llm")


class LlmConfigError(ValueError):
    """The LLM scorer is switched on but cannot run (no key)."""


def _secret_manager_keys(settings: Settings, secrets: SecretSource | None) -> tuple[str, ...]:
    """The key kept in Secret Manager as ``tarn-groq-api-key`` (the ``gcp`` secrets backend)."""
    raw = (secrets or secret_source(settings)).get(GROQ_KEY).decode()
    keys = tuple(k for k in (part.strip() for part in raw.split(",")) if k)
    if settings.env == "production" and len(keys) > 1:
        raise LlmConfigError(
            "the Secret Manager key holds several keys: production uses one paid-plan key"
        )
    return keys


def build_llm_scorer(
    settings: Settings, *, secrets: SecretSource | None = None
) -> tuple[LlmScorer, GroqClient] | None:
    """None while ``TARN_LLM_SCORER_ENABLED`` is false (the default). ``Settings`` has already
    refused the unsafe combinations (development without the allowance, several keys in
    production). The key comes from ``GROQ_API_KEYS``, or, with ``TARN_SECRETS_BACKEND=gcp``
    and none set, from Secret Manager."""
    if not settings.llm_scorer_enabled:
        return None
    keys = settings.groq_keys
    if not keys and settings.secrets_backend == "gcp":
        keys = _secret_manager_keys(settings, secrets)
    if not keys:
        raise LlmConfigError(
            "TARN_LLM_SCORER_ENABLED is true but no key is set "
            "(GROQ_API_KEYS, or the tarn-groq-api-key secret)"
        )
    pool = KeyPool(
        keys,
        per_minute=settings.llm_requests_per_minute,
        max_wait=settings.llm_max_wait_seconds,
    )
    client = GroqClient(
        pool,
        model=settings.groq_model,
        url=settings.groq_api_url,
        timeout=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
        max_tokens=settings.llm_max_output_tokens,
    )
    scorer = LlmScorer(
        client, model=settings.groq_model, max_answer_chars=settings.llm_max_answer_chars
    )
    log.info(
        "llm.enabled",
        model=settings.groq_model,
        keys=len(keys),
        rotation=len(keys) > 1,
        env=settings.env,
    )
    return scorer, client
