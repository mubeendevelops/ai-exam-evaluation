"""Every test starts with the LLM scorer off, whatever the developer's ``.env`` says: a real
``.env`` with Groq keys (P19) must not change what the tests see, and no test may reach the
provider. Tests that need the LLM settings pass them explicitly or set them themselves."""

import pytest


@pytest.fixture(autouse=True)
def _llm_scorer_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARN_LLM_SCORER_ENABLED", "false")
    monkeypatch.setenv("TARN_LLM_ALLOW_IN_DEVELOPMENT", "false")
    monkeypatch.setenv("GROQ_API_KEYS", "")
    monkeypatch.delenv("TARN_GROQ_API_KEYS", raising=False)
