"""Thresholds the scorers and flags use. The defaults are placeholders (design.md: "Until the
dev keys are teacher-validated, the values are placeholders"); ``tarn score calibrate`` fits
the similarity bands and the off-target thresholds per embedding model from teacher-marked
answers and stores them as a ``ScoringCalibration``."""

from dataclasses import dataclass

from tarn_core.domain.common import ContentRef, check_unit_interval
from tarn_core.domain.scoring import ScoringCalibration
from tarn_core.errors import InvariantError


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoringPolicy:
    half: float = 0.45
    full: float = 0.65
    margin: float = 0.05
    relevance_min: float = 0.25
    relevance_soft: float = 0.40
    relevance_top: int = 3
    """Relevance = mean of the best ``relevance_top`` sentence similarities to the key."""
    blank_words: int = 3
    """Fewer words than this (after struck-out text is left out) = blank."""
    low_ocr_share: float = 0.25
    """Share of the answer's lines below the OCR threshold that flags the answer "low OCR"."""
    calibration: ContentRef | None = None
    """The calibration these values came from (None: the placeholder defaults)."""

    def __post_init__(self) -> None:
        for name in ("half", "full", "margin", "relevance_min", "relevance_soft", "low_ocr_share"):
            check_unit_interval(name, getattr(self, name))
        if not self.half < self.full:
            raise InvariantError("the half-credit edge must be below the full-credit edge")
        if self.relevance_top < 1 or self.blank_words < 0:
            raise InvariantError("relevance_top must be >= 1 and blank_words >= 0")

    @classmethod
    def from_calibration(cls, calibration: ScoringCalibration | None) -> "ScoringPolicy":
        if calibration is None:
            return cls()
        return cls(
            half=calibration.half,
            full=calibration.full,
            margin=calibration.margin,
            relevance_min=calibration.relevance_min,
            relevance_soft=calibration.relevance_soft,
            calibration=calibration.ref,
        )

    def band(self, similarity: float) -> tuple[float, bool]:
        """Credit (0, 0.5 or 1) for a similarity, and whether it sits near a band edge."""
        credit = 1.0 if similarity >= self.full else 0.5 if similarity >= self.half else 0.0
        near = min(abs(similarity - self.full), abs(similarity - self.half)) < self.margin
        return credit, near
