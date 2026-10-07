"""Builds the LLM scorer from the settings, or nothing: it is off unless switched on."""

import structlog

from tarn_adapters.config import Settings
from tarn_adapters.llm.groq import GroqClient, KeyPool
from tarn_adapters.llm.scorer import LlmScorer

log = structlog.get_logger("tarn_adapters.llm")


class LlmConfigError(ValueError):
    """The LLM scorer is switched on but cannot run (no key)."""


def build_llm_scorer(settings: Settings) -> tuple[LlmScorer, GroqClient] | None:
    """None while ``TARN_LLM_SCORER_ENABLED`` is false (the default). ``Settings`` has already
    refused the unsafe combinations (development without the allowance, several keys in
    production)."""
    if not settings.llm_scorer_enabled:
        return None
    keys = settings.groq_keys
    if not keys:
        raise LlmConfigError("TARN_LLM_SCORER_ENABLED is true but GROQ_API_KEYS is empty")
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
