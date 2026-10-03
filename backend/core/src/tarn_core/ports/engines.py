"""Ports for the machine-learning engines. Each reports its own name and version so every
result can record what produced it."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from tarn_core.domain.booklet import LineReading, RegionKind
from tarn_core.domain.common import Box, ContentRef, EngineRef
from tarn_core.domain.content import CriterionType, RubricCriterion
from tarn_core.domain.diagram import DiagramGraph
from tarn_core.domain.scoring import CriterionScore


@dataclass(frozen=True, slots=True, kw_only=True)
class DetectedRegion:
    kind: RegionKind
    box: Box


class LayoutDetector(Protocol):
    @property
    def ref(self) -> EngineRef: ...

    def detect(self, image: bytes) -> Sequence[DetectedRegion]: ...


class OcrEngine(Protocol):
    """Reads text lines (design.md "OCR framework"). Cloud engines may ignore ``lines`` and
    return their own; readings are matched to our lines by box overlap."""

    @property
    def ref(self) -> EngineRef: ...

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]: ...


class Embedder(Protocol):
    @property
    def ref(self) -> EngineRef: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringInput:
    """What a scorer sees for one criterion of one answer. No college or student identity:
    scorers judge content only."""

    criterion: RubricCriterion
    answer_text: str
    diagrams: tuple[DiagramGraph, ...] = ()
    reference_text: str = ""
    glossary: tuple[str, ...] = ()
    extra_content: Mapping[str, ContentRef] = field(default_factory=dict)


class Scorer(Protocol):
    """Scores one criterion; non-LLM scorers first, an LLM scorer later and off by default."""

    @property
    def ref(self) -> EngineRef: ...

    def supports(self, criterion_type: CriterionType) -> bool: ...

    def score(self, item: ScoringInput) -> CriterionScore: ...


class DiagramRecognizer(Protocol):
    @property
    def ref(self) -> EngineRef: ...

    def recognize(self, image: bytes) -> DiagramGraph: ...
