"""Best-of-N OCR: content classes, engine calibrations, selector settings and the score of each
reading (design.md "OCR framework (R4)").

Calibrations are global and hold numbers only (no text, no personal data): fitted from
ground-truth lines by the Tarn operator and shared by every college."""

import itertools
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from tarn_core.domain.common import ContentKind, ContentRef, check_unit_interval
from tarn_core.errors import InvariantError
from tarn_core.ids import OcrCalibrationId


class ContentClass(StrEnum):
    """What kind of writing a line is; calibrations and engine weights are per class."""

    PRINT = "print"
    CURSIVE = "cursive"
    NUMERIC = "numeric"


def calibration_id(engine: str, content_class: ContentClass) -> OcrCalibrationId:
    """One calibration item per (engine, class): a refit is its next version."""
    return OcrCalibrationId(uuid5(NAMESPACE_URL, f"tarn:ocr-calibration:{engine}:{content_class}"))


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineCalibration:
    """Maps an engine's raw confidence to a calibrated one (isotonic: piecewise linear through
    non-decreasing breakpoints), plus the engine's weight for the class.

    ``xs``/``ys`` are the breakpoints; between them the value is interpolated, outside them it
    is the nearest end value. No breakpoints = identity (the documented default)."""

    engine: str
    content_class: ContentClass
    version: int = 1
    engine_version: str = ""
    """The engine version the samples were read with (information only)."""
    xs: tuple[float, ...] = ()
    ys: tuple[float, ...] = ()
    weight: float = 1.0
    error_rate: float | None = None
    """Mean character error rate on the fitting lines (the weight is derived from it)."""
    samples: int = 0
    fitted_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.engine.strip():
            raise InvariantError("a calibration names its engine")
        if self.version < 1:
            raise InvariantError("calibration version starts at 1")
        if len(self.xs) != len(self.ys):
            raise InvariantError("one calibrated value per breakpoint")
        for x in self.xs:
            check_unit_interval("calibration breakpoint", x)
        for y in self.ys:
            check_unit_interval("calibrated value", y)
        if any(b < a for a, b in itertools.pairwise(self.xs)):
            raise InvariantError("calibration breakpoints must be sorted")
        if any(b < a for a, b in itertools.pairwise(self.ys)):
            raise InvariantError("calibrated values must not decrease (isotonic)")
        if not (math.isfinite(self.weight) and self.weight >= 0):
            raise InvariantError("an engine weight is a non-negative number")
        if self.error_rate is not None:
            check_unit_interval("error rate", self.error_rate)
        if self.samples < 0:
            raise InvariantError("sample count must not be negative")
        if self.fitted_at is not None and self.fitted_at.tzinfo is None:
            raise InvariantError("fitted_at must be timezone-aware")

    @property
    def id(self) -> OcrCalibrationId:
        return calibration_id(self.engine, self.content_class)

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.OCR_CALIBRATION, id=self.id, version=self.version)

    def calibrate(self, confidence: float) -> float:
        xs, ys = self.xs, self.ys
        if not xs:
            return confidence
        if confidence <= xs[0]:
            return ys[0]
        if confidence >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if confidence <= xs[i]:
                x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
                if x1 == x0:
                    return y1
                return y0 + (y1 - y0) * (confidence - x0) / (x1 - x0)
        return ys[-1]  # pragma: no cover  (the loop always returns)


DEFAULT_ENGINE_SETS: Mapping[ContentClass, tuple[str, ...]] = MappingProxyType(
    {
        ContentClass.PRINT: ("paddle", "tesseract", "textract", "azure"),
        ContentClass.CURSIVE: ("trocr", "paddle", "textract", "azure"),
        ContentClass.NUMERIC: ("trocr", "paddle", "tesseract", "textract", "azure"),
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectorSettings:
    """Weights of the selector ``S = w·p̂ + α·agree + β·lex`` and the line flag threshold.
    The defaults are documented placeholders until the benchmark (P11) and fitted
    calibrations replace them."""

    alpha: float = 0.5
    beta: float = 0.25
    flag_threshold: float = 0.6
    """A line is flagged when its normalised score (best S over the best S it could have had)
    is below this."""
    engine_sets: Mapping[ContentClass, tuple[str, ...]] = field(
        default_factory=lambda: dict(DEFAULT_ENGINE_SETS)
    )
    print_engines: tuple[str, ...] = ("paddle", "tesseract")
    """Engines built for printed text: when they agree confidently, the line is print."""
    numeric_share: float = 0.6
    print_agreement: float = 0.8
    print_confidence: float = 0.8

    def __post_init__(self) -> None:
        for name, value in (("alpha", self.alpha), ("beta", self.beta)):
            if not (math.isfinite(value) and value >= 0):
                raise InvariantError(f"{name} must be a non-negative number")
        for name, unit in (
            ("flag threshold", self.flag_threshold),
            ("numeric share", self.numeric_share),
            ("print agreement", self.print_agreement),
            ("print confidence", self.print_confidence),
        ):
            check_unit_interval(name, unit)
        missing = set(ContentClass) - set(self.engine_sets)
        if missing:
            raise InvariantError(f"no engine set for {sorted(missing)}")

    @property
    def all_engines(self) -> tuple[str, ...]:
        """Every engine named in any class, first mention first."""
        seen: dict[str, None] = {}
        for content_class in ContentClass:
            for name in self.engine_sets[content_class]:
                seen.setdefault(name)
        return tuple(seen)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadingScore:
    """The selector's view of one reading: every term of S, kept so that a teacher, the
    benchmark and the learning loop can see why a reading won."""

    calibrated: float
    agreement: float
    lexicon: float
    weight: float
    score: float
    competing: bool = True
    """False when the reading's engine is not in the line's class set (kept, never chosen)."""

    def __post_init__(self) -> None:
        for name, value in (
            ("calibrated confidence", self.calibrated),
            ("agreement", self.agreement),
            ("lexicon fit", self.lexicon),
        ):
            check_unit_interval(name, value)
        if not (math.isfinite(self.score) and self.score >= 0 and self.weight >= 0):
            raise InvariantError("selector score and weight are non-negative numbers")
