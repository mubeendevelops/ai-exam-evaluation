"""The common/breached password list and its Bloom filter for the browser.

The server checks new passwords against the full list (``CommonPasswordList``). The browser
gets a Bloom filter built from the same list as an early hint only; the server decides.

Bloom filter format (``TBF1``), so the web client (P5) can query it:
- bytes 0-3 ``b"TBF1"``; 4-7 number of bits ``m`` (uint32, big-endian); 8 number of hashes ``k``;
  then ``ceil(m / 8)`` bytes of bits, bit ``i`` = byte ``i // 8``, mask ``1 << (i % 8)``.
- For a password: ``d = SHA-256(lower-case UTF-8)``; ``h1`` = uint32 BE of ``d[0:4]``,
  ``h2`` = uint32 BE of ``d[4:8]`` OR 1; positions ``(h1 + i * h2) mod m`` for ``i < k``.
"""

import gzip
import hashlib
import math
from collections.abc import Iterable, Iterator
from functools import lru_cache
from importlib import resources

MAGIC = b"TBF1"
DEFAULT_FALSE_POSITIVE_RATE = 0.001


@lru_cache(maxsize=1)
def _bundled() -> frozenset[str]:
    data = resources.files("tarn_adapters.auth").joinpath("data/common-passwords.txt.gz")
    text = gzip.decompress(data.read_bytes()).decode()
    return frozenset(line for line in text.splitlines() if line)


class CommonPasswordList:
    def __init__(self, words: Iterable[str] | None = None) -> None:
        self._words = _bundled() if words is None else frozenset(w.lower() for w in words)

    def __contains__(self, password: object) -> bool:
        return isinstance(password, str) and password.lower() in self._words

    def __len__(self) -> int:
        return len(self._words)

    def __iter__(self) -> Iterator[str]:
        return iter(self._words)


def _positions(word: str, m: int, k: int) -> list[int]:
    d = hashlib.sha256(word.lower().encode()).digest()
    h1 = int.from_bytes(d[0:4], "big")
    h2 = int.from_bytes(d[4:8], "big") | 1
    return [(h1 + i * h2) % m for i in range(k)]


class BloomFilter:
    def __init__(self, m: int, k: int, bits: bytearray | None = None) -> None:
        if m < 8 or k < 1:
            raise ValueError("a Bloom filter needs at least 8 bits and 1 hash")
        self.m = m
        self.k = k
        self.bits = bits if bits is not None else bytearray((m + 7) // 8)

    @classmethod
    def sized_for(cls, n: int, p: float = DEFAULT_FALSE_POSITIVE_RATE) -> "BloomFilter":
        """The optimal size for ``n`` items at false-positive rate ``p``."""
        m = math.ceil(-n * math.log(p) / math.log(2) ** 2)
        k = max(1, round(m / n * math.log(2)))
        return cls(m, k)

    @classmethod
    def build(cls, words: Iterable[str], p: float = DEFAULT_FALSE_POSITIVE_RATE) -> "BloomFilter":
        items = list(words)
        bloom = cls.sized_for(max(1, len(items)), p)
        for word in items:
            bloom.add(word)
        return bloom

    def add(self, word: str) -> None:
        for i in _positions(word, self.m, self.k):
            self.bits[i // 8] |= 1 << (i % 8)

    def __contains__(self, word: object) -> bool:
        if not isinstance(word, str):
            return False
        return all(self.bits[i // 8] & (1 << (i % 8)) for i in _positions(word, self.m, self.k))

    def to_bytes(self) -> bytes:
        return MAGIC + self.m.to_bytes(4, "big") + bytes([self.k]) + bytes(self.bits)

    @classmethod
    def from_bytes(cls, data: bytes) -> "BloomFilter":
        if data[:4] != MAGIC:
            raise ValueError("not a TBF1 Bloom filter")
        m = int.from_bytes(data[4:8], "big")
        k = data[8]
        bits = bytearray(data[9:])
        if len(bits) != (m + 7) // 8:
            raise ValueError("truncated Bloom filter")
        return cls(m, k, bits)


@lru_cache(maxsize=1)
def bundled_bloom_bytes() -> bytes:
    return BloomFilter.build(_bundled()).to_bytes()
