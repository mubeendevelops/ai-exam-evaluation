"""The benchmark report as Markdown (``docs/benchmarks/ocr-<date>.md``): numbers only. No line of
text, no page image and no student identifier is written (CLAUDE.md "samples/")."""

from collections import Counter
from collections.abc import Sequence

from tarn_core.domain.groundtruth import CaptureType
from tarn_core.domain.ocr import ContentClass
from tarn_core.services.ocr.benchmark import (
    ALL,
    ALPHAS,
    BETAS,
    ORACLE,
    SELECTOR,
    SELECTOR_CALIBRATED,
    SELECTOR_TRUE_CLASS,
    Accuracy,
    BenchReport,
    Tally,
    capture_group,
    class_group,
)

_CLASSES = tuple(ContentClass)
_CAPTURES = tuple(CaptureType)


def _pct(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f} %"


def _cell(t: Tally | None) -> str:
    if t is None or not t.chars:
        return "-"
    return f"{_pct(t.cer)} / {_pct(t.wer)} (n={t.lines})"


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(row) + " |" for row in rows]
    return [*out, ""]


def render(report: BenchReport) -> str:
    run = report.run
    title = f"# OCR benchmark — {run.date}" + (f" ({run.label})" if run.label else "")
    out = [
        title,
        "",
        "Numbers only: no recognised or transcribed text, page image or student identifier is in "
        'this file. Regenerate with `tarn bench ocr` (see CLAUDE.md, "OCR benchmark (P11)").',
        "",
    ]
    if report.warnings:
        out += ["## Read this first", ""] + [f"- {w}" for w in report.warnings] + [""]
    out += _setup(report)
    out += _ground_truth(report)
    out += _accuracy(report.accuracy, tuple(run.engines)) if report.accuracy else []
    out += _detection(report)
    out += _descriptive(report)
    out += _timing(report)
    out += _definitions()
    return "\n".join(out).rstrip() + "\n"


def _setup(report: BenchReport) -> list[str]:
    run = report.run
    s = run.settings
    out = ["## Set-up", ""]
    out += _table(["Engine", "Version"], [[name, version] for name, version in run.engines.items()])
    if run.skipped:
        out += ["Not run: " + "; ".join(f"{n} ({why})" for n, why in run.skipped.items()), ""]
    out += [
        f"- Layout detector: {run.layout}",
        f"- Device for the accuracy run: {run.device}",
        f"- Selector: α {s.alpha}, β {s.beta}, flag threshold {s.flag_threshold} "
        "(placeholders until this benchmark's numbers are adopted)",
    ]
    out += [f"- {key}: {value}" for key, value in run.config.items()]
    return [*out, ""]


def _ground_truth(report: BenchReport) -> list[str]:
    run = report.run
    pages = Counter(p.capture for p in run.pages)
    verified = Counter(ln.capture for ln in run.lines if ln.verified)
    prefilled = Counter(ln.capture for ln in run.lines if not ln.verified)
    rows = [
        [c.value, str(pages[c]), str(verified[c]), str(prefilled[c])]
        for c in _CAPTURES
        if pages[c] or verified[c] or prefilled[c]
    ]
    rows.append(
        [
            "**all**",
            str(sum(pages.values())),
            str(sum(verified.values())),
            str(sum(prefilled.values())),
        ]
    )
    return [
        "## Ground truth",
        "",
        *_table(["Capture type", "Pages", "Verified lines", "Not yet verified"], rows),
    ]


def _methods_in_order(acc: Accuracy, engines: Sequence[str]) -> list[str]:
    names = [*engines, SELECTOR, SELECTOR_TRUE_CLASS]
    if SELECTOR_CALIBRATED in acc.methods:
        names.append(SELECTOR_CALIBRATED)
    return [*names, ORACLE]


def _accuracy(acc: Accuracy, engines: Sequence[str]) -> list[str]:
    out = ["## Error rates (verified lines only)", ""]
    out += [
        f"{acc.verified_lines} verified lines on {acc.verified_pages} pages. "
        "Cells read **CER / WER (lines)**; the overall table adds a 95 % interval of the CER.",
        "",
        "### Overall",
        "",
    ]
    rows = []
    for name in _methods_in_order(acc, engines):
        method = acc.methods[name]
        t = method.groups.get(ALL)
        ci = "-" if method.ci is None else f"{_pct(method.ci[0])} to {_pct(method.ci[1])}"
        rows.append(
            [
                name,
                str(t.lines if t else 0),
                _pct(t.cer if t else None),
                ci,
                _pct(t.wer if t else None),
            ]
        )
    out += _table(["Method", "Lines", "CER", "95 % interval", "WER"], rows)

    for heading, groups, label in (
        ("By content class (the class the person wrote)", [class_group(c) for c in _CLASSES], None),
        ("By capture type", [capture_group(c) for c in _CAPTURES], None),
    ):
        out += [f"### {heading}", ""]
        present = [g for g in groups if g in acc.methods[SELECTOR].groups]
        rows = []
        for name in _methods_in_order(acc, engines):
            if name == SELECTOR_CALIBRATED:
                continue
            m = acc.methods[name]
            rows.append([name, *[_cell(m.groups.get(g)) for g in present]])
        out += _table(["Method", *[g.split(":", 1)[1] for g in present]], rows)
        del label

    out += ["### Selector against the best single engine", ""]
    rows = []
    for c in acc.comparisons:
        ci = (
            "-"
            if c.delta_ci is None
            else f"{c.delta_ci[0] * 100:+.1f} to {c.delta_ci[1] * 100:+.1f} points"
        )
        rows.append(
            [
                c.group,
                c.best_engine,
                _pct(c.best_cer),
                _pct(c.selector_cer),
                f"{c.delta * 100:+.1f} points",
                ci,
            ]
        )
    out += _table(
        ["Group", "Best engine", "Its CER", "Selector CER", "Selector − best", "95 % interval"],
        rows,
    )
    out += [
        "The best engine is picked on the same lines, which favours the engine; a negative "
        "difference with an interval below zero is a clear win for the selector.",
        "",
    ]
    if acc.calibrated is not None:
        cal = acc.calibrated
        out += [
            "### Calibrated selector",
            "",
            f"Calibrations fitted on the other pages ({cal.folds}-fold by page): "
            f"{cal.fitted} engine-and-class calibrations were fitted at most per fold "
            f"(an engine needs 30 lines per class). CER {_pct(cal.tally.cer)} over "
            f"{cal.tally.lines} lines"
            + (
                " (nothing was fitted, so this is the uncalibrated selector)."
                if not cal.fitted
                else "."
            ),
            "",
        ]

    out += ["### Content-class rule (D69)", ""]
    predicted = [*_CLASSES, None]
    rows = []
    for true in _CLASSES:
        counts = [acc.confusion.get((true, p), 0) for p in predicted]
        total = sum(counts)
        right = acc.confusion.get((true, true), 0)
        rows.append([true.value, *[str(n) for n in counts], _pct(right / total if total else None)])
    out += _table(
        ["True class ↓ / predicted →", *[c.value for c in _CLASSES], "none read", "Correct"], rows
    )

    if acc.ablation:
        out += ["### Does each engine earn its place? (selector without one engine, in-sample)", ""]
        full = acc.methods[SELECTOR].groups.get(ALL)
        rows = [
            ["(all engines)", _pct(full.cer if full else None), _pct(full.wer if full else None)]
        ]
        rows += [[f"without {e}", _pct(t.cer), _pct(t.wer)] for e, t in acc.ablation.items()]
        out += _table(["Selector", "CER", "WER"], rows)

    out += ["### Selector weights α and β (in-sample, CER)", ""]
    rows = [[f"α {a}", *[_pct(acc.grid.get((a, b))) for b in BETAS]] for a in ALPHAS]
    out += _table(["", *[f"β {b}" for b in BETAS]], rows)

    out += ["### Flag threshold (in-sample)", ""]
    rows = [
        [f"{p.threshold}", _pct(p.flagged_share), _pct(p.cer_flagged), _pct(p.cer_unflagged)]
        for p in acc.flag_curve
    ]
    out += _table(["Threshold", "Lines flagged", "CER of flagged", "CER of the rest"], rows)

    out += ["### Other", ""]
    out += [
        "- Verified lines an engine gave no reading for: "
        + ", ".join(f"{e} {n}" for e, n in acc.no_reading.items()),
        "- Verified lines equal to their pre-fill: " + _pct(acc.unchanged_from_prefill),
        "",
    ]
    return out


def _detection(report: BenchReport) -> list[str]:
    d = report.detection
    recall = d.matched / d.truth_lines if d.truth_lines else None
    out = ["## Line detection and layout", ""]
    out += _table(
        [
            "Pages",
            "Truth lines",
            "Covered by a detected line (IoU ≥ 0.5)",
            "Detected lines",
            "Pages with a table",
            "Truth lines inside a table",
        ],
        [
            [
                str(d.pages),
                str(d.truth_lines),
                f"{d.matched} ({_pct(recall)})",
                str(d.detected),
                str(d.pages_with_tables),
                str(d.lines_in_tables),
            ]
        ],
    )
    out += [
        "Truth boxes start from the detector's own lines (pre-fill), so the coverage is an "
        "upper bound: a line the detector missed is covered only if the person added its box.",
        "",
    ]
    if d.failures:
        out += ["Engine failures: " + ", ".join(f"{k} × {n}" for k, n in d.failures.items()), ""]
    return out


def _descriptive(report: BenchReport) -> list[str]:
    d = report.descriptive
    out = ["## What the engines produced (needs no truth)", ""]
    out += _table(
        [
            "Lines",
            "Flagged at the threshold",
            "Mean line score",
            "Class: " + " / ".join(c.value for c in _CLASSES),
        ],
        [
            [
                str(d.lines),
                _pct(d.flagged_share),
                "-" if d.mean_line_score is None else f"{d.mean_line_score:.2f}",
                " / ".join(str(d.predicted_class.get(c, 0)) for c in _CLASSES),
            ]
        ],
    )
    out += _table(
        ["Engine", "Share of lines it read"],
        [[e, _pct(v)] for e, v in d.engines_reading.items()],
    )
    return out


def _timing(report: BenchReport) -> list[str]:
    out = ["## Seconds per page on this machine", ""]
    if not report.run.timings:
        return [*out, "Not timed.", ""]
    engines = list(report.run.engines)
    rows = []
    for t in report.run.timings:
        rows.append(
            [
                t.device,
                str(t.pages),
                f"{t.lines_per_page:.0f}",
                f"{t.layout_seconds:.1f}",
                *[f"{t.engine_seconds.get(e, 0.0):.1f}" for e in engines],
                f"{t.page_seconds:.1f}",
            ]
        )
    out += _table(["Device", "Pages", "Lines/page", "Layout", *engines, "Total"], rows)
    out += [
        "Median seconds of one page over the pages timed, after one untimed warm-up read per "
        "engine. Total = layout plus every engine, as the worker runs a page; cleaning and the "
        "orientation check are not included.",
        "",
    ]
    return out


def _definitions() -> list[str]:
    return [
        "## Definitions",
        "",
        "- **CER** = character edits ÷ characters of the truth, summed over a group's lines "
        "(corpus rate); **WER** the same over words. Text is compared after Unicode NFC, case "
        "folding and collapsing white space; punctuation counts. An engine with no reading for a "
        "line has read nothing. CER can exceed 100 % (insertions).",
        "- **Selector** = the production selector on the engines' readings of the truth boxes; "
        "**@ true class** gives it the class the person wrote; **oracle** = the best reading of "
        "each line, an upper bound.",
        "- **95 % interval** = bootstrap over pages (1000 resamples, fixed seed), shown from 5 "
        "pages. Weights, flag-threshold and engine-removal tables are in-sample: they describe "
        "this set and are not held-out error rates.",
        "- Lexicon term of the selector: English word list only (no question glossary: a ground-"
        "truth page has no booklet or exam).",
    ]
