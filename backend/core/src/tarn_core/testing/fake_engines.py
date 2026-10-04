"""Deterministic stand-ins for the ML engines."""

import hashlib
import math
from collections.abc import Sequence
from decimal import Decimal

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.content import CriterionType
from tarn_core.domain.diagram import DiagramGraph
from tarn_core.domain.ocr import ContentClass, EngineCalibration
from tarn_core.domain.scoring import CriterionScore
from tarn_core.errors import InvariantError
from tarn_core.ports.engines import DetectedRegion, ScoringInput


class ScriptedLayoutDetector:
    def __init__(self, regions: Sequence[DetectedRegion]) -> None:
        self._regions = tuple(regions)
        self.ref = EngineRef(name="scripted-layout", version="1")

    def detect(self, image: bytes) -> Sequence[DetectedRegion]:
        return self._regions


class ScriptedOcrEngine:
    """Returns ``texts[i]`` for the i-th line box (cycling), at a fixed confidence (or one per
    line from ``confidences``). ``fail`` makes every read raise it; ``turned_confidence`` is
    the confidence for an image the fake transform turned by 180° (an upside-down read)."""

    def __init__(
        self,
        name: str,
        texts: Sequence[str],
        confidence: float = 0.9,
        *,
        confidences: Sequence[float] | None = None,
        fail: Exception | None = None,
        turned_confidence: float | None = None,
        version: str = "1",
    ) -> None:
        self._texts = tuple(texts)
        self._confidence = confidence
        self._confidences = tuple(confidences) if confidences is not None else None
        self._fail = fail
        self._turned_confidence = turned_confidence
        self.ref = EngineRef(name=name, version=version)
        self.calls = 0

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        self.calls += 1
        if self._fail is not None:
            raise self._fail
        turned = image.startswith(TURNED)
        readings = []
        for i, box in enumerate(lines):
            confidence = (
                self._confidences[i % len(self._confidences)]
                if self._confidences is not None
                else self._confidence
            )
            if turned and self._turned_confidence is not None:
                confidence = self._turned_confidence
            readings.append(
                LineReading(
                    engine=self.ref,
                    text=self._texts[i % len(self._texts)],
                    box=box,
                    confidence=confidence,
                )
            )
        return readings


class PageOcrEngine:
    """A cloud-like engine: ignores the given lines and returns its own readings (lines or
    words with their own boxes) for every page."""

    def __init__(self, name: str, readings: Sequence[tuple[str, Box, float]]) -> None:
        self.ref = EngineRef(name=name, version="1")
        self._readings = tuple(readings)

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        return [
            LineReading(engine=self.ref, text=text, box=box, confidence=confidence)
            for text, box, confidence in self._readings
        ]


TURNED = b"turned180:"


class FakePageTransform:
    """Marks turned images instead of turning pixels: turning by 180° twice gives the original
    back; downscaling changes nothing."""

    def __init__(self) -> None:
        self.rotations: list[int] = []

    def rotate(self, image: bytes, degrees: int) -> bytes:
        self.rotations.append(degrees)
        if degrees != 180:
            return f"rot{degrees}:".encode() + image
        if image.startswith(TURNED):
            return image[len(TURNED) :]
        return TURNED + image

    def downscale(self, image: bytes, max_edge: int) -> bytes:
        return image

    def size(self, image: bytes) -> tuple[int, int]:
        return (10_000, 10_000)


class SetWordList:
    def __init__(self, words: Sequence[str] = ()) -> None:
        self._words = frozenset(w.casefold() for w in words)

    def contains(self, word: str) -> bool:
        return word.casefold() in self._words


class MemoryCalibrationStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, ContentClass], list[EngineCalibration]] = {}

    def latest(self) -> Sequence[EngineCalibration]:
        return [versions[-1] for versions in self._items.values()]

    def get(self, engine: str, content_class: ContentClass) -> EngineCalibration | None:
        versions = self._items.get((engine, content_class))
        return versions[-1] if versions else None

    def save(self, calibration: EngineCalibration) -> None:
        versions = self._items.setdefault((calibration.engine, calibration.content_class), [])
        if calibration.version != len(versions) + 1:
            raise InvariantError(
                f"calibration version {calibration.version} is not the next ({len(versions) + 1})"
            )
        versions.append(calibration)


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
