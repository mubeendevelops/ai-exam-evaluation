"""
scripts/generate_table_benchmark.py — builds the synthetic table benchmark
set in media/tables/, the fixture base for core/table_extractor.py and
core/table_evaluator.py (there are no real table scans in this repo).

Mirrors scripts/generate_ocr_benchmark.py: renders a fixed ground-truth
grid once per handwriting-style font from media/ocr_benchmark/fonts/ (the
same 10 real Google Fonts handwriting families that benchmark already
uses — NOT synthetic jitter), writes the images to media/tables/images/,
and writes an exact ground_truth.json mapping every filename to the cells
it was rendered from. The same provenance argument applies verbatim: this
repo has no real handwritten-table corpus with known ground truth, and
fabricating "real" scans would misrepresent where the numbers came from.
Rendering the SAME grid in several real handwriting *font* styles varies
slant, stroke shape and spacing the way different people's handwriting
does, while keeping ground truth exact.

FOUR LAYOUT VARIANTS are rendered per style, each targeting one code path
or scoring behaviour that would otherwise have no fixture:

  bordered      full ruling grid, every reference row present. The happy
                path for core/table_extractor.py's morphological line
                detection, and the "student got it exactly right" case.
  borderless    identical content, NO ruling lines at all — the only
                fixture that exercises the whitespace-column-projection
                fallback path.
  missing_row   bordered, one body row (SJF) omitted — the row-alignment
                case core/table_evaluator.py exists for: a student table
                whose row i is NOT the reference's row i.
  shifted       bordered, every row present but body rows reordered, plus
                a misspelled header ("Preemptive" -> "Preemtive"), a
                substantively wrong number ("8.0" -> "8.5") and a
                rounding-level one ("6.2" -> "6.25"). Deliberately hits ALL
                FOUR of core/table_evaluator.py's per-cell verdicts — exact,
                fuzzy, numeric_close, miss — plus align-by-leading-cell, in
                one image, so the end-to-end demo produces a per-cell verdict
                grid with MIXED verdicts rather than an all-green or all-red
                one.

media/tables/reference_table.json is written alongside them: the same
canonical grid in the hand-authored input format scripts/load_reference_table.py
takes (cells with no bbox/confidence), exactly as reference diagrams are
hand-authored JSON rather than extracted (plan.md §4.4).

LINE JITTER: ruling lines are drawn with their endpoints jittered by up to
LINE_JITTER_PX from a per-image SEEDED random, so a perfectly axis-aligned
1px grid doesn't flatter morphological line detection in a way a real scan
never would. The seed is derived from the filename, so regenerating this
set is byte-identical — a benchmark that moves under you isn't one.

KNOWN GAP, deliberately not generated: a table drawn on RULED NOTEBOOK
PAPER, which is this project's real input distribution for handwriting
(see core/diagram_extractor.py's module docstring on why plain-OpenCV
detection failed there). Notebook rules are horizontal lines spanning the
full page width and are essentially indistinguishable from a table's own
horizontal ruling lines by morphology alone, so that variant would be a
genuinely hard, separate problem rather than a harder version of this one.
Treat scores from this set as a comparison signal between extraction
approaches, not as a real-world accuracy number.

Usage:
    python scripts/generate_table_benchmark.py              # 3 styles x 4 variants
    python scripts/generate_table_benchmark.py --all-styles # all 10 fonts
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from scripts.generate_ocr_benchmark import FONTS_DIR, select_fonts  # noqa: E402

TABLES_DIR = REPO_ROOT / "media" / "tables"
IMAGES_DIR = TABLES_DIR / "images"
GROUND_TRUTH_PATH = TABLES_DIR / "ground_truth.json"
REFERENCE_TABLE_PATH = TABLES_DIR / "reference_table.json"

# The canonical grid every variant is derived from — row 0 is the header.
# Deliberately mixes short text, a two-word cell, a yes/no column and a
# decimal numeric column: the numeric column is what gives
# core/table_evaluator.py's numeric-tolerance verdict something to score,
# and "Round Robin" is the multi-word cell that makes whitespace-column
# projection non-trivial (a naive space-splitter would break it in two).
REFERENCE_GRID = [
    ["Algorithm", "Preemptive", "Avg Wait"],
    ["FCFS", "No", "8.0"],
    ["SJF", "No", "5.5"],
    ["Round Robin", "Yes", "6.2"],
]

#: Default subset of media/ocr_benchmark/fonts/ to render. Three styles
#: rather than all ten by default: this set is committed to the repo, and
#: 4 variants x 10 styles is 40 images of fixture for a benchmark whose
#: per-style spread is already measured by the OCR benchmark next door.
#: --all-styles renders the full ten when that spread is what's being
#: measured.
DEFAULT_STYLES = ("PatrickHand", "Caveat", "IndieFlower")

FONT_SIZE = 34
COL_WIDTHS = (280, 230, 190)
ROW_HEIGHT = 74
MARGIN = 40
CELL_PAD_X = 18
TEXT_COLOR = (20, 20, 30)
BACKGROUND_COLOR = (255, 255, 250)
LINE_COLOR = (40, 40, 55)
LINE_WIDTH = 2

#: Max px a ruling line's endpoint is displaced from true. Small enough
#: that a horizontal line stays horizontal within the angle tolerance any
#: line detector uses, large enough that the grid isn't pixel-perfect.
LINE_JITTER_PX = 2


def _variant_grid(variant: str) -> tuple[list[list[str]], list[str]]:
    """Returns (grid, notes) for one variant — `notes` describes how it
    differs from REFERENCE_GRID, and is carried into ground_truth.json so a
    reader doesn't have to diff two grids by eye to know what's expected."""
    if variant in ("bordered", "borderless"):
        return [row[:] for row in REFERENCE_GRID], []
    if variant == "missing_row":
        grid = [row[:] for row in REFERENCE_GRID if row[0] != "SJF"]
        return grid, ["body row 'SJF' omitted"]
    if variant == "shifted":
        header, fcfs, sjf, rr = ([row[:] for row in REFERENCE_GRID])
        header[1] = "Preemtive"      # misspelling -> fuzzy-match verdict
        fcfs[2] = "8.5"               # substantively wrong number -> miss verdict
        rr[2] = "6.25"                # rounding-level difference -> numeric_close verdict
        return [header, sjf, fcfs, rr], [
            "body rows reordered (SJF before FCFS)",
            "header cell 'Preemptive' misspelled 'Preemtive'",
            "cell FCFS/Avg Wait perturbed 8.0 -> 8.5 (substantively wrong)",
            "cell Round Robin/Avg Wait perturbed 6.2 -> 6.25 (rounding-level)",
        ]
    raise ValueError(f"unknown variant {variant!r}")


VARIANTS = ("bordered", "borderless", "missing_row", "shifted")
BORDERLESS_VARIANTS = frozenset({"borderless"})


def _jittered_line(draw: ImageDraw.ImageDraw, rng: random.Random,
                    x1: int, y1: int, x2: int, y2: int) -> None:
    def j(v: int) -> int:
        return v + rng.randint(-LINE_JITTER_PX, LINE_JITTER_PX)

    draw.line([(j(x1), j(y1)), (j(x2), j(y2))], fill=LINE_COLOR, width=LINE_WIDTH)


def render_table(font_path: pathlib.Path, style: str, variant: str) -> dict:
    """Renders one (style, variant) image and returns its ground-truth
    entry. Raises if any cell's rendered text overflows its column — a
    fixture whose text runs into the next column would make every
    extractor look wrong for the generator's mistake."""
    grid, notes = _variant_grid(variant)
    n_rows, n_cols = len(grid), len(grid[0])

    width = MARGIN * 2 + sum(COL_WIDTHS)
    height = MARGIN * 2 + ROW_HEIGHT * n_rows
    image = Image.new("RGB", (width, height), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), FONT_SIZE)

    filename = f"{style}_{variant}.png"
    rng = random.Random(filename)  # seeded per image -> byte-stable regeneration

    col_x = [MARGIN]
    for w in COL_WIDTHS:
        col_x.append(col_x[-1] + w)
    row_y = [MARGIN + ROW_HEIGHT * i for i in range(n_rows + 1)]

    if variant not in BORDERLESS_VARIANTS:
        for y in row_y:
            _jittered_line(draw, rng, col_x[0], y, col_x[n_cols], y)
        for x in col_x[:n_cols + 1]:
            _jittered_line(draw, rng, x, row_y[0], x, row_y[n_rows])

    cells = []
    for r, row in enumerate(grid):
        for c, text in enumerate(row):
            tx = col_x[c] + CELL_PAD_X
            ty = row_y[r] + (ROW_HEIGHT - FONT_SIZE) // 2 - 4
            draw.text((tx, ty), text, font=font, fill=TEXT_COLOR)

            text_width = draw.textlength(text, font=font)
            if text_width > COL_WIDTHS[c] - CELL_PAD_X * 2:
                raise SystemExit(
                    f"{filename}: cell ({r},{c}) {text!r} renders {text_width:.0f}px "
                    f"wide but column {c} only allows "
                    f"{COL_WIDTHS[c] - CELL_PAD_X * 2}px — widen COL_WIDTHS or "
                    f"shorten the cell, don't ship a fixture whose text overlaps."
                )
            cells.append({"row": r, "col": c, "text": text})

    path = IMAGES_DIR / filename
    image.save(path)
    print(f"rendered {path.relative_to(REPO_ROOT)}")

    return {
        "style": style,
        "variant": variant,
        "image": filename,
        "bordered": variant not in BORDERLESS_VARIANTS,
        "rows": n_rows,
        "cols": n_cols,
        "has_header": True,
        "cells": cells,
        "differs_from_reference": notes,
    }


def write_reference_table() -> None:
    """Writes the canonical grid in scripts/load_reference_table.py's
    hand-authored input format (no bbox/confidence — a reference is
    authored, not extracted; same split as reference diagrams)."""
    reference = {
        "rows": len(REFERENCE_GRID),
        "cols": len(REFERENCE_GRID[0]),
        "has_header": True,
        "cells": [
            {"row": r, "col": c, "text": text}
            for r, row in enumerate(REFERENCE_GRID)
            for c, text in enumerate(row)
        ],
    }
    REFERENCE_TABLE_PATH.write_text(json.dumps(reference, indent=2) + "\n")
    print(f"wrote {REFERENCE_TABLE_PATH.relative_to(REPO_ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--all-styles", action="store_true",
                    help=f"render every font in {FONTS_DIR.name}/ instead of "
                         f"the default {len(DEFAULT_STYLES)} ({', '.join(DEFAULT_STYLES)})")
    args = ap.parse_args()

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    font_paths = select_fonts(None if args.all_styles else DEFAULT_STYLES)

    ground_truth = {}
    for font_path in font_paths:
        for variant in VARIANTS:
            entry = render_table(font_path, font_path.stem, variant)
            ground_truth[entry["image"]] = entry

    GROUND_TRUTH_PATH.write_text(json.dumps(ground_truth, indent=2) + "\n")
    print(f"\nwrote {GROUND_TRUTH_PATH.relative_to(REPO_ROOT)} "
          f"({len(ground_truth)} images)")
    write_reference_table()


if __name__ == "__main__":
    main()
