"""Envelope encryption with a tenant's data key.

The data key is generated once per tenant, wrapped by the tenant's KMS key
(``TenantRecord.kms_key_ref``) and stored only wrapped. Encrypted fields have the
provider-neutral form ``enc:v1:<data key version>:<base64 of nonce + ciphertext>``."""

import base64

from tarn_core.domain.identity import TenantRecord
from tarn_core.errors import InvariantError
from tarn_core.ports.identity import Cipher, KeyManager

PREFIX = "enc:v1:"


def tenant_context(tenant: TenantRecord) -> bytes:
    """Additional authenticated data bound to the wrapped data key."""
    return context_for(str(tenant.college_id))


def context_for(tenant_id: str) -> bytes:
    return f"tarn-tenant:{tenant_id}".encode()


class TenantVault:
    """Seals and opens values with one tenant's data key, unwrapped once per instance."""

    def __init__(
        self,
        *,
        key_ref: str,
        wrapped_key: bytes,
        key_version: int,
        context: bytes,
        keys: KeyManager,
        cipher: Cipher,
    ) -> None:
        self._key_ref = key_ref
        self._wrapped = wrapped_key
        self._version = key_version
        self._context = context
        self._keys = keys
        self._cipher = cipher
        self._key: bytes | None = None

    @classmethod
    def for_tenant(cls, tenant: TenantRecord, keys: KeyManager, cipher: Cipher) -> "TenantVault":
        return cls(
            key_ref=tenant.kms_key_ref,
            wrapped_key=tenant.wrapped_data_key,
            key_version=tenant.data_key_version,
            context=tenant_context(tenant),
            keys=keys,
            cipher=cipher,
        )

    def _data_key(self) -> bytes:
        if self._key is None:
            self._key = self._keys.unwrap(self._key_ref, self._wrapped, self._context)
        return self._key

    def seal_bytes(self, plaintext: bytes, aad: str) -> bytes:
        return self._cipher.seal(self._data_key(), plaintext, aad.encode())

    def open_bytes(self, sealed: bytes, aad: str) -> bytes:
        return self._cipher.open(self._data_key(), sealed, aad.encode())

    def seal(self, plaintext: bytes, aad: str) -> str:
        body = base64.b64encode(self.seal_bytes(plaintext, aad)).decode()
        return f"{PREFIX}{self._version}:{body}"

    def open(self, value: str, aad: str) -> bytes:
        if not value.startswith(PREFIX):
            raise InvariantError("not an enc:v1 value")
        version, _, body = value.removeprefix(PREFIX).partition(":")
        if version != str(self._version):
            raise InvariantError("sealed with another data key version")
        return self.open_bytes(base64.b64decode(body), aad)
