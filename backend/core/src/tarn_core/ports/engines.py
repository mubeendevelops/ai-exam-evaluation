"""Ports for the machine-learning engines. Each reports its own name and version so every
result can record what produced it."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from tarn_core.domain.booklet import LineReading, RegionKind
from tarn_core.domain.common import Box, ContentRef, EngineRef
from tarn_core.domain.content import CriterionType, ReferenceDiagram, RubricCriterion
from tarn_core.domain.diagram import AnswerDiagram, DiagramDetection
from tarn_core.domain.ocr import ContentClass, EngineCalibration
from tarn_core.domain.scoring import CriterionScore, LlmUsage, ScoringCalibration, SecondOpinion
from tarn_core.errors import InvariantError
from tarn_core.ids import ReferenceDiagramId


@dataclass(frozen=True, slots=True, kw_only=True)
class TableCell:
    row: int
    col: int
    box: Box

    def __post_init__(self) -> None:
        if self.row < 0 or self.col < 0:
            raise InvariantError("table rows and columns count from 0")


@dataclass(frozen=True, slots=True, kw_only=True)
class DetectedRegion:
    """A text line, a table (with its cells), a diagram or a text block, in page pixels."""

    kind: RegionKind
    box: Box
    cells: tuple[TableCell, ...] = ()

    def __post_init__(self) -> None:
        if self.cells and self.kind is not RegionKind.TABLE:
            raise InvariantError("only a table has cells")
        if len({(c.row, c.col) for c in self.cells}) != len(self.cells):
            raise InvariantError("a table cell position appears twice")


class LayoutDetector(Protocol):
    """Finds text lines, tables (with row/column cells) and diagram regions on a page, in
    reading order."""

    @property
    def ref(self) -> EngineRef: ...

    def detect(self, image: bytes) -> Sequence[DetectedRegion]: ...


class OcrEngine(Protocol):
    """Reads text lines (design.md "OCR framework"): text, box, raw confidence 0..1 and the
    engine's name on every reading. Local engines read the given line crops; cloud engines may
    ignore ``lines`` and return their own lines or words for the whole page, which the core
    matches to our lines by box overlap. Raises ``EngineFailedError`` (or
    ``EngineTimeoutError``) when it cannot read the page; the reader carries on without it."""

    @property
    def ref(self) -> EngineRef: ...

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]: ...


class PageTransform(Protocol):
    """Image operations the core needs but cannot do itself (no image library in the core)."""

    def rotate(self, image: bytes, degrees: int) -> bytes:
        """The page turned clockwise by 90, 180 or 270 degrees, re-encoded as JPEG."""
        ...

    def downscale(self, image: bytes, max_edge: int) -> bytes:
        """The page with its longer side at most ``max_edge`` pixels (JPEG)."""
        ...

    def size(self, image: bytes) -> tuple[int, int]:
        """Width and height in pixels."""
        ...


class WordList(Protocol):
    """A dictionary of English words for the selector's lexicon term."""

    def contains(self, word: str) -> bool:
        """Case-insensitive."""
        ...


class CalibrationStore(Protocol):
    """Global OCR calibrations (no personal data): latest version per engine and class."""

    def latest(self) -> Sequence[EngineCalibration]: ...

    def get(self, engine: str, content_class: ContentClass) -> EngineCalibration | None: ...

    def save(self, calibration: EngineCalibration) -> None:
        """Writes the next version; raises InvariantError when the version is not the next."""
        ...


class ScoringCalibrationStore(Protocol):
    """Global scoring calibrations (numbers only), one item per embedding model."""

    def latest(self, embedder: str) -> ScoringCalibration | None: ...

    def save(self, calibration: ScoringCalibration) -> None:
        """Writes the next version; raises InvariantError when the version is not the next."""
        ...


class Embedder(Protocol):
    """Sentence vectors. Scoring accepts only embedders that run on this machine (no student
    text leaves Tarn in the non-LLM phase, design.md "Embedding model")."""

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
    diagrams: tuple[AnswerDiagram, ...] = ()
    """The diagrams recognised in the answer (each with its graph version)."""
    reference_diagrams: Mapping[ReferenceDiagramId, ReferenceDiagram] = field(default_factory=dict)
    """The reference diagrams the question's diagram criteria point at, in the versions the
    score records."""
    question_code: str = ""
    question_text: str = ""
    slot_label: str | None = None
    reference_text: str = ""
    glossary: tuple[str, ...] = ()
    extra_content: Mapping[str, ContentRef] = field(default_factory=dict)
    sentences: tuple[str, ...] = ()
    """The answer split into sentences (struck-out text already left out); empty = the
    scorer splits ``answer_text`` itself."""
    sentence_vectors: tuple[tuple[float, ...], ...] = ()
    """One vector per sentence, computed once per answer by ``embedder``."""
    embedder: EngineRef | None = None


class Scorer(Protocol):
    """Scores one criterion; non-LLM scorers first, an LLM scorer later and off by default.
    ``credit`` is in [0, 1]; the result names the criterion version and the scorer version."""

    @property
    def ref(self) -> EngineRef: ...

    def supports(self, criterion_type: CriterionType) -> bool: ...

    def score(self, item: ScoringInput) -> CriterionScore: ...


class SecondOpinionScorer(Scorer, Protocol):
    """The LLM scorer (P19): a ``Scorer`` for ``llm`` criteria that can also judge a criterion
    another scorer scored. ``opinion`` raises ``ScorerUnavailableError`` after its retries; it
    never raises for a bad answer from the model (that is a retry, then unavailable)."""

    def opinion(self, item: ScoringInput) -> tuple[SecondOpinion, LlmUsage]: ...


class DiagramRecognizer(Protocol):
    """Finds shapes and arrows (with head and tail) in an image or in one box of it; the core
    reads the labels with the OCR framework and builds the graph. Coordinates are the whole
    image's. Raises ``EngineFailedError`` when it cannot read the image."""

    @property
    def ref(self) -> EngineRef: ...

    def recognize(self, image: bytes, box: Box | None = None) -> DiagramDetection: ...
