"""The OCR benchmark's arithmetic by hand: error rates, grouping, the selector against the best
engine, intervals, calibration by fold, and a report that holds numbers and no text."""

from datetime import UTC, datetime

import pytest

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.groundtruth import CaptureType
from tarn_core.domain.ocr import ContentClass, SelectorSettings
from tarn_core.services.ocr.benchmark import (
    ALL,
    MIN_PAGES_FOR_CI,
    ORACLE,
    SELECTOR,
    SELECTOR_CALIBRATED,
    SELECTOR_TRUE_CLASS,
    BenchLine,
    BenchRun,
    DeviceTiming,
    PageStats,
    build_report,
    capture_group,
    class_group,
    measure,
    pick,
    tally,
)
from tarn_core.services.ocr.benchmark_report import render
from tarn_core.services.ocr.selector import Lexicon
from tarn_core.testing import SetWordList

NOW = datetime(2026, 10, 4, tzinfo=UTC)
BOX = Box(x0=0, y0=0, x1=100, y1=20)
ENGINES = ("trocr", "paddle")
LEXICON = Lexicon(frozenset(), SetWordList(["the", "quick", "brown", "fox", "jumps"]))


def reading(engine: str, text: str, confidence: float = 0.9) -> LineReading:
    return LineReading(
        engine=EngineRef(name=engine, version="1"), text=text, box=BOX, confidence=confidence
    )


def line(
    truth: str,
    trocr: str | None,
    paddle: str | None,
    *,
    page: str = "p1",
    capture: CaptureType = CaptureType.PHONE_PHOTO,
    content_class: ContentClass = ContentClass.CURSIVE,
    verified: bool = True,
    prefill: str | None = None,
    confidences: tuple[float, float] = (0.9, 0.9),
) -> BenchLine:
    readings = tuple(
        reading(name, text, conf)
        for name, text, conf in (
            ("trocr", trocr, confidences[0]),
            ("paddle", paddle, confidences[1]),
        )
        if text is not None
    )
    return BenchLine(
        page=page,
        capture=capture,
        content_class=content_class,
        truth=truth,
        verified=verified,
        readings=readings,
        prefill=prefill,
    )


def run_measure(lines: list[BenchLine], **kwargs):  # type: ignore[no-untyped-def]
    return measure(
        lines, engines=ENGINES, settings=SelectorSettings(), lexicon=LEXICON, now=NOW, **kwargs
    )


# --- the measures -----------------------------------------------------------------------------


def test_tally_counts_edits_by_hand() -> None:
    t = tally("the quik brown", "the quick brown")
    assert (t.chars, t.char_edits, t.words, t.word_edits) == (15, 1, 3, 1)
    assert t.cer == pytest.approx(1 / 15)
    assert t.wer == pytest.approx(1 / 3)


def test_tally_ignores_case_and_spacing_but_not_punctuation() -> None:
    assert tally("The  QUICK", "the quick").char_edits == 0
    assert tally("the quick.", "the quick").char_edits == 1


def test_a_missing_reading_misses_every_character_and_cer_can_exceed_one() -> None:
    assert tally("", "abcd").cer == 1.0
    assert tally("abcdefgh", "abcd").cer == 1.0  # 4 insertions over 4 characters


def test_only_verified_lines_are_measured() -> None:
    lines = [
        line("the fox", "the fox", "the fox"),
        line("hidden", "totally wrong", "nothing", verified=False),
    ]
    acc = run_measure(lines)
    assert acc is not None
    assert acc.verified_lines == 1
    assert acc.methods["trocr"].groups[ALL].lines == 1
    assert acc.methods["trocr"].groups[ALL].cer == 0
    assert run_measure([lines[1]]) is None


def test_engine_selector_oracle_rates_and_groups() -> None:
    lines = [
        line("the quick fox", "the quick fox", "the quik fax", capture=CaptureType.CLEAN_SCAN),
        line("brown fox", "brown fx", "brown fox", content_class=ContentClass.PRINT),
    ]
    acc = run_measure(lines)
    assert acc is not None
    trocr, paddle = acc.methods["trocr"], acc.methods["paddle"]
    # trocr: 0 + 1 edit over 13 + 9 characters; paddle: 2 + 0
    assert trocr.groups[ALL].cer == pytest.approx(1 / 22)
    assert paddle.groups[ALL].cer == pytest.approx(2 / 22)
    assert trocr.groups[class_group(ContentClass.CURSIVE)].lines == 1
    assert paddle.groups[capture_group(CaptureType.CLEAN_SCAN)].cer == pytest.approx(2 / 13)
    # the oracle takes the best reading of every line: no errors at all
    assert acc.methods[ORACLE].groups[ALL].char_edits == 0
    # nothing beats the oracle, and every group of a method adds up to its overall tally
    for method in acc.methods.values():
        assert method.groups[ALL].char_edits >= acc.methods[ORACLE].groups[ALL].char_edits
        per_class = sum(t.lines for g, t in method.groups.items() if g.startswith("class:"))
        per_capture = sum(t.lines for g, t in method.groups.items() if g.startswith("capture:"))
        assert per_class == per_capture == method.groups[ALL].lines


def test_selector_matches_the_production_selector_and_the_true_class_variant() -> None:
    settings = SelectorSettings()
    item = line("the fox", "the fox", "the fax", confidences=(0.9, 0.5))
    picked = pick(item, settings=settings, calibrations={}, lexicon=LEXICON)
    assert picked.text == "the fox"  # higher confidence and in the word list
    acc = run_measure([item])
    assert acc is not None
    assert acc.methods[SELECTOR].groups[ALL].char_edits == 0
    assert acc.methods[SELECTOR_TRUE_CLASS].groups[ALL].char_edits == 0
    assert (
        pick(item, settings=settings, calibrations={}, lexicon=LEXICON, without="trocr").text
        == "the fax"
    )


def test_no_reading_lines_are_counted_per_engine() -> None:
    acc = run_measure([line("the fox", "the fox", None), line("the fox", None, None)])
    assert acc is not None
    assert acc.no_reading == {"trocr": 1, "paddle": 2}
    assert acc.methods["paddle"].groups[ALL].cer == 1.0
    assert acc.methods[SELECTOR].groups[ALL].lines == 2


def test_best_single_engine_comparison_and_ablation() -> None:
    lines = [line("the quick fox", "the quick fox", "the quik fox", page=f"p{i}") for i in range(3)]
    acc = run_measure(lines)
    assert acc is not None
    overall = next(c for c in acc.comparisons if c.group == ALL)
    assert overall.best_engine == "trocr"
    assert overall.delta == pytest.approx(overall.selector_cer - overall.best_cer)
    assert set(acc.ablation) == set(ENGINES)
    # without trocr only paddle is left, so the selector can only be as bad as paddle
    assert acc.ablation["trocr"].cer == pytest.approx(acc.methods["paddle"].groups[ALL].cer)


def test_confusion_counts_the_predicted_class_against_the_written_one() -> None:
    lines = [
        line("12 + 7 = 19", "12 + 7 = 19", "12 + 7 = 19", content_class=ContentClass.NUMERIC),
        line("the fox", "the fox", "the fox", content_class=ContentClass.NUMERIC),
    ]
    acc = run_measure(lines)
    assert acc is not None
    assert acc.confusion[(ContentClass.NUMERIC, ContentClass.NUMERIC)] == 1
    assert acc.confusion[(ContentClass.NUMERIC, ContentClass.CURSIVE)] == 1


def test_flag_curve_flags_more_as_the_threshold_rises() -> None:
    lines = [
        line("the fox", "the fox", "the fox", confidences=(0.95, 0.95)),
        line("the fox", "the fax", "tke fox", confidences=(0.4, 0.3)),
        line("brown", "brown", "brwn", confidences=(0.7, 0.6)),
    ]
    acc = run_measure(lines)
    assert acc is not None
    shares = [p.flagged_share for p in acc.flag_curve]
    assert shares == sorted(shares)
    assert shares[0] < shares[-1]
    low = acc.flag_curve[-1]
    assert low.cer_flagged is not None and low.cer_unflagged is not None


def test_grid_covers_every_alpha_and_beta() -> None:
    acc = run_measure([line("the fox", "the fox", "the fax")])
    assert acc is not None
    assert len(acc.grid) == 15


def test_anchoring_share_of_truth_equal_to_prefill() -> None:
    lines = [
        line("same", "same", "same", prefill="Same"),
        line("changed", "chnged", "chnged", prefill="chnged"),
        line("typed", "typed", "typed"),  # no pre-fill: not counted
    ]
    acc = run_measure(lines)
    assert acc is not None
    assert acc.unchanged_from_prefill == 0.5


# --- intervals and calibration ----------------------------------------------------------------


def many_pages(n: int) -> list[BenchLine]:
    lines = []
    for i in range(n):
        lines.append(
            line("the quick brown fox", "the quick brown fox", "the quik brown fox", page=f"p{i}")
        )
        lines.append(
            line(
                "jumps the fox",
                "jumps the fx" if i % 2 else "jumps the fox",
                "jumps tha fox",
                page=f"p{i}",
            )
        )
    return lines


def test_intervals_appear_from_five_pages_and_surround_the_estimate() -> None:
    few = run_measure(many_pages(MIN_PAGES_FOR_CI - 1))
    enough = run_measure(many_pages(MIN_PAGES_FOR_CI + 3))
    assert few is not None and enough is not None
    assert few.methods["trocr"].ci is None
    ci = enough.methods["trocr"].ci
    assert ci is not None
    cer = enough.methods["trocr"].groups[ALL].cer
    assert cer is not None and ci[0] <= cer <= ci[1]
    assert run_measure(many_pages(MIN_PAGES_FOR_CI + 3)).methods["trocr"].ci == ci
    assert next(c for c in enough.comparisons if c.group == ALL).delta_ci is not None


def test_calibrated_selector_is_scored_only_on_pages_it_was_not_fitted_on() -> None:
    acc = run_measure(many_pages(6), min_samples=2)
    assert acc is not None and acc.calibrated is not None
    assert acc.calibrated.folds == 5
    assert acc.calibrated.fitted > 0
    assert acc.calibrated.tally.lines == 12  # every line scored exactly once
    assert SELECTOR_CALIBRATED in acc.methods
    # too few pages to hold any out, or too few lines to fit
    few = run_measure(many_pages(2), min_samples=2)
    assert few is not None and few.calibrated is None
    unfitted = run_measure(many_pages(6))
    assert unfitted is not None and unfitted.calibrated is not None
    assert unfitted.calibrated.fitted == 0


# --- the report -------------------------------------------------------------------------------


def make_run(lines: list[BenchLine]) -> BenchRun:
    return BenchRun(
        lines=tuple(lines),
        pages=(
            PageStats(
                page="p0",
                capture=CaptureType.PHONE_PHOTO,
                lines=len(lines),
                detected=len(lines),
                matched=len(lines) - 1,
                tables=1,
                lines_in_tables=2,
                layout_seconds=1.2,
                engine_seconds={"trocr": 2.5, "paddle": 1.0},
                failures=("tesseract:error",),
            ),
        ),
        timings=(
            DeviceTiming(
                device="cuda (test)",
                pages=3,
                lines_per_page=20,
                layout_seconds=1.2,
                engine_seconds={"trocr": 2.5, "paddle": 1.0},
            ),
            DeviceTiming(
                device="cpu",
                pages=3,
                lines_per_page=20,
                layout_seconds=1.5,
                engine_seconds={"trocr": 30.0, "paddle": 1.1},
            ),
        ),
        engines={"trocr": "m1", "paddle": "p1"},
        skipped={"textract": "cloud OCR is disabled"},
        layout="layout 1",
        device="cuda",
        settings=SelectorSettings(),
        date="2026-10-04",
        label="unit",
        config={"TARN_TROCR_MODEL": "base"},
    )


def test_the_report_holds_numbers_and_none_of_the_text() -> None:
    secret = "unmistakable"
    lines = [
        BenchLine(
            page="p0",
            capture=CaptureType.PHONE_PHOTO,
            content_class=ContentClass.CURSIVE,
            truth=f"{secret} truth {i}",
            verified=i % 2 == 0,
            readings=(
                reading("trocr", f"{secret} truth {i}"),
                reading("paddle", f"{secret} trth {i}"),
            ),
            prefill=f"{secret} prefill",
        )
        for i in range(10)
    ]
    report = build_report(make_run(lines), lexicon=LEXICON, now=NOW)
    text = render(report)
    assert secret not in text and "prefill" not in text.split("## Definitions")[0]
    for heading in (
        "## Set-up",
        "## Ground truth",
        "## Error rates",
        "## Line detection",
        "## Seconds per page",
        "Selector against the best single engine",
    ):
        assert heading in text
    assert "cuda (test)" in text and "tesseract:error" in text
    assert "textract (cloud OCR is disabled)" in text


def test_without_verified_lines_the_report_says_so_and_still_gives_the_rest() -> None:
    lines = [line("the fox", "the fox", "the fax", verified=False)]
    report = build_report(make_run(lines), lexicon=LEXICON, now=NOW)
    assert report.accuracy is None
    text = render(report)
    assert "No line is verified yet" in text
    assert "## Error rates" not in text
    assert "## Seconds per page" in text and "## Line detection" in text
    assert report.descriptive.lines == 1
    assert report.detection.matched == 0


def test_warnings_for_a_small_set_and_for_anchoring() -> None:
    lines = [line("the fox", "the fox", "the fox", prefill="the fox")]
    report = build_report(make_run(lines), lexicon=LEXICON, now=NOW)
    joined = " ".join(report.warnings)
    assert "indicative only" in joined
    assert "anchoring" in joined
    assert "no confidence intervals" in joined
