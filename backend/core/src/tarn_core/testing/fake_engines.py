"""Deterministic stand-ins for the ML engines."""

import hashlib
import math
from collections.abc import Sequence
from decimal import Decimal

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.content import CriterionType
from tarn_core.domain.diagram import DiagramGraph
from tarn_core.domain.scoring import CriterionScore
from tarn_core.ports.engines import DetectedRegion, ScoringInput


class ScriptedLayoutDetector:
    def __init__(self, regions: Sequence[DetectedRegion]) -> None:
        self._regions = tuple(regions)
        self.ref = EngineRef(name="scripted-layout", version="1")

    def detect(self, image: bytes) -> Sequence[DetectedRegion]:
        return self._regions


class ScriptedOcrEngine:
    """Returns ``texts[i]`` for the i-th line box (cycling), at a fixed confidence."""

    def __init__(self, name: str, texts: Sequence[str], confidence: float = 0.9) -> None:
        self._texts = tuple(texts)
        self._confidence = confidence
        self.ref = EngineRef(name=name, version="1")

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        return [
            LineReading(
                engine=self.ref,
                text=self._texts[i % len(self._texts)],
                box=box,
                confidence=self._confidence,
            )
            for i, box in enumerate(lines)
        ]


class HashEmbedder:
    """Unit vectors derived from SHA-256: equal texts embed equally, nothing else is meaningful."""

    def __init__(self, dimension: int = 8) -> None:
        self.dimension = dimension
        self.ref = EngineRef(name="hash-embedder", version="1")

    def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        vectors = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            raw = [digest[i % len(digest)] - 127.5 for i in range(self.dimension)]
            norm = math.sqrt(sum(x * x for x in raw)) or 1.0
            vectors.append(tuple(x / norm for x in raw))
        return vectors


class FixedCreditScorer:
    """Gives every supported criterion the same credit."""

    def __init__(
        self,
        credit: Decimal = Decimal(1),
        types: frozenset[CriterionType] = frozenset(CriterionType),
        name: str = "fixed-credit",
    ) -> None:
        self._credit = credit
        self._types = types
        self.ref = EngineRef(name=name, version="1")

    def supports(self, criterion_type: CriterionType) -> bool:
        return criterion_type in self._types

    def score(self, item: ScoringInput) -> CriterionScore:
        return CriterionScore(
            criterion=item.criterion.ref,
            weight=item.criterion.weight,
            credit=self._credit,
            scorer=self.ref,
        )


class ScriptedDiagramRecognizer:
    def __init__(self, graph: DiagramGraph) -> None:
        self._graph = graph
        self.ref = EngineRef(name="scripted-diagram", version="1")

    def recognize(self, image: bytes) -> DiagramGraph:
        return self._graph
