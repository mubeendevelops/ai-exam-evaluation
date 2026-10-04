"""Text similarity for segmentation: cosine of embeddings (the ``Embedder`` port), plus a
standard-library embedder of hashed character trigrams and words.

``TrigramEmbedder`` needs no model and tolerates OCR errors well (a misread letter spoils three
trigrams, not the word's whole vector); it is the fallback when no sentence-embedding model is
installed, and the deterministic embedder of the tests."""

import math
import re
import zlib
from collections.abc import Sequence

from tarn_core.domain.common import EngineRef

type Vector = tuple[float, ...]

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    re.findall(
        r"\S+",
        "the a an and or of to in on for is are was were be by with as at from that this it its "
        "which what who whom how why when where explain write discuss describe briefly short note "
        "notes any five four two three state give list",
    )
)


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


class TrigramEmbedder:
    def __init__(self, dimension: int = 1024) -> None:
        self._dimension = dimension

    @property
    def ref(self) -> EngineRef:
        return EngineRef(name="trigram", version=f"1-{self._dimension}")

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> Sequence[Vector]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> Vector:
        vector = [0.0] * self._dimension
        for word in _WORD.findall(text.lower()):
            if len(word) < 3 or word in _STOP:
                continue
            padded = f" {word} "
            for k in range(len(padded) - 2):
                vector[self._slot(padded[k : k + 3])] += 1.0
            vector[self._slot("w:" + word)] += 2.0
        return tuple(vector)

    def _slot(self, feature: str) -> int:
        return zlib.crc32(feature.encode()) % self._dimension
