"""The OCR benchmark (design.md "Benchmark before launch"): character and word error rates per
engine, content class and capture type; the selector against the best single engine; and the
questions that tune it. Pure arithmetic over readings already made, so it runs on fakes in tests
and on the real engines in ``tarn bench ocr``.

Definitions (stated in every report):

* **CER** = character edits / characters of the truth, summed over the lines of a group (the
  corpus rate, not the mean of line rates); **WER** the same over words. Text is compared after
  Unicode NFC, case folding and collapsing white space; punctuation counts. An engine with no
  reading for a line is scored as having read nothing (all characters missed).
* Only **verified** lines are truth. Pre-filled lines are never measured.
* **Selector** = the production selector (``classify_line`` then ``select``) on the engines'
  readings of the truth boxes; **selector @ true class** gives it the class the person wrote;
  **oracle** takes the best reading of each line (an upper bound no selector can reach).
* Confidence intervals: 95 %, bootstrap over pages (lines of one page are not independent),
  fixed seed, reported only from ``MIN_PAGES_FOR_CI`` pages.

The α/β grid, the engine-removal runs and the flag curve are *in-sample*: they describe this set
and must not be read as held-out error rates. The calibrated selector is the exception: it is
fitted on pages other than the ones it is scored on (k-fold by page)."""

import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.groundtruth import CaptureType
from tarn_core.domain.ocr import ContentClass, EngineCalibration, SelectorSettings
from tarn_core.services.ocr.calibration import MIN_SAMPLES, GroundTruthSample, fit_calibrations
from tarn_core.services.ocr.selector import Calibrations, Lexicon, classify_line, select
from tarn_core.services.ocr.text import levenshtein, normalise

MIN_PAGES_FOR_CI = 5
RESAMPLES = 1000
SEED = 20261004
MAX_FOLDS = 5

ALPHAS = (0.0, 0.25, 0.5, 0.75, 1.0)
BETAS = (0.0, 0.25, 0.5)
FLAG_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)

SELECTOR = "selector"
SELECTOR_TRUE_CLASS = "selector @ true class"
SELECTOR_CALIBRATED = "selector, calibrated (k-fold)"
ORACLE = "oracle (best engine per line)"
ALL = "all"


def class_group(content_class: ContentClass) -> str:
    return f"class:{content_class.value}"


def capture_group(capture: CaptureType) -> str:
    return f"capture:{capture.value}"


# --- data -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchLine:
    """One ground-truth line with what every engine read in its box."""

    page: str
    capture: CaptureType
    content_class: ContentClass
    """The class the person wrote (the truth of the class)."""
    truth: str
    verified: bool
    readings: tuple[LineReading, ...]
    """In the configured engine order; an engine that read nothing for the line is absent."""
    prefill: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PageStats:
    """What running the layout and the engines on one page cost and found."""

    page: str
    capture: CaptureType
    lines: int
    """Ground-truth lines on the page (verified or not, not ignored)."""
    detected: int
    """Text lines (and table cells) the layout detector found."""
    matched: int
    """Truth lines covered by a detected line (IoU of at least 0.5)."""
    tables: int
    lines_in_tables: int
    layout_seconds: float
    engine_seconds: Mapping[str, float]
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceTiming:
    """Seconds per page on one device: median over the pages timed."""

    device: str
    pages: int
    lines_per_page: float
    layout_seconds: float
    engine_seconds: Mapping[str, float]

    @property
    def page_seconds(self) -> float:
        """Layout plus every engine, as one page costs in the worker (cleaning and the
        orientation check not included)."""
        return self.layout_seconds + sum(self.engine_seconds.values())


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchRun:
    """Everything a benchmark run measured; ``build_report`` turns it into numbers."""

    lines: tuple[BenchLine, ...]
    pages: tuple[PageStats, ...]
    timings: tuple[DeviceTiming, ...]
    engines: Mapping[str, str]
    """Engine name → version, in the configured order."""
    skipped: Mapping[str, str]
    layout: str
    device: str
    settings: SelectorSettings
    date: str
    label: str = ""
    config: Mapping[str, str] = field(default_factory=dict)


# --- measures ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Tally:
    lines: int = 0
    chars: int = 0
    char_edits: int = 0
    words: int = 0
    word_edits: int = 0

    def __add__(self, other: "Tally") -> "Tally":
        return Tally(
            self.lines + other.lines,
            self.chars + other.chars,
            self.char_edits + other.char_edits,
            self.words + other.words,
            self.word_edits + other.word_edits,
        )

    @property
    def cer(self) -> float | None:
        return self.char_edits / self.chars if self.chars else None

    @property
    def wer(self) -> float | None:
        return self.word_edits / self.words if self.words else None


def tally(reading: str, truth: str) -> Tally:
    nr, nt = normalise(reading), normalise(truth)
    tr, tt = nr.split(), nt.split()
    return Tally(
        lines=1,
        chars=len(nt),
        char_edits=levenshtein(nr, nt),
        words=len(tt),
        word_edits=levenshtein(tr, tt),
    )


@dataclass(frozen=True, slots=True)
class Picked:
    text: str
    line_score: float | None
    flagged: bool
    predicted: ContentClass | None
    """The class ``classify_line`` gave (None when nothing was read)."""


def pick(
    line: BenchLine,
    *,
    settings: SelectorSettings,
    calibrations: Calibrations,
    lexicon: Lexicon,
    true_class: bool = False,
    without: str | None = None,
) -> Picked:
    """What the production selector would choose for the line (optionally given the true class,
    or without one engine)."""
    readings = tuple(r for r in line.readings if r.engine.name != without)
    if not readings:
        return Picked("", None, True, None)
    predicted = classify_line(readings, settings)
    choice = select(
        readings,
        line.content_class if true_class else predicted,
        settings=settings,
        calibrations=calibrations,
        lexicon=lexicon,
    )
    text = "" if choice.chosen is None else readings[choice.chosen].text
    return Picked(text, choice.line_score, choice.flagged, predicted)


def _oracle(line: BenchLine) -> Tally:
    options = [tally(r.text, line.truth) for r in line.readings] or [tally("", line.truth)]
    return min(options, key=lambda t: (t.char_edits, t.word_edits))


# --- results ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Method:
    """One way of producing text (an engine, the selector, the oracle) and its tallies."""

    name: str
    groups: Mapping[str, Tally]
    ci: tuple[float, float] | None
    """95 % interval of the overall CER (None below ``MIN_PAGES_FOR_CI`` pages)."""


@dataclass(frozen=True, slots=True)
class Comparison:
    group: str
    best_engine: str
    best_cer: float
    selector_cer: float
    delta_ci: tuple[float, float] | None
    """95 % interval of (selector CER − best engine CER); below 0 means the selector wins."""

    @property
    def delta(self) -> float:
        return self.selector_cer - self.best_cer


@dataclass(frozen=True, slots=True)
class FlagPoint:
    threshold: float
    flagged_share: float
    cer_flagged: float | None
    cer_unflagged: float | None


@dataclass(frozen=True, slots=True)
class CalibratedResult:
    folds: int
    fitted: int
    """Engine-and-class calibrations fitted in the fold with the most (0: nothing had enough
    lines, so the selector ran uncalibrated)."""
    tally: Tally


@dataclass(frozen=True, slots=True)
class Accuracy:
    verified_lines: int
    verified_pages: int
    methods: Mapping[str, Method]
    comparisons: tuple[Comparison, ...]
    ablation: Mapping[str, Tally]
    """Selector without one engine, by the engine removed."""
    grid: Mapping[tuple[float, float], float]
    """Selector CER by (α, β)."""
    flag_curve: tuple[FlagPoint, ...]
    confusion: Mapping[tuple[ContentClass, ContentClass | None], int]
    """(true class, predicted class) → verified lines."""
    no_reading: Mapping[str, int]
    """Verified lines each engine gave no reading for."""
    unchanged_from_prefill: float | None
    """Share of verified lines (that had a pre-fill) whose truth equals the pre-fill."""
    calibrated: CalibratedResult | None


@dataclass(frozen=True, slots=True)
class Descriptive:
    """What needs no truth: the pre-filled and verified lines alike, as the selector sees them."""

    lines: int
    by_capture: Mapping[CaptureType, int]
    predicted_class: Mapping[ContentClass, int]
    flagged_share: float | None
    mean_line_score: float | None
    engines_reading: Mapping[str, float]
    """Share of lines each engine produced a reading for."""


@dataclass(frozen=True, slots=True)
class Detection:
    pages: int
    truth_lines: int
    matched: int
    detected: int
    pages_with_tables: int
    lines_in_tables: int
    failures: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class BenchReport:
    run: BenchRun
    accuracy: Accuracy | None
    """None until at least one line is verified."""
    descriptive: Descriptive
    detection: Detection
    warnings: tuple[str, ...]


# --- analysis ---------------------------------------------------------------------------------


def _groups(line: BenchLine) -> tuple[str, str, str]:
    return ALL, class_group(line.content_class), capture_group(line.capture)


def _ratio_ci(units: Sequence[tuple[float, float]]) -> tuple[float, float] | None:
    """95 % bootstrap interval of sum(numerator) / sum(denominator) over pages."""
    if len(units) < MIN_PAGES_FOR_CI:
        return None
    rng = random.Random(SEED)  # noqa: S311  (resampling, not security)
    values = []
    for _ in range(RESAMPLES):
        sample = [units[rng.randrange(len(units))] for _ in units]
        denominator = sum(d for _, d in sample)
        if denominator:
            values.append(sum(n for n, _ in sample) / denominator)
    if not values:
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


class _Accumulator:
    """Tallies per method, group and page."""

    def __init__(self) -> None:
        self.cells: dict[str, dict[str, Tally]] = defaultdict(lambda: defaultdict(Tally))
        self.pages: dict[tuple[str, str], dict[str, Tally]] = defaultdict(
            lambda: defaultdict(Tally)
        )

    def add(self, method: str, line: BenchLine, value: Tally) -> None:
        for group in _groups(line):
            self.cells[method][group] = self.cells[method][group] + value
            page = self.pages[(method, group)]
            page[line.page] = page[line.page] + value

    def method(self, name: str) -> Method:
        units = [(t.char_edits, t.chars) for t in self.pages[(name, ALL)].values()]
        return Method(name=name, groups=dict(self.cells[name]), ci=_ratio_ci(units))

    def delta_ci(self, a: str, b: str, group: str) -> tuple[float, float] | None:
        pages_a, pages_b = self.pages[(a, group)], self.pages[(b, group)]
        units = [(pages_a[p].char_edits - pages_b[p].char_edits, pages_a[p].chars) for p in pages_a]
        return _ratio_ci(units)


def _selector_cer(
    lines: Sequence[BenchLine],
    settings: SelectorSettings,
    calibrations: Calibrations,
    lexicon: Lexicon,
) -> float | None:
    total = Tally()
    for line in lines:
        total = total + tally(
            pick(line, settings=settings, calibrations=calibrations, lexicon=lexicon).text,
            line.truth,
        )
    return total.cer


def _flag_curve(lines: Sequence[BenchLine], picks: Sequence[Picked]) -> tuple[FlagPoint, ...]:
    points = []
    for threshold in FLAG_THRESHOLDS:
        flagged, kept = Tally(), Tally()
        for line, picked in zip(lines, picks, strict=True):
            value = tally(picked.text, line.truth)
            is_flagged = (
                picked.line_score is None
                or picked.line_score < threshold
                or not normalise(picked.text)
            )
            if is_flagged:
                flagged = flagged + value
            else:
                kept = kept + value
        total = flagged.lines + kept.lines
        points.append(
            FlagPoint(
                threshold=threshold,
                flagged_share=flagged.lines / total if total else 0.0,
                cer_flagged=flagged.cer,
                cer_unflagged=kept.cer,
            )
        )
    return tuple(points)


def _calibrated(
    lines: Sequence[BenchLine],
    settings: SelectorSettings,
    lexicon: Lexicon,
    now: datetime,
    min_samples: int,
) -> CalibratedResult | None:
    """The selector with calibrations fitted on the other pages (k-fold by page)."""
    page_ids = sorted({line.page for line in lines})
    folds = min(MAX_FOLDS, len(page_ids))
    if folds < 3:
        return None
    total, fitted_most = Tally(), 0
    for fold in range(folds):
        held = set(page_ids[fold::folds])
        train = [ln for ln in lines if ln.page not in held]
        samples = [
            GroundTruthSample(
                engine=r.engine.name,
                engine_version=r.engine.version,
                content_class=ln.content_class,
                confidence=r.confidence,
                text=r.text,
                truth=ln.truth,
            )
            for ln in train
            for r in ln.readings
        ]
        fitted = fit_calibrations(samples, previous={}, fitted_at=now, min_samples=min_samples)
        fitted_most = max(fitted_most, len(fitted))
        calibrations: dict[tuple[str, ContentClass], EngineCalibration] = {
            (c.engine, c.content_class): c for c in fitted
        }
        for line in lines:
            if line.page in held:
                picked = pick(line, settings=settings, calibrations=calibrations, lexicon=lexicon)
                total = total + tally(picked.text, line.truth)
    return CalibratedResult(folds=folds, fitted=fitted_most, tally=total)


def measure(
    lines: Sequence[BenchLine],
    *,
    engines: Sequence[str],
    settings: SelectorSettings,
    lexicon: Lexicon,
    now: datetime,
    min_samples: int = MIN_SAMPLES,
) -> Accuracy | None:
    """Error rates over the verified lines; None when there are none."""
    truth = [ln for ln in lines if ln.verified]
    if not truth:
        return None
    acc = _Accumulator()
    no_calibration: Calibrations = {}
    picks: list[Picked] = []
    confusion: dict[tuple[ContentClass, ContentClass | None], int] = defaultdict(int)
    no_reading: dict[str, int] = dict.fromkeys(engines, 0)
    for line in truth:
        by_engine = {r.engine.name: r for r in line.readings}
        for engine in engines:
            reading = by_engine.get(engine)
            if reading is None:
                no_reading[engine] += 1
            acc.add(engine, line, tally("" if reading is None else reading.text, line.truth))
        chosen = pick(line, settings=settings, calibrations=no_calibration, lexicon=lexicon)
        picks.append(chosen)
        confusion[(line.content_class, chosen.predicted)] += 1
        acc.add(SELECTOR, line, tally(chosen.text, line.truth))
        informed = pick(
            line, settings=settings, calibrations=no_calibration, lexicon=lexicon, true_class=True
        )
        acc.add(SELECTOR_TRUE_CLASS, line, tally(informed.text, line.truth))
        acc.add(ORACLE, line, _oracle(line))

    methods: dict[str, Method] = {name: acc.method(name) for name in (*engines, SELECTOR)}
    methods[SELECTOR_TRUE_CLASS] = acc.method(SELECTOR_TRUE_CLASS)
    methods[ORACLE] = acc.method(ORACLE)
    calibrated = _calibrated(truth, settings, lexicon, now, min_samples)
    if calibrated is not None:
        methods[SELECTOR_CALIBRATED] = Method(
            name=SELECTOR_CALIBRATED, groups={ALL: calibrated.tally}, ci=None
        )

    comparisons = []
    for group in sorted(acc.cells[SELECTOR]):
        candidates = [
            (e, acc.cells[e][group].cer)
            for e in engines
            if group in acc.cells[e] and acc.cells[e][group].cer is not None
        ]
        selector_cer = acc.cells[SELECTOR][group].cer
        if not candidates or selector_cer is None:
            continue
        best, best_cer = min(candidates, key=lambda item: item[1] or 0.0)
        comparisons.append(
            Comparison(
                group=group,
                best_engine=best,
                best_cer=best_cer or 0.0,
                selector_cer=selector_cer,
                delta_ci=acc.delta_ci(SELECTOR, best, group),
            )
        )

    ablation: dict[str, Tally] = {}
    if len(engines) > 1:
        for engine in engines:
            total = Tally()
            for line in truth:
                picked = pick(
                    line,
                    settings=settings,
                    calibrations=no_calibration,
                    lexicon=lexicon,
                    without=engine,
                )
                total = total + tally(picked.text, line.truth)
            ablation[engine] = total

    grid: dict[tuple[float, float], float] = {}
    for alpha in ALPHAS:
        for beta in BETAS:
            cer = _selector_cer(
                truth, replace(settings, alpha=alpha, beta=beta), no_calibration, lexicon
            )
            if cer is not None:
                grid[(alpha, beta)] = cer

    prefilled = [ln for ln in truth if ln.prefill is not None]
    unchanged = (
        sum(1 for ln in prefilled if normalise(ln.prefill or "") == normalise(ln.truth))
        / len(prefilled)
        if prefilled
        else None
    )
    return Accuracy(
        verified_lines=len(truth),
        verified_pages=len({ln.page for ln in truth}),
        methods=methods,
        comparisons=tuple(comparisons),
        ablation=ablation,
        grid=grid,
        flag_curve=_flag_curve(truth, picks),
        confusion=dict(confusion),
        no_reading=no_reading,
        unchanged_from_prefill=unchanged,
        calibrated=calibrated,
    )


def describe(
    lines: Iterable[BenchLine],
    *,
    engines: Sequence[str],
    settings: SelectorSettings,
    lexicon: Lexicon,
) -> Descriptive:
    items = list(lines)
    by_capture: dict[CaptureType, int] = defaultdict(int)
    predicted: dict[ContentClass, int] = defaultdict(int)
    reading_counts = dict.fromkeys(engines, 0)
    scores: list[float] = []
    flagged = 0
    for line in items:
        by_capture[line.capture] += 1
        for r in line.readings:
            if r.engine.name in reading_counts:
                reading_counts[r.engine.name] += 1
        picked = pick(line, settings=settings, calibrations={}, lexicon=lexicon)
        if picked.predicted is not None:
            predicted[picked.predicted] += 1
        flagged += picked.flagged
        if picked.line_score is not None:
            scores.append(picked.line_score)
    total = len(items)
    return Descriptive(
        lines=total,
        by_capture=dict(by_capture),
        predicted_class=dict(predicted),
        flagged_share=flagged / total if total else None,
        mean_line_score=sum(scores) / len(scores) if scores else None,
        engines_reading={e: (n / total if total else 0.0) for e, n in reading_counts.items()},
    )


def detection(pages: Sequence[PageStats]) -> Detection:
    failures: dict[str, int] = defaultdict(int)
    for page in pages:
        for failure in page.failures:
            failures[failure] += 1
    return Detection(
        pages=len(pages),
        truth_lines=sum(p.lines for p in pages),
        matched=sum(p.matched for p in pages),
        detected=sum(p.detected for p in pages),
        pages_with_tables=sum(1 for p in pages if p.tables),
        lines_in_tables=sum(p.lines_in_tables for p in pages),
        failures=dict(failures),
    )


def build_report(run: BenchRun, *, lexicon: Lexicon, now: datetime) -> BenchReport:
    engines = tuple(run.engines)
    accuracy = measure(run.lines, engines=engines, settings=run.settings, lexicon=lexicon, now=now)
    warnings: list[str] = []
    if accuracy is None:
        warnings.append(
            "No line is verified yet: error rates cannot be reported. Pre-filled text is an "
            "engine's reading and is never used as truth."
        )
    else:
        selector = accuracy.methods[SELECTOR]
        for group, cell in sorted(selector.groups.items()):
            if cell.lines < 100:
                warnings.append(
                    f"{group}: {cell.lines} verified lines (under 100): indicative only."
                )
        if accuracy.verified_pages < MIN_PAGES_FOR_CI:
            warnings.append(
                f"{accuracy.verified_pages} verified pages: no confidence intervals "
                f"(from {MIN_PAGES_FOR_CI} pages)."
            )
        if accuracy.unchanged_from_prefill is not None and accuracy.unchanged_from_prefill > 0.8:
            warnings.append(
                f"{accuracy.unchanged_from_prefill:.0%} of the verified lines equal their "
                "pre-fill: the truth may lean towards the engines (anchoring); check a sample "
                "against the page."
            )
    return BenchReport(
        run=run,
        accuracy=accuracy,
        descriptive=describe(run.lines, engines=engines, settings=run.settings, lexicon=lexicon),
        detection=detection(run.pages),
        warnings=tuple(warnings),
    )
