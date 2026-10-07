"""Server-side secrets: the password pepper and the access-token signing key.

Development reads them from settings (``.env``); production reads Secret Manager. Both are
bytes and never logged."""

from typing import Protocol

from tarn_adapters.config import Settings

PEPPER = "password-pepper"
PEPPER_PREVIOUS = "password-pepper-previous"
"""Earlier peppers still accepted during a rotation (O13): optional; one per line or comma."""
SIGNING_KEY = "token-signing-key"
SMTP_PASSWORD = "smtp-password"  # noqa: S105  (a secret's name in Secret Manager)
GROQ_KEY = "groq-api-key"
"""The LLM provider's key (P19): one paid-plan key in production (O95)."""


class SecretSource(Protocol):
    def get(self, name: str) -> bytes: ...


class SettingsSecrets:
    def __init__(self, settings: Settings) -> None:
        self._values = {
            PEPPER: settings.password_pepper.get_secret_value().encode(),
            PEPPER_PREVIOUS: settings.password_pepper_previous.get_secret_value().encode(),
            SIGNING_KEY: settings.token_signing_key.get_secret_value().encode(),
        }

    def get(self, name: str) -> bytes:
        return self._values[name]


class SecretManagerClient(Protocol):
    def access_secret_version(self, request: dict[str, object]) -> object: ...


class GcpSecrets:
    """Secret Manager: secret ``tarn-<name>``, latest version, in ``project``."""

    def __init__(self, project: str, client: SecretManagerClient | None = None) -> None:
        if client is None:
            from google.cloud import secretmanager  # lazily: development never needs it

            client = secretmanager.SecretManagerServiceClient()
        self._client = client
        self._project = project
        self._cache: dict[str, bytes] = {}

    def get(self, name: str) -> bytes:
        if name not in self._cache:
            path = f"projects/{self._project}/secrets/tarn-{name}/versions/latest"
            response = self._client.access_secret_version({"name": path})
            self._cache[name] = bytes(response.payload.data)  # type: ignore[attr-defined]
        return self._cache[name]


def previous_peppers(secrets: SecretSource) -> tuple[bytes, ...]:
    """The earlier peppers of a rotation, or none. In Secret Manager the secret
    ``tarn-password-pepper-previous`` may simply not exist."""
    try:
        raw = secrets.get(PEPPER_PREVIOUS)
    except KeyError:
        return ()
    except Exception as exc:
        if type(exc).__name__ == "NotFound":  # google.api_core.exceptions.NotFound
            return ()
        raise
    parts = raw.replace(b",", b"\n").splitlines()
    return tuple(p.strip() for p in parts if p.strip())


def secret_source(settings: Settings) -> SecretSource:
    if settings.secrets_backend == "gcp":
        return GcpSecrets(settings.gcp_project)
    return SettingsSecrets(settings)
