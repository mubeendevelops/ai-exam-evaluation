"""argon2id password and recovery-code hashing with a server-side pepper.

argon2-cffi has no "secret" input, so the pepper is applied first: the hashed value is
HMAC-SHA256(pepper, secret), base64-encoded. The stored string is a standard
``$argon2id$v=19$m=...,t=...,p=...$salt$hash`` and carries no trace of the pepper, so a
leaked identity database cannot be attacked offline without the secret store's pepper.

Rotation (O13, P21) keeps that format (the boss's schema pins ``$argon2id$...``): the hasher
holds the current pepper and the earlier ones still accepted. ``verify`` tries the current
pepper, then each earlier one; ``pepper_is_current`` tells sign-in to hash again with the current
pepper. Once every account has signed in (or after a set period), the earlier pepper is removed
and the accounts left on it reset their passwords. Recovery codes are never re-hashed: issuing a
new set moves them to the current pepper."""

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
    def __init__(self, pepper: bytes, previous: tuple[bytes, ...] = ()) -> None:
        if len(pepper) < 16 or any(len(p) < 16 for p in previous):
            raise ValueError("the pepper must be at least 16 bytes")
        self._pepper = pepper
        self._previous = tuple(p for p in previous if p != pepper)

    @staticmethod
    def _peppered(pepper: bytes, secret: str) -> str:
        digest = hmac.new(pepper, secret.encode(), hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    def hash(self, secret: str, params: HashParams) -> str:
        return _hasher(params).hash(self._peppered(self._pepper, secret))

    @staticmethod
    def _verify_with(pepper: bytes, encoded: str, secret: str) -> bool:
        try:
            # Verification reads the parameters from the encoded hash itself.
            return _Argon2().verify(encoded, Argon2Hasher._peppered(pepper, secret))
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def verify(self, encoded: str, secret: str) -> bool:
        return any(self._verify_with(p, encoded, secret) for p in (self._pepper, *self._previous))

    def pepper_is_current(self, encoded: str, secret: str) -> bool:
        if not self._previous:
            return True
        return self._verify_with(self._pepper, encoded, secret)

    def needs_rehash(self, encoded: str, params: HashParams) -> bool:
        try:
            return _hasher(params).check_needs_rehash(encoded)
        except InvalidHashError:
            return True
