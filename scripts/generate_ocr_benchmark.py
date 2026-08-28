"""
scripts/generate_ocr_benchmark.py — builds the 10-handwriting-style OCR
benchmark set used by scripts/benchmark_ocr_engines.py to score
core/ocr_engines/ plugins against ground truth.

Renders a fixed two-line ground-truth string once per handwriting-style
font in media/ocr_benchmark/fonts/ (10 real Google Fonts handwriting
families — Caveat, Kalam, Indie Flower, etc., NOT synthetic jitter) onto a
lined-notebook-style background. Saves both:
  - media/ocr_benchmark/images/<font_name>_full.png — the whole two-line
    scan, for exercising detect_regions() (WHERE the lines are).
  - media/ocr_benchmark/images/<font_name>_line1.png / _line2.png —
    each line cropped separately, for exercising recognize() (WHAT a line
    says). This matters because every OCREngine.recognize() in
    core/ocr_engines/ is written to read ONE already-cropped line (that's
    what core/diagram_extractor.py always hands it, per detected region)
    — scoring recognize() against a two-line block would penalize engines
    for a mismatch with their real calling convention, not a real
    handwriting-reading failure.
ground_truth.json maps every image filename to the exact text it was
rendered from.

Why a fixed rendered string rather than real scanned handwriting: this repo
has no real handwriting-sample corpus with known ground truth, and
fabricating "real" scans would misrepresent the benchmark's provenance.
Rendering the SAME two lines in 10 different real handwriting *font*
styles is a legitimate, if imperfect, proxy for "handwriting style
variation" — it varies slant, stroke shape, spacing, and letterforms the
way different people's handwriting does, while keeping ground truth exact
(font rendering has no ambiguity, unlike transcribing a real photo).
Treat scores from this set as a signal for comparing engines' relative
handwriting-style robustness, not as an absolute real-world accuracy
number — see scripts/benchmark_ocr_engines.py's own docstring.

Usage:
    python scripts/generate_ocr_benchmark.py
"""
from __future__ import annotations

import json
import pathlib

from PIL import Image, ImageDraw, ImageFont

BENCHMARK_DIR = pathlib.Path(__file__).resolve().parent.parent / "media" / "ocr_benchmark"
FONTS_DIR = BENCHMARK_DIR / "fonts"
IMAGES_DIR = BENCHMARK_DIR / "images"
GROUND_TRUTH_PATH = BENCHMARK_DIR / "ground_truth.json"

# Two lines, mimicking a diagram label pair (a component name + a short
# description) — deliberately includes a digit and a hyphen, since those
# are common failure points for handwriting-tuned OCR models that lean
# heavily on dictionary/language priors.
LINE_1 = "Central Processing Unit"
LINE_2 = "Bus-01 Controller"

IMAGE_SIZE = (640, 220)
FONT_SIZE = 42
LINE_1_Y = 30
LINE_2_Y = 120
LINE_SPACING = LINE_2_Y - LINE_1_Y
LINE_CROP_HEIGHT = 70  # per-line crop height, tight around FONT_SIZE=42 plus padding
TEXT_COLOR = (20, 20, 30)
BACKGROUND_COLOR = (255, 255, 250)
RULE_LINE_COLOR = (200, 210, 230)


def _draw_ruled_background(draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
    """Faint horizontal ruled lines, echoing the ruled-paper photos this
    pipeline actually targets (see core/diagram_extractor.py's module
    docstring on why plain OpenCV detection failed on ruled paper) —
    keeps the benchmark closer to the real input distribution than a
    blank white background would."""
    width, height = size
    for y in range(40, height, 45):
        draw.line([(0, y), (width, y)], fill=RULE_LINE_COLOR, width=1)


def render_sample(font_path: pathlib.Path, style_name: str) -> dict:
    """Renders one style's full two-line image plus two single-line crops.
    Returns the ground-truth entry for this style (all three filenames +
    their exact text)."""
    image = Image.new("RGB", IMAGE_SIZE, BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)
    _draw_ruled_background(draw, IMAGE_SIZE)

    font = ImageFont.truetype(str(font_path), FONT_SIZE)
    draw.text((24, LINE_1_Y), LINE_1, font=font, fill=TEXT_COLOR)
    draw.text((24, LINE_2_Y), LINE_2, font=font, fill=TEXT_COLOR)

    full_path = IMAGES_DIR / f"{style_name}_full.png"
    image.save(full_path)

    line1_crop = image.crop((0, LINE_1_Y - 10, IMAGE_SIZE[0], LINE_1_Y - 10 + LINE_CROP_HEIGHT))
    line1_path = IMAGES_DIR / f"{style_name}_line1.png"
    line1_crop.save(line1_path)

    line2_crop = image.crop((0, LINE_2_Y - 10, IMAGE_SIZE[0], LINE_2_Y - 10 + LINE_CROP_HEIGHT))
    line2_path = IMAGES_DIR / f"{style_name}_line2.png"
    line2_crop.save(line2_path)

    for path in (full_path, line1_path, line2_path):
        print(f"rendered {path.relative_to(BENCHMARK_DIR.parent.parent)}")

    return {
        "style": style_name,
        "full_image": full_path.name,
        "line1_image": line1_path.name,
        "line2_image": line2_path.name,
        "line_1": LINE_1,
        "line_2": LINE_2,
        "full_text": f"{LINE_1} {LINE_2}",
    }


def main() -> None:
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    font_paths = sorted(FONTS_DIR.glob("*.ttf"))
    if not font_paths:
        raise SystemExit(f"No .ttf fonts found in {FONTS_DIR} — nothing to render.")

    ground_truth = {}
    for font_path in font_paths:
        style_name = font_path.stem
        ground_truth[style_name] = render_sample(font_path, style_name)

    GROUND_TRUTH_PATH.write_text(json.dumps(ground_truth, indent=2) + "\n")
    print(f"\nwrote {GROUND_TRUTH_PATH.relative_to(BENCHMARK_DIR.parent.parent)} "
          f"({len(ground_truth)} styles)")


if __name__ == "__main__":
    main()
