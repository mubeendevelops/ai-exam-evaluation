#!/usr/bin/env python3
"""
scripts/generate_booklet_benchmark.py — builds the synthetic answer-booklet
benchmark in media/booklets/, the fixture base for core/booklet_ingest.py and
core/booklet_segmenter.py.

WHY THIS EXISTS: this repo contains no PDF at all (`find . -name '*.pdf'`
returns nothing), so booklet ingestion had no input to be verified against.
Mirrors scripts/generate_ocr_benchmark.py / generate_table_benchmark.py /
generate_diagram_benchmark.py: renders fixed, known content using the same 10
real handwriting fonts in media/ocr_benchmark/fonts/, and writes an exact
ground_truth.json alongside. Offline fixture tooling — NOT part of the
production pipeline.

WHAT IS RENDERED — a 4-page booklet whose pages each target one code path
that would otherwise have no fixture:

  page 1  Q1 marker + two prose paragraphs.        Plain text segmentation
                                                   and the simplest possible
                                                   marker->region assignment.
  page 2  Q2 marker, prose, then a fully ruled     Mixed page: the case where
          table, then sub-part markers 2a / 2b.    one page holds regions of
                                                   two different block_types
                                                   AND sub-part markers.
  page 3  continuation prose with NO marker at     Cross-page assignment: these
          the top of the page.                     regions belong to the most
                                                   recent marker from page 2,
                                                   which is the whole reason
                                                   assignment is not per-page.
  page 4  Q3 marker + a box-and-arrow diagram,     Diagram classification, and
          then an unmarked region ABOVE the        the deliberate UNASSIGNED
          first marker (a stray header line).      case the report must flag
                                                   rather than guess at.

PAGE ROTATION: pages 2 and 4 are rendered rotated by a small fixed angle
(ROTATED_PAGES below) so core/booklet_ingest.py::deskew has something real to
correct. The angles are fixed, not random, so the fixture is reproducible.

DELIBERATE HONESTY LIMIT, stated here so its absence is never read as
coverage: these pages are RENDERED, not scanned. There is no perspective, no
ruled-paper bleed-through, no camera noise, and no ink that breaks mid-stroke.
CLAUDE_CONTEXT.md §7/§7B record that photographs of ruled notebook paper are
this project's real input distribution and its known hard case; this fixture
does NOT stand in for one, and numbers measured against it must not be quoted
as evidence about real booklets. No such fixture was fabricated, for the same
reason generate_table_benchmark.py declined to fabricate one.

Usage:
    python3 scripts/generate_booklet_benchmark.py
    python3 scripts/generate_booklet_benchmark.py --font PatrickHand --dpi 150
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

OUT_DIR = REPO_ROOT / "media" / "booklets"
FONT_DIR = REPO_ROOT / "media" / "ocr_benchmark" / "fonts"

# A4 at 150 DPI. The booklet is rendered as images and then bound into a PDF,
# rather than drawn as PDF vectors, because the thing under test is a SCAN
# pipeline — vector text would rasterize too cleanly to exercise anything.
PAGE_W, PAGE_H = 1240, 1754
MARGIN = 110

# Fixed, not random: a reproducible fixture. Page number -> degrees.
ROTATED_PAGES = {2: -1.6, 4: 1.1}

DEFAULT_FONT = "PatrickHand"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / f"{name}.ttf"
    if not path.exists():
        raise SystemExit(
            f"font not found: {path}\n"
            f"Available: {', '.join(sorted(p.stem for p in FONT_DIR.glob('*.ttf')))}"
        )
    return ImageFont.truetype(str(path), size)


def _wrap(draw, text, font, max_width):
    """Greedy word wrap to max_width pixels."""
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _draw_paragraph(draw, text, font, x, y, max_width, line_height):
    """Render wrapped text, returning the region bbox [x, y, w, h]."""
    lines = _wrap(draw, text, font, max_width)
    widest = 0
    for index, line in enumerate(lines):
        draw.text((x, y + index * line_height), line, font=font, fill=(20, 20, 30))
        widest = max(widest, int(draw.textlength(line, font=font)))
    return [x, y, widest, len(lines) * line_height]


def _draw_marker(draw, label, font, x, y):
    draw.text((x, y), label, font=font, fill=(15, 15, 40))
    width = int(draw.textlength(label, font=font))
    return [x, y, width, font.size + 6]


def _draw_table(draw, x, y, font, rows, cols, col_w, row_h, cells):
    """Fully ruled grid with its cell text. Returns the region bbox."""
    width, height = cols * col_w, rows * row_h
    for r in range(rows + 1):
        draw.line([(x, y + r * row_h), (x + width, y + r * row_h)], fill=(30, 30, 30), width=3)
    for c in range(cols + 1):
        draw.line([(x + c * col_w, y), (x + c * col_w, y + height)], fill=(30, 30, 30), width=3)
    for (r, c), text in cells.items():
        draw.text((x + c * col_w + 14, y + r * row_h + 10), text, font=font, fill=(20, 20, 30))
    return [x, y, width, height]


def _draw_diagram(draw, x, y, font, width, height):
    """Three labelled boxes joined by two arrows — the same shape
    media/diagram_benchmark/ renders, so the diagram class is recognizable."""
    box_w, box_h, gap = 250, 120, (width - 3 * 250) // 2
    boxes = []
    for index, label in enumerate(["Input", "Process", "Output"]):
        bx = x + index * (box_w + gap)
        draw.rectangle([bx, y, bx + box_w, y + box_h], outline=(25, 25, 25), width=4)
        text_w = int(draw.textlength(label, font=font))
        draw.text((bx + (box_w - text_w) // 2, y + box_h // 2 - font.size // 2),
                  label, font=font, fill=(20, 20, 30))
        boxes.append((bx, bx + box_w))
    for index in range(2):
        start_x, end_x = boxes[index][1], boxes[index + 1][0]
        mid_y = y + box_h // 2
        draw.line([(start_x, mid_y), (end_x, mid_y)], fill=(25, 25, 25), width=4)
        draw.polygon([(end_x, mid_y), (end_x - 16, mid_y - 9), (end_x - 16, mid_y + 9)],
                     fill=(25, 25, 25))
    return [x, y, width, max(box_h, height)]


def build_pages(font_name: str):
    """Render the 4 booklet pages. Returns (images, ground_truth_pages)."""
    body = _font(font_name, 40)
    marker = _font(font_name, 52)
    cell = _font(font_name, 34)

    images, truth = [], []

    # --- page 1: Q1 + two prose paragraphs ---------------------------------
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)
    regions = []
    y = MARGIN
    mb = _draw_marker(draw, "Q1.", marker, MARGIN, y)
    regions.append({"bbox": mb, "type": "marker", "marker": "Q1"})
    y += 90
    b = _draw_paragraph(draw, (
        "An operating system schedules processes so that the processor is never idle "
        "while runnable work exists. The scheduler chooses which ready process runs next."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q1"})
    y += b[3] + 70
    b = _draw_paragraph(draw, (
        "Preemptive scheduling allows the operating system to interrupt a running "
        "process, whereas non preemptive scheduling waits for it to yield voluntarily."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q1"})
    images.append(page)
    truth.append({"page_number": 1, "regions": regions})

    # --- page 2: Q2 + prose + table + sub-parts ----------------------------
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)
    regions = []
    y = MARGIN
    mb = _draw_marker(draw, "Q2.", marker, MARGIN, y)
    regions.append({"bbox": mb, "type": "marker", "marker": "Q2"})
    y += 90
    b = _draw_paragraph(draw, (
        "Compare the scheduling algorithms in the table below."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q2"})
    y += b[3] + 60
    tb = _draw_table(draw, MARGIN, y, cell, rows=4, cols=3, col_w=330, row_h=90, cells={
        (0, 0): "Algorithm", (0, 1): "Preempt", (0, 2): "Avg Wait",
        (1, 0): "FCFS", (1, 1): "No", (1, 2): "8.0",
        (2, 0): "SJF", (2, 1): "No", (2, 2): "6.2",
        (3, 0): "RR", (3, 1): "Yes", (3, 2): "7.4",
    })
    regions.append({"bbox": tb, "type": "table", "question": "Q2"})
    y += tb[3] + 80
    mb = _draw_marker(draw, "a)", marker, MARGIN, y)
    regions.append({"bbox": mb, "type": "marker", "marker": "Q2a"})
    y += 80
    b = _draw_paragraph(draw, (
        "Shortest job first gives the lowest average waiting time of the three."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q2a"})
    images.append(page)
    truth.append({"page_number": 2, "regions": regions})

    # --- page 3: continuation, NO marker at the top ------------------------
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)
    regions = []
    y = MARGIN
    b = _draw_paragraph(draw, (
        "It does however require knowing the burst time of each process in advance, "
        "which a real operating system cannot generally do, so it is approximated."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q2a"})
    y += b[3] + 70
    mb = _draw_marker(draw, "b)", marker, MARGIN, y)
    regions.append({"bbox": mb, "type": "marker", "marker": "Q2b"})
    y += 80
    b = _draw_paragraph(draw, (
        "Round robin is preferred for interactive systems because every process "
        "receives the processor within a bounded time."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q2b"})
    images.append(page)
    truth.append({"page_number": 3, "regions": regions})

    # --- page 4: stray unmarked header, then Q3 + diagram ------------------
    page = Image.new("RGB", (PAGE_W, PAGE_H), "white")
    draw = ImageDraw.Draw(page)
    regions = []
    y = MARGIN
    # Deliberately BEFORE any marker on this page. Because page 3 ended
    # inside 2b, a correct implementation carries 2b across the page break
    # and assigns this to 2b -- it is the cross-page rule, not an orphan.
    b = _draw_paragraph(draw, (
        "Rough work: quantum = 4 ms."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q2b"})
    y += b[3] + 80
    mb = _draw_marker(draw, "Q3.", marker, MARGIN, y)
    regions.append({"bbox": mb, "type": "marker", "marker": "Q3"})
    y += 90
    b = _draw_paragraph(draw, (
        "The process state pipeline is shown below."
    ), body, MARGIN, y, PAGE_W - 2 * MARGIN, 56)
    regions.append({"bbox": b, "type": "text", "question": "Q3"})
    y += b[3] + 70
    db = _draw_diagram(draw, MARGIN, y, cell, PAGE_W - 2 * MARGIN, 120)
    regions.append({"bbox": db, "type": "diagram", "question": "Q3"})
    images.append(page)
    truth.append({"page_number": 4, "regions": regions})

    return images, truth


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--font", default=DEFAULT_FONT,
                    help=f"handwriting font stem from media/ocr_benchmark/fonts/ (default: {DEFAULT_FONT})")
    ap.add_argument("--out", default=str(OUT_DIR),
                    help="output directory (default: media/booklets/)")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    images, truth = build_pages(args.font)

    # Rotate the pages that are meant to be crooked, AFTER ground truth is
    # recorded: the truth bboxes describe the DESKEWED page, which is what
    # the segmenter sees once core/booklet_ingest.py has straightened it.
    rendered = []
    for index, image in enumerate(images, start=1):
        angle = ROTATED_PAGES.get(index)
        if angle:
            image = image.rotate(angle, resample=Image.BICUBIC,
                                 expand=False, fillcolor="white")
        rendered.append(image)

    pdf_path = out_dir / "sample_booklet.pdf"
    rendered[0].save(pdf_path, save_all=True, append_images=rendered[1:], resolution=150.0)

    truth_path = out_dir / "ground_truth.json"
    truth_path.write_text(json.dumps({
        "pdf": pdf_path.name,
        "font": args.font,
        "page_size": [PAGE_W, PAGE_H],
        "rotated_pages": ROTATED_PAGES,
        "pages": truth,
    }, indent=2))

    total = sum(len(p["regions"]) for p in truth)
    print(f"Wrote {pdf_path} ({len(rendered)} pages)")
    print(f"Wrote {truth_path} ({total} ground-truth regions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
