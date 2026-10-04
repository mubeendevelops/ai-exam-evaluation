"""Fitting OCR calibrations from ground-truth lines: isotonic regression, engine weights from
error rates, minimum samples, versions, and the calibration curve itself."""

import itertools
import math
import random
from datetime import UTC, datetime

import pytest

from tarn_core.domain.ocr import ContentClass, EngineCalibration, calibration_id
from tarn_core.errors import InvariantError
from tarn_core.services.ocr.calibration import (
    GroundTruthSample,
    engine_weights,
    fit_calibrations,
    isotonic,
)
from tarn_core.testing import MemoryCalibrationStore

NOW = datetime(2026, 10, 4, tzinfo=UTC)


def test_isotonic_pools_adjacent_violators_by_hand() -> None:
    # y: 1, 3, 2, 4 at x 0.1..0.4 → (3 + 2)/2 pooled: 1, 2.5, 2.5, 4 (scaled to [0, 1] below).
    xs, ys = isotonic([(0.1, 0.1), (0.2, 0.3), (0.3, 0.2), (0.4, 0.4)])
    assert xs == (0.1, 0.2, 0.3, 0.4)
    assert ys == pytest.approx((0.1, 0.25, 0.25, 0.4))


def test_isotonic_averages_equal_x_and_handles_a_falling_series() -> None:
    xs, ys = isotonic([(0.5, 1.0), (0.5, 0.0), (0.2, 0.9), (0.9, 0.1)])
    # x=0.5 averages to 0.5; 0.9, 0.5, 0.1 fall throughout → one block of mean 0.5.
    assert xs == (0.2, 0.9)
    assert ys == pytest.approx((0.5, 0.5))
    assert isotonic([]) == ((), ())


def test_isotonic_is_monotone_and_least_squares_on_random_data() -> None:
    rng = random.Random(7)  # noqa: S311  (test data, not secrets)
    points = [(round(rng.random(), 3), rng.random()) for _ in range(300)]
    xs, ys = isotonic(points)
    assert list(xs) == sorted(xs)
    assert all(b >= a for a, b in itertools.pairwise(ys))
    # A fit cannot have a larger squared error than the best constant (the mean).
    cal = EngineCalibration(engine="e", content_class=ContentClass.CURSIVE, xs=xs, ys=ys)
    mean = sum(y for _, y in points) / len(points)
    fitted_error = sum((cal.calibrate(x) - y) ** 2 for x, y in points)
    assert fitted_error <= sum((mean - y) ** 2 for _, y in points)


def test_calibrate_interpolates_and_clamps() -> None:
    cal = EngineCalibration(
        engine="e", content_class=ContentClass.PRINT, xs=(0.2, 0.6), ys=(0.1, 0.9)
    )
    assert cal.calibrate(0.0) == 0.1
    assert cal.calibrate(0.4) == pytest.approx(0.5)
    assert cal.calibrate(1.0) == 0.9
    assert EngineCalibration(engine="e", content_class=ContentClass.PRINT).calibrate(0.37) == 0.37


def test_calibrations_are_well_formed() -> None:
    with pytest.raises(InvariantError):
        EngineCalibration(
            engine="e", content_class=ContentClass.PRINT, xs=(0.5, 0.2), ys=(0.1, 0.2)
        )
    with pytest.raises(InvariantError):
        EngineCalibration(
            engine="e", content_class=ContentClass.PRINT, xs=(0.2, 0.5), ys=(0.3, 0.2)
        )
    with pytest.raises(InvariantError):
        EngineCalibration(engine="e", content_class=ContentClass.PRINT, xs=(0.2,), ys=())
    with pytest.raises(InvariantError):
        EngineCalibration(engine="e", content_class=ContentClass.PRINT, weight=-0.1)
    with pytest.raises(InvariantError):
        EngineCalibration(
            engine="e", content_class=ContentClass.PRINT, fitted_at=datetime(2026, 1, 1)
        )
    assert calibration_id("trocr", ContentClass.CURSIVE) == calibration_id(
        "trocr", ContentClass.CURSIVE
    )
    assert calibration_id("trocr", ContentClass.CURSIVE) != calibration_id(
        "trocr", ContentClass.PRINT
    )


def test_engine_weights_follow_adaboost_and_scale_to_the_best() -> None:
    weights = engine_weights({"good": 0.1, "fair": 0.3, "chance": 0.5, "bad": 0.7})

    def alpha(e: float) -> float:
        return 0.5 * math.log((1 - e) / e)

    assert weights["good"] == 1.0
    assert weights["fair"] == pytest.approx(alpha(0.3) / alpha(0.1), abs=1e-6)
    assert weights["chance"] == 0.0
    assert weights["bad"] == 0.0
    assert engine_weights({"perfect": 0.0, "fair": 0.3})["perfect"] == 1.0
    assert engine_weights({"bad": 0.9}) == {"bad": 0.0}


def _samples(engine: str, cls: ContentClass, n: int, error_every: int) -> list[GroundTruthSample]:
    out = []
    for i in range(n):
        confidence = (i % 10) / 10
        wrong = i % error_every == 0
        out.append(
            GroundTruthSample(
                engine=engine,
                engine_version="v1",
                content_class=cls,
                confidence=confidence,
                text="abcdefghij" if not wrong else "abcdeXXXXX",
                truth="abcdefghij",
            )
        )
    return out


def test_fit_needs_enough_lines_and_weights_engines_within_a_class() -> None:
    samples = (
        _samples("trocr", ContentClass.CURSIVE, 40, error_every=4)
        + _samples("paddle", ContentClass.CURSIVE, 40, error_every=2)
        + _samples("tesseract", ContentClass.CURSIVE, 10, error_every=2)  # too few
    )
    fitted = fit_calibrations(samples, previous={}, fitted_at=NOW, min_samples=30)
    by_engine = {c.engine: c for c in fitted}
    assert set(by_engine) == {"paddle", "trocr"}
    trocr, paddle = by_engine["trocr"], by_engine["paddle"]
    assert trocr.error_rate == pytest.approx(10 * 0.5 / 40)
    assert paddle.error_rate == pytest.approx(20 * 0.5 / 40)
    assert trocr.weight == 1.0
    assert 0 < paddle.weight < 1
    assert trocr.samples == 40 and trocr.version == 1 and trocr.fitted_at == NOW
    assert trocr.engine_version == "v1"
    assert list(trocr.ys) == sorted(trocr.ys)


def test_a_refit_is_the_next_version_and_the_store_insists_on_it() -> None:
    store = MemoryCalibrationStore()
    first = fit_calibrations(
        _samples("trocr", ContentClass.CURSIVE, 30, 3), previous={}, fitted_at=NOW
    )
    for cal in first:
        store.save(cal)
    previous = {(c.engine, c.content_class): c for c in store.latest()}
    second = fit_calibrations(
        _samples("trocr", ContentClass.CURSIVE, 30, 5), previous=previous, fitted_at=NOW
    )
    assert [c.version for c in second] == [2]
    store.save(second[0])
    assert store.get("trocr", ContentClass.CURSIVE) == second[0]
    with pytest.raises(InvariantError):
        store.save(second[0])  # version 2 again


def test_a_fitted_calibration_holds_numbers_only() -> None:
    cal = fit_calibrations(
        _samples("trocr", ContentClass.NUMERIC, 30, 3), previous={}, fitted_at=NOW
    )[0]
    text_fields = {"engine", "engine_version", "content_class"}
    for name in cal.__slots__:
        value = getattr(cal, name)
        if isinstance(value, str):
            assert name in text_fields, name  # no reading or truth text kept
