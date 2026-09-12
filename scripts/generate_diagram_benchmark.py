"""
scripts/generate_diagram_benchmark.py — builds the labeled diagram
benchmark set in media/diagram_benchmark/, the fixture base for
scripts/benchmark_diagram_extraction.py.

WHY IT EXISTS. core/diagram_shapes.py's module docstring is explicit that
the whole shape/edge pipeline was validated against exactly two images
(media/diagrams/{buses.webp,hand_drawn_buses_image.jpeg}), both the same
diagram family: rectangular boxes joined by straight arrows. Diamonds,
circles/ellipses and curved connectors were called UNVALIDATED there — not
as a disclaimer, but literally: there was nothing in this repo to run them
against, so "does it work on a flowchart" had no answer either way. This
script produces that missing labeled set, so the claim can be measured
instead of asserted.

Mirrors scripts/generate_ocr_benchmark.py and
scripts/generate_table_benchmark.py: renders each family once per
handwriting-style font from media/ocr_benchmark/fonts/ (the same real
Google Fonts handwriting families — not synthetic jitter), writes images to
media/diagram_benchmark/images/, and writes an exact ground_truth.json
naming every node (label, shape type, drawn bbox) and every edge it was
rendered from.

FIVE FAMILIES, each isolating one thing the pipeline was never tested on:

  boxes_straight  rectangles + straight arrows. The ALREADY-VALIDATED
                  family, rendered here too — it is the control. A change
                  that improves diamonds while quietly breaking boxes has to
                  be visible, and without this row it wouldn't be.
  diamonds        flowchart decision diamond among rectangles, straight
                  arrows. Isolates shape classification and the fact that a
                  diamond's drawn ink occupies only ~half its bounding box.
  ellipses        circles/ovals only, straight arrows. Isolates round-shape
                  pairing and classification.
  curved          rectangles joined by CURVED (quadratic-Bezier) connectors.
                  Isolates connector detection alone — same shapes as the
                  control, only the connectors change, so any drop is
                  attributable to curvature and nothing else.
  mixed           rectangle + diamond + ellipse, straight and curved
                  connectors together, including a loop-back. The realistic
                  flowchart, and the only family where every unvalidated
                  feature has to work at once.

HONEST PROVENANCE, same argument as the table benchmark's docstring: these
are RENDERED, not scanned. This repo has no corpus of real hand-drawn
flowcharts with known ground truth, and fabricating "real" scans would
misrepresent where the numbers came from. Rendering the same shapes in
several real handwriting fonts, with seeded jitter on every drawn stroke,
varies letterform and line straightness the way different hands do — but a
rendered diagram still has clean, unbroken, high-contrast ink on white, and
the hard case for this pipeline (documented at length in
core/diagram_shapes.py) is a phone photo of RULED NOTEBOOK PAPER, where box
outlines fuse with page rules into one page-wide connected component. That
case is deliberately NOT generated here: it is a different problem, not a
harder version of this one. Read scores from this set as a comparison
signal between pipeline versions, not as real-world accuracy.

JITTER: every stroke's endpoints are displaced by up to JITTER_PX from true,
from a per-image SEEDED random derived from the filename — so regenerating
the set is byte-identical (a benchmark that moves under you isn't one) while
no line is machine-perfect.

Usage:
    python scripts/generate_diagram_benchmark.py               # 2 styles x 5 families
    python scripts/generate_diagram_benchmark.py --all-styles  # all 10 fonts
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from scripts.generate_ocr_benchmark import FONTS_DIR, select_fonts  # noqa: E402

BENCHMARK_DIR = REPO_ROOT / "media" / "diagram_benchmark"
IMAGES_DIR = BENCHMARK_DIR / "images"
GROUND_TRUTH_PATH = BENCHMARK_DIR / "ground_truth.json"

#: Two styles by default rather than all ten: this set is committed to the
#: repo, and the per-handwriting-style spread is already measured next door
#: by the OCR benchmark. What THIS benchmark measures is geometry, which
#: barely moves with the font. --all-styles when that assumption is what's
#: being checked.
DEFAULT_STYLES = ("PatrickHand", "Caveat")

CANVAS = (1100, 820)
BACKGROUND_COLOR = (255, 255, 252)
INK = (25, 25, 35)
FONT_SIZE = 40
STROKE_WIDTH = 3

#: Max px any drawn point is displaced from true. Small enough that a
#: rectangle still reads as a rectangle to cv2.approxPolyDP, large enough
#: that nothing in the image is pixel-perfect.
JITTER_PX = 2

#: Gap left between a shape's outline and the connector that terminates
#: against it. Real diagrams (hand-drawn and digital alike) leave one, and
#: core/diagram_shapes.py's REACH_PX/EXTEND_PX exist precisely to bridge it —
#: rendering connectors flush to the outline would test a case that doesn't
#: occur and skip the one that does.
CONNECTOR_GAP_PX = 10

ARROWHEAD_LEN_PX = 22
ARROWHEAD_HALF_WIDTH_PX = 9

#: Perpendicular displacement of a curved connector's Bezier control point
#: from the straight chord midpoint. Big enough that the connector is
#: unmistakably not a straight line (a Hough line detector must not be able
#: to fit it as one), small enough to stay a plausible drawn arc.
CURVE_BULGE_PX = 110

#: Points sampled along each Bezier — enough that the drawn polyline reads
#: as a smooth curve at STROKE_WIDTH rather than as a visible chain of
#: chords, which would flatter a segment-based detector.
CURVE_SAMPLES = 48


def _node(label: str, shape: str, cx: int, cy: int, w: int, h: int) -> dict:
    return {"label": label, "shape_type": shape, "cx": cx, "cy": cy, "w": w, "h": h}


#: family -> (nodes, edges). Edges name nodes by label; "connector" is
#: "straight" or "curved", and every edge is drawn with exactly one
#: arrowhead, at `to`.
FAMILIES: dict[str, dict] = {
    "boxes_straight": {
        "nodes": [
            _node("CPU", "rectangle", 260, 200, 300, 150),
            _node("Memory", "rectangle", 820, 200, 300, 150),
            _node("Cache", "rectangle", 260, 610, 300, 150),
            _node("Disk", "rectangle", 820, 610, 300, 150),
        ],
        "edges": [
            {"from": "CPU", "to": "Memory", "connector": "straight"},
            {"from": "CPU", "to": "Cache", "connector": "straight"},
            {"from": "Memory", "to": "Disk", "connector": "straight"},
            {"from": "Cache", "to": "Disk", "connector": "straight"},
        ],
    },
    "diamonds": {
        "nodes": [
            _node("Start", "rectangle", 260, 150, 280, 130),
            _node("Check", "diamond", 260, 420, 380, 250),
            _node("Print", "rectangle", 820, 420, 280, 130),
            _node("Stop", "rectangle", 260, 700, 280, 130),
        ],
        "edges": [
            {"from": "Start", "to": "Check", "connector": "straight"},
            {"from": "Check", "to": "Print", "connector": "straight"},
            {"from": "Check", "to": "Stop", "connector": "straight"},
        ],
    },
    "ellipses": {
        "nodes": [
            _node("Water", "ellipse", 270, 200, 340, 190),
            _node("Vapour", "ellipse", 830, 200, 340, 190),
            _node("Cloud", "ellipse", 830, 610, 340, 190),
            _node("Rain", "ellipse", 270, 610, 340, 190),
        ],
        "edges": [
            {"from": "Water", "to": "Vapour", "connector": "straight"},
            {"from": "Vapour", "to": "Cloud", "connector": "straight"},
            {"from": "Cloud", "to": "Rain", "connector": "straight"},
            {"from": "Rain", "to": "Water", "connector": "straight"},
        ],
    },
    "curved": {
        "nodes": [
            _node("CPU", "rectangle", 260, 200, 300, 150),
            _node("Memory", "rectangle", 820, 200, 300, 150),
            _node("Cache", "rectangle", 260, 610, 300, 150),
            _node("Disk", "rectangle", 820, 610, 300, 150),
        ],
        "edges": [
            {"from": "CPU", "to": "Memory", "connector": "curved"},
            {"from": "CPU", "to": "Cache", "connector": "curved"},
            {"from": "Memory", "to": "Disk", "connector": "curved"},
            {"from": "Cache", "to": "Disk", "connector": "curved"},
        ],
    },
    "mixed": {
        "nodes": [
            _node("Start", "rectangle", 250, 150, 280, 130),
            _node("Valid", "diamond", 250, 430, 380, 250),
            _node("Done", "ellipse", 820, 430, 320, 180),
            _node("Retry", "rectangle", 820, 130, 280, 130),
        ],
        "edges": [
            {"from": "Start", "to": "Valid", "connector": "straight"},
            {"from": "Valid", "to": "Done", "connector": "straight"},
            {"from": "Valid", "to": "Retry", "connector": "curved"},
            {"from": "Retry", "to": "Start", "connector": "curved"},
        ],
    },
}


def _bbox(node: dict) -> list[int]:
    """[x, y, w, h] of the node's drawn shape, in image coordinates — this
    is what lands in ground_truth.json and what the harness matches
    extracted nodes against."""
    return [node["cx"] - node["w"] // 2, node["cy"] - node["h"] // 2, node["w"], node["h"]]


def _jitter(rng: random.Random, point: tuple[float, float]) -> tuple[float, float]:
    return (point[0] + rng.uniform(-JITTER_PX, JITTER_PX),
            point[1] + rng.uniform(-JITTER_PX, JITTER_PX))


def _boundary_point(node: dict, toward: tuple[float, float]) -> tuple[float, float]:
    """Where the ray from the node's centre toward `toward` crosses the
    node's OUTLINE, pushed CONNECTOR_GAP_PX further out. Per shape type,
    because a diamond's and an ellipse's outlines sit well inside the
    bounding box a rectangle's outline traces exactly — connectors drawn to
    a shared bounding box would cross the ink of one and stop short of the
    other."""
    cx, cy = node["cx"], node["cy"]
    dx, dy = toward[0] - cx, toward[1] - cy
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    hw, hh = node["w"] / 2, node["h"] / 2

    if node["shape_type"] == "rectangle":
        # Distance along the ray to the nearer of the two axis walls.
        tx = hw / abs(ux) if ux else math.inf
        ty = hh / abs(uy) if uy else math.inf
        t = min(tx, ty)
    elif node["shape_type"] == "ellipse":
        t = 1.0 / math.hypot(ux / hw, uy / hh)
    elif node["shape_type"] == "diamond":
        # |x|/hw + |y|/hh = 1 along the ray.
        t = 1.0 / (abs(ux) / hw + abs(uy) / hh)
    else:
        raise ValueError(f"unknown shape_type {node['shape_type']!r}")

    t += CONNECTOR_GAP_PX
    return (cx + ux * t, cy + uy * t)


def _draw_shape(draw: ImageDraw.ImageDraw, rng: random.Random, node: dict) -> None:
    x, y, w, h = _bbox(node)
    if node["shape_type"] == "rectangle":
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        pts = [_jitter(rng, p) for p in corners]
        draw.line(pts + [pts[0]], fill=INK, width=STROKE_WIDTH, joint="curve")
    elif node["shape_type"] == "diamond":
        corners = [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
        pts = [_jitter(rng, p) for p in corners]
        draw.line(pts + [pts[0]], fill=INK, width=STROKE_WIDTH, joint="curve")
    elif node["shape_type"] == "ellipse":
        # Drawn as a sampled polyline rather than draw.ellipse() so the
        # outline can carry the same per-point jitter every other stroke
        # here has — a perfectly analytic ellipse would be the one shape in
        # the set that isn't hand-drawn-ish.
        pts = []
        for i in range(CURVE_SAMPLES * 2):
            theta = 2 * math.pi * i / (CURVE_SAMPLES * 2)
            pts.append(_jitter(rng, (node["cx"] + math.cos(theta) * w / 2,
                                      node["cy"] + math.sin(theta) * h / 2)))
        draw.line(pts + [pts[0]], fill=INK, width=STROKE_WIDTH, joint="curve")
    else:
        raise ValueError(f"unknown shape_type {node['shape_type']!r}")


def _arrowhead(draw: ImageDraw.ImageDraw, tip: tuple[float, float],
                back: tuple[float, float]) -> None:
    """Filled triangle at `tip`, pointing away from `back`. Filled, not two
    strokes: core/diagram_shapes.py detects direction by dark-pixel DENSITY
    at the endpoint, so an unfilled V would be testing a different thing
    than what real ink does."""
    dx, dy = tip[0] - back[0], tip[1] - back[1]
    norm = math.hypot(dx, dy) or 1.0
    ux, uy = dx / norm, dy / norm
    base = (tip[0] - ux * ARROWHEAD_LEN_PX, tip[1] - uy * ARROWHEAD_LEN_PX)
    px, py = -uy, ux
    draw.polygon([
        tip,
        (base[0] + px * ARROWHEAD_HALF_WIDTH_PX, base[1] + py * ARROWHEAD_HALF_WIDTH_PX),
        (base[0] - px * ARROWHEAD_HALF_WIDTH_PX, base[1] - py * ARROWHEAD_HALF_WIDTH_PX),
    ], fill=INK)


def _draw_connector(draw: ImageDraw.ImageDraw, rng: random.Random,
                     src: dict, dst: dict, connector: str) -> None:
    if connector == "straight":
        start = _boundary_point(src, (dst["cx"], dst["cy"]))
        end = _boundary_point(dst, (src["cx"], src["cy"]))
        pts = [_jitter(rng, start), _jitter(rng, end)]
        draw.line(pts, fill=INK, width=STROKE_WIDTH)
        _arrowhead(draw, pts[-1], pts[0])
        return

    if connector != "curved":
        raise ValueError(f"unknown connector {connector!r}")

    # Quadratic Bezier: control point displaced perpendicular to the chord.
    mid = ((src["cx"] + dst["cx"]) / 2, (src["cy"] + dst["cy"]) / 2)
    dx, dy = dst["cx"] - src["cx"], dst["cy"] - src["cy"]
    norm = math.hypot(dx, dy) or 1.0
    ctrl = (mid[0] - dy / norm * CURVE_BULGE_PX, mid[1] + dx / norm * CURVE_BULGE_PX)

    # Both ends leave/arrive along the direction of the control point, so
    # the curve exits its shape cleanly instead of clipping through it.
    start = _boundary_point(src, ctrl)
    end = _boundary_point(dst, ctrl)

    pts = []
    for i in range(CURVE_SAMPLES + 1):
        t = i / CURVE_SAMPLES
        bx = (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * ctrl[0] + t ** 2 * end[0]
        by = (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * ctrl[1] + t ** 2 * end[1]
        pts.append(_jitter(rng, (bx, by)))
    draw.line(pts, fill=INK, width=STROKE_WIDTH, joint="curve")
    _arrowhead(draw, pts[-1], pts[-2])


def render_diagram(font_path: pathlib.Path, style: str, family: str) -> dict:
    """Renders one (style, family) image and returns its ground-truth entry.
    Raises if a label doesn't fit inside its shape — a fixture whose text
    overflows its own box would make every extractor look wrong for the
    generator's mistake (same guard as generate_table_benchmark.py)."""
    spec = FAMILIES[family]
    filename = f"{style}_{family}.png"
    rng = random.Random(filename)  # seeded per image -> byte-stable regeneration

    image = Image.new("RGB", CANVAS, BACKGROUND_COLOR)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(font_path), FONT_SIZE)

    by_label = {n["label"]: n for n in spec["nodes"]}

    for node in spec["nodes"]:
        _draw_shape(draw, rng, node)
    # Connectors after shapes so an arrowhead is never overdrawn by an
    # outline; labels last so no stroke crosses the text an OCR engine has
    # to read.
    for edge in spec["edges"]:
        _draw_connector(draw, rng, by_label[edge["from"]], by_label[edge["to"]],
                        edge["connector"])

    nodes_out = []
    for node in spec["nodes"]:
        text = node["label"]
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        tw, th = right - left, bottom - top
        # A diamond's usable interior at its vertical centre is its full
        # width, but the text must clear the sloping sides; require it to fit
        # the inscribed box (half-width, half-height) for diamonds.
        limit_w = node["w"] * (0.5 if node["shape_type"] == "diamond" else 0.85)
        limit_h = node["h"] * (0.5 if node["shape_type"] == "diamond" else 0.7)
        if tw > limit_w or th > limit_h:
            raise SystemExit(
                f"{filename}: label {text!r} renders {tw:.0f}x{th:.0f}px but "
                f"{node['shape_type']} {node['label']} only allows "
                f"{limit_w:.0f}x{limit_h:.0f}px — enlarge the shape or shorten the "
                f"label, don't ship a fixture whose text overflows its own shape."
            )
        tx = node["cx"] - tw / 2 - left
        ty = node["cy"] - th / 2 - top
        draw.text((tx, ty), text, font=font, fill=INK)

        nodes_out.append({
            "label": text,
            "shape_type": node["shape_type"],
            "bbox": _bbox(node),
            "text_bbox": [int(node["cx"] - tw / 2), int(node["cy"] - th / 2),
                           int(tw), int(th)],
        })

    path = IMAGES_DIR / filename
    image.save(path)
    print(f"rendered {path.relative_to(REPO_ROOT)}")

    return {
        "style": style,
        "family": family,
        "image": filename,
        "nodes": nodes_out,
        "edges": [dict(e) for e in spec["edges"]],
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--all-styles", action="store_true",
                    help=f"render every font in {FONTS_DIR.name}/ instead of the "
                         f"default {len(DEFAULT_STYLES)} ({', '.join(DEFAULT_STYLES)})")
    args = ap.parse_args()

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    font_paths = select_fonts(None if args.all_styles else DEFAULT_STYLES)

    ground_truth = {}
    for font_path in font_paths:
        for family in FAMILIES:
            entry = render_diagram(font_path, font_path.stem, family)
            ground_truth[entry["image"]] = entry

    GROUND_TRUTH_PATH.write_text(json.dumps(ground_truth, indent=2) + "\n")
    print(f"\nwrote {GROUND_TRUTH_PATH.relative_to(REPO_ROOT)} "
          f"({len(ground_truth)} images, {len(FAMILIES)} families)")


if __name__ == "__main__":
    main()
