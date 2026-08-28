"""
scripts/benchmark_ocr_engines.py — scores every registered OCR engine
plugin (core/ocr_engines/) against the 10-handwriting-style benchmark set
(scripts/generate_ocr_benchmark.py) and reports per-style, per-engine
accuracy, plus how core/ocr_fallback.py's "ensemble" selection heuristic
performs versus each individual engine.

This is the empirical half of the fallback-OCR framework: core/ocr_fallback
.py's module docstring is explicit that ranking engines by raw self-reported
confidence is an unproven heuristic (different engines' confidence scales
aren't calibrated against each other) — this script is what actually checks
whether that heuristic picks the more accurate engine, per style, on a
labeled set. Re-run it after adding a new engine plugin, changing an
engine's model/config, or before trusting `strategy="ensemble"` in
production.

Metric: normalized character-level accuracy = 1 - (Levenshtein distance /
len(ground truth)), clamped to [0, 1] — i.e. character error rate (CER)
subtracted from 1. Chosen over exact-match because handwriting OCR rarely
gets every character right even when the read is clearly "close enough";
CER gives a continuous signal instead of a binary pass/fail per style.

Usage:
    python scripts/generate_ocr_benchmark.py   # once, or after adding fonts
    python scripts/benchmark_ocr_engines.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from PIL import Image

BENCHMARK_DIR = pathlib.Path(__file__).resolve().parent.parent / "media" / "ocr_benchmark"
IMAGES_DIR = BENCHMARK_DIR / "images"
GROUND_TRUTH_PATH = BENCHMARK_DIR / "ground_truth.json"


def _levenshtein(a: str, b: str) -> int:
    """Standard O(len(a)*len(b)) edit distance, no external dependency —
    the benchmark set is 10 short two-line strings, so this doesn't need
    to be fast."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                prev[j] + 1,      # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr
    return prev[-1]


def char_accuracy(hypothesis: str, reference: str) -> float:
    """1 - CER, clamped to [0, 1]. A hypothesis much longer than the
    reference (e.g. an engine hallucinating extra words) can otherwise push
    CER above 1.0 — clamped so "accuracy" stays a sane percentage."""
    if not reference:
        return 1.0 if not hypothesis else 0.0
    distance = _levenshtein(hypothesis.strip().lower(), reference.strip().lower())
    return max(0.0, 1.0 - distance / len(reference))


def _load_ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.exists():
        raise SystemExit(
            f"{GROUND_TRUTH_PATH} not found — run "
            f"scripts/generate_ocr_benchmark.py first."
        )
    return json.loads(GROUND_TRUTH_PATH.read_text())


def main() -> None:
    from core.ocr_fallback import FallbackOCR, default_engines

    ground_truth = _load_ground_truth()
    engines = default_engines()
    fallback = FallbackOCR(engines=engines)

    # style -> {engine_name: accuracy, ..., "ensemble": accuracy}
    rows: dict[str, dict[str, float | None]] = {}
    engine_names = [e.name for e in engines]
    skipped_engines: set[str] = set()

    for style, info in sorted(ground_truth.items()):
        # recognize() is scored per single-line crop (line1/line2), not the
        # combined full image — that's the calling convention every engine
        # is actually written for (see this script's docstring). Per-style
        # accuracy is the mean of both lines' accuracy.
        lines = [
            (info["line1_image"], info["line_1"]),
            (info["line2_image"], info["line_2"]),
        ]

        row: dict[str, float | None] = {}
        engine_line_scores: dict[str, list[float]] = {e.name: [] for e in engines}
        ensemble_line_scores: list[float] = []
        ensemble_engines_picked: list[str] = []

        for filename, reference in lines:
            image = Image.open(IMAGES_DIR / filename).convert("RGB")

            for engine in engines:
                try:
                    text, _confidence = engine.recognize(image)
                except Exception as exc:
                    if engine.name not in skipped_engines:
                        print(f"[skip] engine {engine.name!r} unavailable: {exc}")
                        skipped_engines.add(engine.name)
                    continue
                engine_line_scores[engine.name].append(char_accuracy(text, reference))

            ensemble_result = fallback.recognize(image, strategy="ensemble")
            ensemble_line_scores.append(char_accuracy(ensemble_result.text, reference))
            ensemble_engines_picked.append(ensemble_result.engine)

        for engine in engines:
            scores = engine_line_scores[engine.name]
            row[engine.name] = round(sum(scores) / len(scores), 4) if scores else None
        row["ensemble"] = (
            round(sum(ensemble_line_scores) / len(ensemble_line_scores), 4)
            if ensemble_line_scores else None
        )
        row["ensemble_engine"] = "/".join(ensemble_engines_picked)

        rows[style] = row

    _print_report(rows, engine_names)


def _print_report(rows: dict[str, dict], engine_names: list[str]) -> None:
    columns = engine_names + ["ensemble"]
    header = f"{'style':<20}" + "".join(f"{c:>14}" for c in columns) + "  ensemble_picked"
    print(header)
    print("-" * len(header))

    totals = {c: [] for c in columns}
    for style, row in rows.items():
        cells = []
        for c in columns:
            value = row.get(c)
            cells.append(f"{value:>14.4f}" if value is not None else f"{'n/a':>14}")
            if value is not None:
                totals[c].append(value)
        picked = row.get("ensemble_engine", "?")
        print(f"{style:<20}" + "".join(cells) + f"  {picked}")

    print("-" * len(header))
    avg_cells = []
    for c in columns:
        values = totals[c]
        avg_cells.append(f"{(sum(values) / len(values)):>14.4f}" if values else f"{'n/a':>14}")
    print(f"{'AVERAGE':<20}" + "".join(avg_cells))

    best_single = max(
        ((c, sum(v) / len(v)) for c, v in totals.items() if v and c != "ensemble"),
        key=lambda kv: kv[1],
        default=None,
    )
    ensemble_avg = (sum(totals["ensemble"]) / len(totals["ensemble"])) if totals["ensemble"] else None
    if best_single and ensemble_avg is not None:
        print(
            f"\nBest single engine: {best_single[0]} ({best_single[1]:.4f}) — "
            f"ensemble: {ensemble_avg:.4f} "
            f"({'better' if ensemble_avg > best_single[1] else 'not better'} than best single engine)"
        )


if __name__ == "__main__":
    main()
