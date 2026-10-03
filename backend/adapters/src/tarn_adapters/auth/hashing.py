"""argon2id password and recovery-code hashing with a server-side pepper.

argon2-cffi has no "secret" input, so the pepper is applied first: the hashed value is
HMAC-SHA256(pepper, secret), base64-encoded. The stored string is a standard
``$argon2id$v=19$m=...,t=...,p=...$salt$hash`` and carries no trace of the pepper, so a
leaked identity database cannot be attacked offline without the secret store's pepper."""

import base64
import hashlib
import hmac

from argon2 import PasswordHasher as _Argon2
from argon2 import Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from tarn_core.domain.identity import HashParams


def _hasher(params: HashParams) -> _Argon2:
    return _Argon2(
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    )


class Argon2Hasher:
    def __init__(self, pepper: bytes) -> None:
        if len(pepper) < 16:
            raise ValueError("the pepper must be at least 16 bytes")
        self._pepper = pepper

    def _peppered(self, secret: str) -> str:
        digest = hmac.new(self._pepper, secret.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def hash(self, secret: str, params: HashParams) -> str:
        return _hasher(params).hash(self._peppered(secret))

    def verify(self, encoded: str, secret: str) -> bool:
        try:
            # Verification reads the parameters from the encoded hash itself.
            return _Argon2().verify(encoded, self._peppered(secret))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, encoded: str, params: HashParams) -> bool:
        try:
            return _hasher(params).check_needs_rehash(encoded)
        except InvalidHashError:
            return True
