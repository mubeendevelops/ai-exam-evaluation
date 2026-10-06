"""Thresholds and weights of diagram recognition and comparison. Every number is a
placeholder chosen by hand on drawn examples (no teacher-marked diagram exists, C24); they live
here so a later calibration replaces them in one place."""

from dataclasses import dataclass

from tarn_core.errors import InvariantError


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramPolicy:
    # node substitution cost = label·w_label + shape·w_shape + degree·w_degree (weights sum 1)
    label_weight: float = 0.6
    shape_weight: float = 0.25
    degree_weight: float = 0.15
    accept: float = 0.7
    """The highest substitution cost that still pairs two nodes; above it both count as
    unmatched (a missing reference node and an extra student node)."""
    snap: float = 0.25
    """Largest normalised edit distance at which a label snaps to a glossary term."""
    close: float = 0.34
    """Largest normalised edit distance at which a label is a close match of another."""
    # graph building (share of the diagram's diagonal, with a floor in pixels)
    attach: float = 0.05
    attach_min_px: int = 12
    beside: float = 0.04
    """A text this near a shape (and nearer than any arrow) labels it."""
    min_shape_confidence: float = 0.3
    min_arrow_confidence: float = 0.3
    # scoring
    low_confidence: float = 0.5
    """A student graph with an element recognised below this is flagged ``check``."""

    def __post_init__(self) -> None:
        total = self.label_weight + self.shape_weight + self.degree_weight
        if abs(total - 1.0) > 1e-9:
            raise InvariantError("the node cost weights must sum to 1")
        for name in ("accept", "snap", "close", "attach", "beside", "low_confidence"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise InvariantError(f"{name} must be in [0, 1]")
