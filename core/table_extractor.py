"""
core/table_extractor.py — turns a handwritten/scanned TABLE image into the
structured grid used by core/table_evaluator.py, the same way
core/diagram_extractor.py turns a diagram image into a {nodes, edges} graph.

SCHEMA returned by extract_table_structure() (documented here for the same
reason core/diagram_extractor.py documents its {nodes, edges} schema — this
docstring is the contract, and scripts/load_reference_table.py validates
hand-authored reference tables against the same shape):

    {"schema_version": 1,
     "rows": int,                 # grid rows, INCLUDING the header row
     "cols": int,
     "has_header": bool,          # see _infer_header() for the heuristic
     "cells": [{"row": int,       # 0-based, row 0 is the header when has_header
                "col": int,       # 0-based
                "text": str,      # "" when nothing was read (see below)
                "confidence": float,        # 0.0-1.0, the winning engine's
                "bbox": [x, y, w, h],       # cell rect in image pixels
                "ocr_engine": str | None},  # which engine's read won
               ...],
     "detection_method": "ruled" | "whitespace",
     "extraction_warnings": [str, ...]}

`cells` is dense: every (row, col) of the detected grid gets an entry, so a
consumer can index it as a grid without holes. A cell whose OCR returned
nothing is text="" at confidence 0.0 — and, exactly as with a missing
diagram edge (core/diagram_extractor.py's own warning text), that means
"nothing was reliably read here", NOT "the student left this cell blank".
core/table_evaluator.py treats it as an unscored miss and says so in the
verdict rather than asserting the student omitted the value.

TWO DETECTION PATHS, tried in order:

1. RULED LINES (detection_method="ruled") — the normal case. Binarizes via
   core/diagram_shapes.binarize() (shared OTSU helper; see its docstring for
   why OTSU and not adaptive thresholding), then isolates long horizontal
   and vertical strokes with two morphological opens using a long-and-thin
   kernel per axis — the standard erode-then-dilate line-extraction idiom,
   here reusing the same cv2 primitives core/diagram_shapes.py already
   builds on. Row/column boundaries are the projection peaks of those two
   masks; their cross product is the cell grid, so "intersect the two line
   sets to find cell boundaries" happens implicitly at the projection
   level rather than by hunting pixel intersections (equivalent result,
   and it degrades gracefully when a line is locally broken instead of
   losing the whole intersection).

   A JITTER-ABSORBING DILATION is applied to each mask perpendicular to its
   own axis before projecting (PERP_DILATE_PX). This is load-bearing, not
   polish: a real ruling line is never perfectly axis-aligned, and a line
   that drifts 2px over its length spreads its ink across several
   projection rows, so no single row reaches the "a full-width line lives
   here" threshold and the line is missed entirely. Measured on
   media/tables/images/ during development: without it, 4 of 9 bordered
   fixtures lost at least one ruling line (and therefore a whole row or
   column); with it, all 9 resolve every line.

2. WHITESPACE PROJECTION (detection_method="whitespace") — the borderless
   fallback, used when path 1 finds fewer than 2 lines on either axis (a
   table needs 2 lines per axis to bound even one cell). Ink is projected
   onto each axis and the runs of ink are segmented at the gaps between
   them, with a per-axis minimum gap so the space BETWEEN WORDS in one cell
   ("Round Robin") isn't mistaken for the space between two columns. This
   is why MIN_COL_GAP_PX is much larger than MIN_ROW_GAP_PX: inter-word
   gaps compete with inter-column gaps on the x axis, and nothing competes
   with inter-row gaps on the y axis.

OCR: every cell crop goes through the EXISTING core/ocr_fallback.py
multi-engine ensemble (PaddleOCR + Tesseract, best-confidence read wins per
crop) — the same framework and the same FallbackOCR entry point
core/diagram_extractor.py uses per label region. No new OCR dependency is
introduced, and each cell records which engine won under "ocr_engine",
matching how a diagram node records the same thing.

WHY HAND-ROLLED OPENCV, not PP-Structure or img2table (evaluated on
media/tables/ before this module was written — see
scripts/generate_table_benchmark.py for the fixture set, and CLAUDE_CONTEXT
for the numbers):

  approach                            shape ok   cell acc   avg time
  this module (OpenCV + FallbackOCR)  12/12      0.945      3.4 s
  PaddleOCR PP-StructureV3            11/12      0.847      48.2 s
  img2table 2.0 (Tesseract backend)   12/12      0.842      0.16 s

  PP-StructureV3 needs the paddlex[ocr] extras plus ~10 additional model
  checkpoints, and on this project's pinned paddlepaddle 3.3.1 CPU build it
  CRASHES on its default oneDNN path (NotImplementedError deep in
  onednn_instruction.cc) unless constructed with enable_mkldnn=False — a
  platform-specific workaround this repo would then own. img2table requires
  opencv-contrib-python (cv2.ximgproc.niBlackThreshold); the venv here
  resolves cv2 to opencv-python-headless, where every img2table call fails
  outright, so adopting it means changing the OpenCV distribution the
  working diagram pipeline already runs on. Both also bring their OWN OCR
  and would bypass core/ocr_fallback.py's ensemble entirely.

  HONEST CAVEAT ON THOSE NUMBERS: the fixtures are synthetic and rendered by
  this repo, and this module's thresholds were tuned against them, while the
  two libraries were run untuned. The accuracy gap is therefore NOT evidence
  that hand-rolled OpenCV generalizes better — on real photographs with
  perspective, skew, ruled notebook paper or spanning cells, a learned table
  model would plausibly win, and the honest reading of the table above is
  "all three grid this fixture set correctly; the differences are OCR
  backend and operational cost." The decision rests on dependency cost and
  on reusing this repo's own OCR ensemble, not on 0.945 vs 0.847.
  core/plugins/registry.py is what makes this reversible: a PP-Structure
  based extractor is a second plugin, not a rewrite.

CONFIRMED SCOPE — same posture as core/diagram_shapes.py's. Validated only
against flat, axis-aligned, single-table images with non-spanning cells
(media/tables/images/, 3 handwriting styles x 4 layout variants). NOT
validated, and expected to be unreliable, on: rowspan/colspan cells (the
grid model here has no concept of them), multiple tables in one image (only
one grid is returned), rotated/perspective-skewed photos (no deskew step),
and tables drawn on RULED NOTEBOOK PAPER — the last is this project's real
handwriting input distribution and is the known hard case, since notebook
rules are full-width horizontal lines that path 1 cannot distinguish from a
table's own ruling lines by morphology alone. See
scripts/generate_table_benchmark.py's docstring for why no fixture for that
case was fabricated.
"""
from __future__ import annotations

import numpy as np
import cv2

from core import diagram_shapes
from core.ocr_fallback import lazy_fallback_ocr

SCHEMA_VERSION = 1

# --- ruled-line detection ----------------------------------------------

#: The line-extraction kernel for each axis is 1/this of the image's extent
#: along that axis. Long enough that a stroke must span a real fraction of
#: the table to survive the erode (letterforms and short marks don't),
#: short enough that a line broken by a crossing stroke still survives in
#: pieces. 15 tracks the value diagram-adjacent OpenCV table work commonly
#: uses; verified against every fixture in media/tables/images/.
LINE_KERNEL_DIVISOR = 15

#: A projection peak must reach this fraction of the axis's full extent to
#: count as a ruling line. Below ~0.4 a dense row of handwriting starts
#: competing with a real line; above ~0.6 a line that stops short of the
#: table edge is lost.
LINE_PROJECTION_RATIO = 0.5

#: Perpendicular dilation applied to each line mask before projecting, to
#: absorb a hand-drawn/scanned line's drift across a few px. See the module
#: docstring — without this, drifting lines are missed outright.
PERP_DILATE_PX = 5

#: Projection peaks closer together than this are one line (a thick or
#: doubled stroke), not two.
LINE_MERGE_GAP_PX = 8

# --- borderless (whitespace projection) fallback -----------------------

#: A pixel darker than this counts as ink for whitespace projection. A
#: fixed cutoff rather than the OTSU mask: OTSU is tuned to separate ink
#: from paper for LINE work, and on a borderless table (no lines at all)
#: its threshold is set entirely by text, which makes faint strokes drop
#: out exactly where a column boundary needs them.
INK_LEVEL = 160

#: Minimum blank-column run that separates two COLUMNS. Much larger than
#: MIN_ROW_GAP_PX because inter-word whitespace inside a single cell
#: ("Round Robin") competes with it on this axis and must not win.
MIN_COL_GAP_PX = 25

#: Minimum blank-row run that separates two ROWS. Nothing inside a cell
#: competes on this axis, so it only has to clear intra-glyph gaps.
MIN_ROW_GAP_PX = 12

#: Whitespace-projected bounds are padded outward by this much so a
#: descender/ascender clipped by the ink run isn't cropped away.
WHITESPACE_PAD_PX = 4

# --- cell cropping / OCR ------------------------------------------------

#: A detected cell smaller than this on either axis is dropped as a
#: detection artifact (two ruling lines a few px apart) rather than OCR'd.
MIN_CELL_PX = 12

#: Pixels trimmed off each side of a cell rect before OCR, so the cell's
#: own ruling lines aren't fed to the recognizer as if they were glyph
#: strokes. Inset (not padded) — the inverse of
#: core/diagram_extractor.REGION_CROP_PADDING_PX, which pads a detected
#: TEXT box outward because there is no border there to exclude.
CELL_INSET_PX = 4

#: This module's own lazily-built FallbackOCR. Deliberately NOT the instance
#: core/diagram_extractor.py uses — see core/ocr_fallback.lazy_fallback_ocr()
#: for why sharing one would be a concurrency bug, not a saving.
_get_fallback_ocr = lazy_fallback_ocr()


def stub_extract(source=None) -> dict:
    """Deterministic fake extraction — no image fetch, no model load. Cell
    text is "[STUB]"-prefixed so it is obvious in output and logs that this
    is not a real reading of an image, matching
    core/diagram_extractor.stub_extract()'s convention. The grid is shaped
    to align cleanly against media/tables/reference_table.json so a
    --stub-extraction end-to-end run still exercises real alignment and
    scoring rather than degenerating to "nothing matched"."""
    grid = [
        ["[STUB] Algorithm", "[STUB] Preemptive", "[STUB] Avg Wait"],
        ["[STUB] FCFS", "[STUB] No", "8.0"],
        ["[STUB] SJF", "[STUB] No", "5.5"],
    ]
    cells = [
        {"row": r, "col": c, "text": text, "confidence": 1.0,
         "bbox": [c * 100, r * 50, 100, 50], "ocr_engine": None}
        for r, row in enumerate(grid)
        for c, text in enumerate(row)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "rows": len(grid), "cols": len(grid[0]), "has_header": True,
        "cells": cells,
        "detection_method": "stub",
        "extraction_warnings": [],
    }


def _merge_positions(indices: list[int], gap: int) -> list[int]:
    """Collapses a sorted list of qualifying projection indices into one
    position per run, using each run's midpoint."""
    groups: list[int] = []
    current: list[int] = []
    for i in indices:
        if current and i - current[-1] > gap:
            groups.append(sum(current) // len(current))
            current = []
        current.append(i)
    if current:
        groups.append(sum(current) // len(current))
    return groups


def detect_ruling_lines(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Finds the y positions of horizontal ruling lines and the x positions
    of vertical ones. Returns (row_positions, col_positions), each sorted
    and de-duplicated; either may be empty (a borderless table)."""
    binary = diagram_shapes.binarize(gray)
    height, width = binary.shape

    h_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(10, width // LINE_KERNEL_DIVISOR), 1))
    v_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, max(10, height // LINE_KERNEL_DIVISOR)))

    horizontal = cv2.dilate(cv2.erode(binary, h_kernel), h_kernel)
    vertical = cv2.dilate(cv2.erode(binary, v_kernel), v_kernel)

    # Absorb line drift perpendicular to each line's own axis — see the
    # module docstring; this is what makes a slightly skewed line project
    # as one strong peak instead of several weak ones.
    horizontal = cv2.dilate(
        horizontal, cv2.getStructuringElement(cv2.MORPH_RECT, (1, PERP_DILATE_PX)))
    vertical = cv2.dilate(
        vertical, cv2.getStructuringElement(cv2.MORPH_RECT, (PERP_DILATE_PX, 1)))

    row_positions = _merge_positions(
        [i for i, v in enumerate(horizontal.sum(axis=1) / 255)
         if v >= width * LINE_PROJECTION_RATIO],
        LINE_MERGE_GAP_PX,
    )
    col_positions = _merge_positions(
        [i for i, v in enumerate(vertical.sum(axis=0) / 255)
         if v >= height * LINE_PROJECTION_RATIO],
        LINE_MERGE_GAP_PX,
    )
    return row_positions, col_positions


def _ink_runs(projection: np.ndarray, min_gap: int) -> list[tuple[int, int]]:
    """Contiguous (start, end) runs of ink in a 1-D projection, with runs
    separated by less than `min_gap` merged into one."""
    runs: list[list[int]] = []
    start = None
    for i, value in enumerate(projection):
        if value > 0 and start is None:
            start = i
        elif value == 0 and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(projection)])
    if not runs:
        return []

    merged = [runs[0]]
    for run_start, run_end in runs[1:]:
        if run_start - merged[-1][1] < min_gap:
            merged[-1][1] = run_end
        else:
            merged.append([run_start, run_end])
    return [(s, e) for s, e in merged]


def _bounds_from_runs(runs: list[tuple[int, int]], limit: int) -> list[int]:
    """Turns ink runs into the boundaries BETWEEN them: the outer edges are
    the first/last run padded outward, and each internal boundary is the
    midpoint of the gap separating two runs. Returns len(runs)+1 positions,
    so len(runs) cells fall between them."""
    if not runs:
        return []
    bounds = [max(0, runs[0][0] - WHITESPACE_PAD_PX)]
    for current, following in zip(runs, runs[1:]):
        bounds.append((current[1] + following[0]) // 2)
    bounds.append(min(limit, runs[-1][1] + WHITESPACE_PAD_PX))
    return bounds


def detect_whitespace_grid(gray: np.ndarray) -> tuple[list[int], list[int]]:
    """Borderless fallback: derives row and column boundaries from where the
    ink ISN'T. Returns (row_bounds, col_bounds) — boundary positions, not
    line positions, but consumed identically by _build_grid()."""
    ink = (gray < INK_LEVEL).astype(np.uint8)
    height, width = ink.shape
    col_bounds = _bounds_from_runs(_ink_runs(ink.sum(axis=0), MIN_COL_GAP_PX), width)
    row_bounds = _bounds_from_runs(_ink_runs(ink.sum(axis=1), MIN_ROW_GAP_PX), height)
    return row_bounds, col_bounds


def _is_numeric(text: str) -> bool:
    """Whether `text` reads as a plain number. Shared shape with
    core/table_evaluator.parse_number() but deliberately NOT that function:
    this only asks a yes/no question about header inference and must not
    grow the evaluator's tolerance semantics."""
    try:
        float(text.strip().replace(",", ""))
    except (ValueError, AttributeError):
        return False
    return True


def _infer_header(cells: list[dict], rows: int) -> tuple[bool, str]:
    """Heuristic header detection. Returns (has_header, reason) — the reason
    is carried into extraction_warnings when the inference is weak, because
    "row 0 is a header" silently decides how core/table_evaluator.py aligns
    every column, and a caller deserves to know when that came from a guess.

    Rules, in order:
      - Fewer than 2 rows: no header (a single row is the data).
      - Any numeric cell in row 0: NOT a header. A header cell is a name;
        a row of numbers is data that happens to be first.
      - A numeric cell in any later row: header. This is the strong signal —
        non-numeric first row over numeric body is what a header looks like.
      - Otherwise: header, but WEAKLY inferred. An all-text table gives no
        positive evidence either way; row 0 is assumed to be the header
        because that is overwhelmingly the convention in exam tables, and
        the assumption is reported rather than hidden.
    """
    if rows < 2:
        return False, "single-row grid — no header row to distinguish"

    header_cells = [c for c in cells if c["row"] == 0 and c["text"].strip()]
    body_cells = [c for c in cells if c["row"] > 0 and c["text"].strip()]

    if any(_is_numeric(c["text"]) for c in header_cells):
        return False, "row 0 contains numeric value(s) — reads as data, not a header"
    if any(_is_numeric(c["text"]) for c in body_cells):
        return True, "row 0 is all non-numeric over numeric body rows"
    return True, "weak inference — no numeric cells anywhere to contrast row 0 against"


def _build_grid(pil_image, row_bounds: list[int], col_bounds: list[int],
                 ocr) -> tuple[list[dict], list[str]]:
    """OCRs every cell rect bounded by consecutive row/col boundaries.
    Returns (cells, warnings). Cells are emitted with CONTIGUOUS row/col
    indices, so dropping an undersized rect doesn't leave a hole in the
    grid that a consumer would read as a missing cell."""
    cells: list[dict] = []
    warnings: list[str] = []
    empty_cells: list[str] = []

    kept_rows = [(top, bottom) for top, bottom in zip(row_bounds, row_bounds[1:])
                 if bottom - top >= MIN_CELL_PX]
    kept_cols = [(left, right) for left, right in zip(col_bounds, col_bounds[1:])
                 if right - left >= MIN_CELL_PX]

    dropped = (len(row_bounds) - 1 - len(kept_rows)) + (len(col_bounds) - 1 - len(kept_cols))
    if dropped:
        warnings.append(
            f"Dropped {dropped} sub-{MIN_CELL_PX}px band(s) between detected "
            f"boundaries as detection artifacts (doubled/thick ruling lines), "
            f"not as real rows/columns."
        )

    for row_index, (top, bottom) in enumerate(kept_rows):
        for col_index, (left, right) in enumerate(kept_cols):
            crop = pil_image.crop((
                left + CELL_INSET_PX, top + CELL_INSET_PX,
                max(left + CELL_INSET_PX + 1, right - CELL_INSET_PX),
                max(top + CELL_INSET_PX + 1, bottom - CELL_INSET_PX),
            ))
            result = ocr.recognize(crop)
            text = (result.text or "").strip()
            if not text:
                empty_cells.append(f"({row_index},{col_index})")
            cells.append({
                "row": row_index,
                "col": col_index,
                "text": text,
                "confidence": float(result.confidence) if text and result.confidence is not None else 0.0,
                "bbox": [int(left), int(top), int(right - left), int(bottom - top)],
                "ocr_engine": result.engine if text else None,
            })

    if empty_cells:
        warnings.append(
            f"{len(empty_cells)} cell(s) returned no text ({', '.join(empty_cells)}). "
            f"An empty cell means 'nothing was reliably read here', NOT 'the student "
            f"left this cell blank' — same convention as an undetected diagram edge "
            f"(core/diagram_extractor.py). Route these to review rather than scoring "
            f"them as confirmed omissions."
        )
    return cells, warnings


def extract_table_structure(image) -> dict:
    """Real image -> table grid extraction. See the module docstring for the
    returned schema, the two detection paths, and the validated scope.

    `image` may be either:
      - a PIL Image (the documented primary form), or
      - a str, which is ALWAYS treated as a stored blob_url in this repo's
        stable "bucket/key" form and fetched via
        core/diagram_extractor._load_image() — the same str-means-blob_url
        convention core/plugins/text_extraction.py's extract() uses, so a
        caller holding a DB row doesn't have to fetch bytes itself.
    """
    if isinstance(image, str):
        from core.diagram_extractor import _load_image
        image = _load_image(image)

    gray = np.array(image.convert("L"))
    warnings: list[str] = []

    row_bounds, col_bounds = detect_ruling_lines(gray)
    detection_method = "ruled"
    if len(row_bounds) < 2 or len(col_bounds) < 2:
        # Fewer than 2 lines on an axis can't bound even one cell — this is
        # a borderless table (or one whose ruling is too faint to survive
        # the morphological open), so fall through to whitespace projection.
        warnings.append(
            f"Ruling-line detection found {len(row_bounds)} horizontal and "
            f"{len(col_bounds)} vertical line(s) — too few to bound a grid, so "
            f"the borderless whitespace-projection path was used instead. Column "
            f"boundaries from whitespace are less reliable than real ruling lines: "
            f"a cell whose text nearly fills its column can merge with its "
            f"neighbour."
        )
        row_bounds, col_bounds = detect_whitespace_grid(gray)
        detection_method = "whitespace"

    if len(row_bounds) < 2 or len(col_bounds) < 2:
        warnings.append(
            "Neither ruling lines nor whitespace projection produced a usable "
            "grid — returning an empty table rather than guessing at one."
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "rows": 0, "cols": 0, "has_header": False, "cells": [],
            "detection_method": detection_method,
            "extraction_warnings": warnings,
        }

    cells, grid_warnings = _build_grid(image, row_bounds, col_bounds, _get_fallback_ocr())
    warnings.extend(grid_warnings)

    rows = max((c["row"] for c in cells), default=-1) + 1
    cols = max((c["col"] for c in cells), default=-1) + 1

    has_header, header_reason = _infer_header(cells, rows)
    if "weak" in header_reason:
        warnings.append(
            f"has_header={has_header} is a {header_reason}. Column alignment in "
            f"core/table_evaluator.py matches header cells first, so if this is "
            f"wrong the whole grid may align by position instead."
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "rows": rows,
        "cols": cols,
        "has_header": has_header,
        "cells": cells,
        "detection_method": detection_method,
        "extraction_warnings": warnings,
    }
