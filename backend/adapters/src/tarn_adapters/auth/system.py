"""Operating-system randomness."""

import secrets


class SystemRandom:
    def token_bytes(self, n: int) -> bytes:
        return secrets.token_bytes(n)
