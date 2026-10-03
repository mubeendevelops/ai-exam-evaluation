"""Envelope encryption adapters: AES-256-GCM with data keys, and two key managers that wrap
the data keys: a local file key for development and Google Cloud KMS for production.

KMS key references are provider-neutral strings:
- ``local:<name>``: a 256-bit key in ``<key dir>/<name>.key`` (created on first use, mode 600).
- ``gcp-kms:projects/<p>/locations/<l>/keyRings/<r>/cryptoKeys/<k>``: a Cloud KMS key."""

import os
import re
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tarn_core.ports.identity import DataKey

NONCE_BYTES = 12
DATA_KEY_BYTES = 32


class AesGcmCipher:
    """``nonce (12 bytes) || ciphertext+tag``; a fresh random nonce per seal."""

    def seal(self, key: bytes, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)

    def open(self, key: bytes, sealed: bytes, aad: bytes) -> bytes:
        if len(sealed) < NONCE_BYTES + 16:
            raise ValueError("sealed value is too short")
        try:
            return AESGCM(key).decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], aad)
        except InvalidTag:
            raise ValueError("authentication failed: wrong key, context or altered data") from None


_LOCAL_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")


class LocalKeyManager:
    """Development only: the "KMS key" is a file on this machine. Never use in production
    (``Settings`` refuses it there)."""

    PREFIX = "local:"

    def __init__(self, key_dir: Path) -> None:
        self._dir = key_dir
        self._cipher = AesGcmCipher()

    def _master(self, key_ref: str) -> bytes:
        if not key_ref.startswith(self.PREFIX):
            raise ValueError("not a local key reference")
        name = key_ref.removeprefix(self.PREFIX)
        if not _LOCAL_NAME.fullmatch(name):
            raise ValueError("bad local key name")
        path = self._dir / f"{name}.key"
        if not path.exists():
            self._dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(os.urandom(DATA_KEY_BYTES))
        key = path.read_bytes()
        if len(key) != DATA_KEY_BYTES:
            raise ValueError("local key file has the wrong size")
        return key

    def generate_data_key(self, key_ref: str, context: bytes) -> DataKey:
        plaintext = os.urandom(DATA_KEY_BYTES)
        return DataKey(
            plaintext=plaintext,
            wrapped=self._cipher.seal(self._master(key_ref), plaintext, context),
        )

    def unwrap(self, key_ref: str, wrapped: bytes, context: bytes) -> bytes:
        return self._cipher.open(self._master(key_ref), wrapped, context)


class KmsClient(Protocol):
    """The two calls of ``google.cloud.kms.KeyManagementServiceClient`` we use."""

    def encrypt(self, request: dict[str, object]) -> object: ...

    def decrypt(self, request: dict[str, object]) -> object: ...


class GcpKmsKeyManager:
    """Cloud KMS (asia-south1 in production). The data key is generated locally and wrapped
    by ``encrypt`` with the tenant context as additional authenticated data."""

    PREFIX = "gcp-kms:"

    def __init__(self, client: KmsClient | None = None) -> None:
        if client is None:
            from google.cloud import kms  # imported lazily: development never needs it

            client = kms.KeyManagementServiceClient()
        self._client = client

    def _name(self, key_ref: str) -> str:
        if not key_ref.startswith(self.PREFIX):
            raise ValueError("not a Cloud KMS key reference")
        return key_ref.removeprefix(self.PREFIX)

    def generate_data_key(self, key_ref: str, context: bytes) -> DataKey:
        plaintext = os.urandom(DATA_KEY_BYTES)
        response = self._client.encrypt(
            {
                "name": self._name(key_ref),
                "plaintext": plaintext,
                "additional_authenticated_data": context,
            }
        )
        return DataKey(plaintext=plaintext, wrapped=bytes(response.ciphertext))  # type: ignore[attr-defined]

    def unwrap(self, key_ref: str, wrapped: bytes, context: bytes) -> bytes:
        try:
            response = self._client.decrypt(
                {
                    "name": self._name(key_ref),
                    "ciphertext": wrapped,
                    "additional_authenticated_data": context,
                }
            )
        except Exception as exc:  # google.api_core errors: wrong key, context or data
            raise ValueError(f"Cloud KMS refused to unwrap ({type(exc).__name__})") from exc
        return bytes(response.plaintext)  # type: ignore[attr-defined]


class RoutingKeyManager:
    """Picks the key manager from the reference's prefix, so tenants created under one
    backend still open after the default moves to another."""

    def __init__(self, *, local: LocalKeyManager | None, gcp: GcpKmsKeyManager | None) -> None:
        self._local = local
        self._gcp = gcp

    def _for(self, key_ref: str) -> LocalKeyManager | GcpKmsKeyManager:
        if key_ref.startswith(LocalKeyManager.PREFIX) and self._local is not None:
            return self._local
        if key_ref.startswith(GcpKmsKeyManager.PREFIX) and self._gcp is not None:
            return self._gcp
        raise ValueError("no key manager is configured for this key reference")

    def generate_data_key(self, key_ref: str, context: bytes) -> DataKey:
        return self._for(key_ref).generate_data_key(key_ref, context)

    def unwrap(self, key_ref: str, wrapped: bytes, context: bytes) -> bytes:
        return self._for(key_ref).unwrap(key_ref, wrapped, context)
