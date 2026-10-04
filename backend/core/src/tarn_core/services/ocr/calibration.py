"""Fitting OCR calibrations from ground-truth lines (design.md "Choosing the best reading",
"Learning loop").

For each (engine, content class) with enough lines:

* **isotonic regression** (pool adjacent violators) of the line's character accuracy
  (1 − CER) on the engine's raw confidence: p̂ becomes the expected share of correct characters
  at that confidence, and it never falls as the confidence rises;
* **engine weight** from its error rate ε (mean CER) as learners are weighted in AdaBoost:
  ½·ln((1 − ε)/ε), at least 0, divided by the best engine's value in the class (best = 1;
  an engine no better than chance, ε ≥ 0.5, gets 0).

Engines with fewer lines than ``min_samples`` keep their current calibration (or the default:
identity, weight 1). The fit holds numbers only."""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from tarn_core.domain.ocr import ContentClass, EngineCalibration
from tarn_core.errors import InvariantError
from tarn_core.services.ocr.text import cer

MIN_SAMPLES = 30
_EPS = 1e-6


@dataclass(frozen=True, slots=True, kw_only=True)
class GroundTruthSample:
    """One engine's reading of one hand-transcribed line."""

    engine: str
    engine_version: str
    content_class: ContentClass
    confidence: float
    text: str
    truth: str

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise InvariantError("confidence must be in [0, 1]")


def isotonic(points: Sequence[tuple[float, float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Least-squares non-decreasing fit of y on x (pool adjacent violators). Returns
    breakpoints: each pooled block's lowest and highest x with the block's mean."""
    if not points:
        return (), ()
    grouped: dict[float, list[float]] = defaultdict(list)
    for x, y in points:
        grouped[x].append(y)
    # blocks: [sum of y, count, lowest x, highest x]
    blocks: list[list[float]] = []
    for x in sorted(grouped):
        ys = grouped[x]
        blocks.append([sum(ys), float(len(ys)), x, x])
        while len(blocks) >= 2 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            last = blocks.pop()
            prev = blocks[-1]
            prev[0] += last[0]
            prev[1] += last[1]
            prev[3] = last[3]
    xs: list[float] = []
    values: list[float] = []
    for total, count, low, high in blocks:
        mean = min(1.0, max(0.0, round(total / count, 6)))
        for x in (low, high) if high > low else (low,):
            xs.append(x)
            values.append(mean)
    return tuple(xs), tuple(values)


def engine_weights(error_rates: Mapping[str, float]) -> dict[str, float]:
    """AdaBoost-style weights from error rates, scaled so that the best engine has 1."""
    raw = {}
    for engine, rate in error_rates.items():
        eps = min(1 - _EPS, max(_EPS, rate))
        raw[engine] = max(0.0, 0.5 * math.log((1 - eps) / eps))
    best = max(raw.values(), default=0.0)
    return {e: (round(v / best, 6) if best > 0 else 0.0) for e, v in raw.items()}


def fit_calibrations(
    samples: Sequence[GroundTruthSample],
    *,
    previous: Mapping[tuple[str, ContentClass], EngineCalibration],
    fitted_at: datetime,
    min_samples: int = MIN_SAMPLES,
) -> list[EngineCalibration]:
    """New versions for every (engine, class) with at least ``min_samples`` lines; weights are
    relative within a class, among the engines fitted together."""
    groups: dict[tuple[str, ContentClass], list[GroundTruthSample]] = defaultdict(list)
    for sample in samples:
        groups[(sample.engine, sample.content_class)].append(sample)
    eligible = {key: group for key, group in groups.items() if len(group) >= min_samples}
    error_rates: dict[ContentClass, dict[str, float]] = defaultdict(dict)
    for (engine, content_class), group in eligible.items():
        error_rates[content_class][engine] = sum(cer(s.text, s.truth) for s in group) / len(group)
    weights = {c: engine_weights(rates) for c, rates in error_rates.items()}
    fitted: list[EngineCalibration] = []
    for (engine, content_class), group in sorted(eligible.items()):
        xs, ys = isotonic([(s.confidence, 1 - cer(s.text, s.truth)) for s in group])
        before = previous.get((engine, content_class))
        fitted.append(
            EngineCalibration(
                engine=engine,
                content_class=content_class,
                version=1 if before is None else before.version + 1,
                engine_version=group[-1].engine_version,
                xs=xs,
                ys=ys,
                weight=weights[content_class][engine],
                error_rate=round(error_rates[content_class][engine], 6),
                samples=len(group),
                fitted_at=fitted_at,
            )
        )
    return fitted
