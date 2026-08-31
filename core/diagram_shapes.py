"""
core/diagram_shapes.py — the OpenCV half of Task 4 graph extraction:
pairs each OCR-detected label region with its enclosing drawn shape (box,
circle, ...), and detects the line/arrow connectors between shapes.

Split out of core/diagram_extractor.py (which owns OCR/label detection via
core/ocr_fallback.py) for the same reason core/ocr_engines/ is its own
package: this is a distinct concern (pixel geometry, not text recognition)
with its own tuning knobs, and keeping it separate means an extraction
pipeline that only needs labels (unlikely, but e.g. a future glossary-only
pass) doesn't have to import OpenCV-heavy code.

MEASURED SCOPE (2026-08-31). This module used to say that diamonds,
circles/ellipses and curved connectors were "unvalidated until tested
against a real example". They have now been tested, against a labeled set
built for the purpose — media/diagram_benchmark/ (scripts/generate_diagram_-
benchmark.py, 5 families x 2 handwriting styles), scored by
scripts/benchmark_diagram_extraction.py. Per-family means, before this
change and after it:

    family           edge F1          shape accuracy
    boxes_straight   0.929 -> 1.000    1.000 -> 1.000   (the control)
    diamonds         0.900 -> 1.000    0.750 -> 1.000
    ellipses         0.929 -> 1.000    1.000 -> 1.000
    curved           0.000 -> 1.000    1.000 -> 1.000
    mixed            0.667 -> 1.000    0.750 -> 1.000
    ALL              0.685 -> 1.000    0.900 -> 1.000

Node precision/recall and label accuracy were 1.000 before and after —
finding and reading the labels was never the gap; connectors and shape
naming were.

READ THOSE NUMBERS THE WAY THE TABLE BENCHMARK'S DOCSTRING ASKS ITS OWN TO
BE READ. They are not a real-world accuracy figure and 1.000 does not mean
"solved":
  - The fixtures are RENDERED, not scanned: unbroken high-contrast ink on
    clean white. Every hard property of a real scan — perspective, skew,
    faint pencil, ruled paper — is absent by construction.
  - The thresholds in _classify_shape were set FROM this set's own measured
    extent clusters. That is fitting, not holding out. It is defensible
    because extent's separation is geometric (1.0 / 0.785 / 0.5 are what
    those shapes ARE, not what these renders happened to score) and the
    thresholds sit at the midpoints of gaps ~0.2 wide, but it does mean the
    benchmark cannot be evidence for its own thresholds.
  - Four edges per image means one miss moves a family by 0.125. These are
    small numbers.

WHAT THE REAL IMAGES SAY, which is the part that matters most:
  - media/diagrams/buses.webp (clean digital): 4 edges detected before,
    5 after, none lost. A real gain, small.
  - media/diagrams/hand_drawn_buses_image.jpeg (phone photo of RULED
    NOTEBOOK PAPER): 10 edges before, 10 after — IDENTICAL, not one edge
    added or removed. The contour pass contributes NOTHING here, because
    the root cause documented below is unchanged: the page's ruled lines
    chain every stroke on the page into one connected component, so
    "connected ink = one connector" is false for exactly this image. The
    end-to-end score on it (scripts/run_diagram_eval_demo.sh) is byte-for-
    byte what it was before this change.

STILL POOR, STATED RATHER THAN SHIPPED QUIETLY:
  1. RULED-PAPER PHOTOS remain this pipeline's real input distribution and
     its unsolved case. Neither of the two connector detectors works there;
     the fallback in pair_shapes() (_find_box_by_lines) is what carries that
     image, and it resolves boxes by looking for four straight walls.
  2. Which means SHAPE CLASSIFICATION IS RECTANGLE-ONLY ON RULED PAPER. A
     diamond or an ellipse drawn on notebook paper reaches _find_box_by_lines,
     which can only ever return "rectangle" or None — it has no notion of a
     sloping or curved side. The diamond/ellipse accuracy above is a
     CONTOUR-PATH result and does not transfer to that path. Fixing it means
     a different box-finding method, not a better classifier.
  3. Curved-connector support is unvalidated on any REAL curved diagram.
     This repo still contains none; the curved family is synthetic. What is
     established is that the capability exists and that adding it did not
     cost anything on the straight families or the real images.

Nothing here scores on shape_type — core/diagram_evaluator.py compares
labels and edges only — so a wrong shape name degrades provenance and
review context, not marks.

Two independent stages:

1. pair_shapes(gray, nodes) — for each node's OCR text bbox, finds the
   smallest contour that fully encloses it with a real margin (rules out
   glyph-stroke contours, which sit almost flush with the text) and isn't
   implausibly large (rules out picking up the whole page). Classifies the
   shape by vertex count after cv2.approxPolyDP. Mutates each node dict
   in-place with "shape_bbox" (falls back to the text bbox itself if no
   enclosing shape is found — e.g. a label with no drawn border around it,
   which is legitimate: media/diagrams/buses.webp's "System Bus" label sits
   outside any box) and "shape_type" (None in that case).

   KNOWN LIMITATION: a shape whose outline is interrupted by an internal
   divider/tick mark that touches the border (buses.webp's Control/Address
   Bus rows each have a short internal stroke where an arrow terminates
   flush against the top edge) gets contour-split into a sub-rectangle
   instead of the true full-width box. A 7x7 morphological close is applied
   before contour-finding to bridge small gaps, which fixes short
   interruptions but not one spanning a box's full height, as in that
   example. Net effect: shape_bbox can be narrower than the drawn shape in
   that specific case. This under-sizes the mask in detect_edges() (below)
   for that node, which can cause a real connecting line to fall just short
   of REACH_PX and get dropped — a missed edge, not a wrong one. See
   scripts/explore_diagram_edges.py-style ad-hoc testing (not checked in;
   this was iterated against the two real images in media/diagrams/ during
   development) before trusting shape_bbox precision for scoring beyond
   "is there roughly a shape here."

   ROOT CAUSE FOUND (2026-08-30, on media/diagrams/hand_drawn_buses_image.jpeg):
   this is NOT a small-gap problem that a bigger morphological-close kernel
   can fix (verified: kernels up to 25x25 still found zero contour
   containing any real label's text bbox). The actual cause is page-wide
   connectivity — cv2.connectedComponents on this photo's OTSU binary shows
   the CPU box's top edge sitting in a single connected-component blob
   spanning x:[0,1599] y:[126,871] (126k+ px) on a 1600px-wide image: the
   box's own strokes touch the notebook's ruled horizontal lines, which run
   the full page width and chain every box/line on the page into one
   component. No per-box contour can ever be isolated this way; the
   contour-based method below is fundamentally the wrong tool for a ruled-
   paper photo, not under-tuned.

   FIX: pair_shapes() now tries the contour method first (unchanged, still
   what actually gets used for a clean digital diagram like buses.webp,
   where ink doesn't touch a ruled-line grid), and falls back to
   _find_box_by_lines() — a local Hough-line search per node (see below) —
   when no contour is found. That in turn falls back to the plain text bbox
   only if lines are found on none of the 4 sides, and even then the
   returned box is expanded outward by FALLBACK_MARGIN_PX rather than
   returned tight, so a real nearby line still has a realistic chance of
   reaching it in detect_edges().

   _find_box_by_lines(): searches a padded ROI around the node's text bbox
   with Canny + HoughLinesP (same primitives as detect_edges below, applied
   locally instead of whole-image), classifies segments as roughly
   horizontal/vertical, and — independently per side — keeps the nearest
   one whose extent overlaps the text bbox on the perpendicular axis. Each
   of the 4 sides is resolved independently, so a diagram element that is
   only a bracket/open shape on one side (this repo's Control/Address/Data
   Bus rows are drawn open-ended, not closed rectangles — confirmed by eye
   against the image) still gets the sides it does have, with the missing
   side(s) filled in by FALLBACK_MARGIN_PX expansion rather than forcing a
   full rectangle that isn't actually drawn. shape_type is only reported
   "rectangle" when at least 3 of 4 sides were resolved by real lines (not
   just margin filler) — below that, treat shape classification as
   unreliable for this node, same caveat as the old no-shape-found case.

2. detect_edges(gray, nodes) — masks every node's shape_bbox out of the
   image (so line detection isn't confused by box borders or letterforms,
   per the task's own instruction), then runs TWO independent connector
   passes over what's left and merges their results, deduplicated per node
   pair with the more confident detection winning. Each edge records which
   pass found it, in its "detector" field.

   a. STRAIGHT (detector="hough"): Canny + HoughLinesP, merging
      near-collinear/adjacent segments into logical lines. This is the
      original pass and the one this repo's real images were validated
      against; it is unchanged.
   b. ANY SHAPE (detector="contour"): connected-ink contours, whose two
      extreme points are the connector's two ends — no straightness
      assumption, which is what makes curved connectors detectable at all.
      See the block comment above _detect_connector_contours for why this
      could not be done by loosening the Hough pass's merge tolerance.

   Both match their endpoints to the nearest node the same way (within
   REACH_PX, or after projecting up to EXTEND_PX further along the
   connector's own outgoing direction — bridges the small gap most
   hand/digital diagrams leave between a connector's visible end and the
   box it terminates against). Ends that don't resolve to two distinct
   nodes are dropped, not guessed.

   Arrow direction (both passes): samples dark-pixel density in a small
   disk at each raw (pre-extension) endpoint and at the connector's
   midpoint — for a curve, the point furthest along the stroke from both
   ends. An
   arrowhead's triangular fill reads as meaningfully denser than the
   plain-stroke midpoint; a plain line-end does not. This is a coarse
   heuristic — see the module-level ARROW_DENSITY_MARGIN docstring note
   for why it's unreliable exactly where a line is drawn touching a box
   border (the border's own ink inflates density at both ends
   symmetrically, masking a real arrowhead's signal). When the two ends'
   scores don't clear ARROW_DENSITY_MARGIN apart, direction is reported
   "undirected" with reduced confidence rather than guessed — this is the
   "flag for review instead of silently wrong" behavior the task asked
   for; do not raise the margin just to force more directed calls without
   re-validating against real images.

Both functions take a grayscale numpy array (not a PIL Image, unlike
core/diagram_extractor.py's OCR calls) since OpenCV operates on numpy
arrays natively and every operation here needs pixel access, not just a
single predict() call.
"""
from __future__ import annotations

import math

import numpy as np
import cv2

# --- shape pairing -----------------------------------------------------

# A candidate enclosing contour must extend at least this many px past the
# text bbox on every side. Rules out contours from the glyph strokes
# themselves (anti-aliasing/serif contours sit ~0-2px from the text bbox);
# tuned against media/diagrams/buses.webp, where the real box outline sits
# as little as 7px from a short label's bbox on its tightest side.
SHAPE_MIN_MARGIN_PX = 3

# A candidate contour's bounding-box area must be at least this multiple of
# the text bbox's own area — otherwise a contour barely larger than the text
# (e.g. from JPEG ringing around glyph edges) can slip past the margin check.
SHAPE_MIN_AREA_RATIO = 1.3

# Kernel for the morphological close applied before contour-finding, to
# bridge short interruptions in a shape's outline (an arrow terminating
# flush against a box edge breaks the outline there). See module docstring
# for the case this does NOT fix (an interruption spanning a box's full
# height/width).
_MORPH_CLOSE_KERNEL = np.ones((7, 7), np.uint8)

# --- line-based fallback (ruled-paper / broken-contour hand-drawn boxes) --

# How far out from the text bbox to search for a wall line, on each side.
# Tuned against hand_drawn_buses_image.jpeg, where the farthest genuine box
# wall (ControlBus's left side) sits ~160px from its label's text bbox.
LINE_SEARCH_PAD_PX = 180

# A candidate wall line must be at least this fraction of the text bbox's
# corresponding dimension (width for a horizontal/top-bottom line, height
# for a vertical/left-right line) — rules out short unrelated strokes
# (arrowheads, stray marks) inside the search window.
LINE_MIN_LENGTH_RATIO = 0.5

# A candidate wall line's extent along the text bbox's own axis must
# overlap it by at least this fraction — rules out a line that passes the
# length check but sits off to the side (e.g. a neighboring box's edge).
LINE_MIN_OVERLAP_RATIO = 0.3

# Segments within this many degrees of 0/180 count as "horizontal"; within
# this many degrees of 90 count as "vertical". Matches the tolerance used
# by detect_edges's own _merge_segments for the same reason: hand-drawn
# lines are never perfectly axis-aligned.
LINE_ANGLE_TOL_DEG = 10

# When a side has no resolvable wall line at all, the fallback box is
# expanded outward from the text bbox by this many px on that side (rather
# than left flush with the text) — gives detect_edges's own REACH_PX a
# realistic chance of still reaching a real nearby line on top of this.
FALLBACK_MARGIN_PX = 20


def _find_box_by_lines(gray: np.ndarray, tx: int, ty: int, tw: int, th: int) -> dict:
    """Independently resolves each of the 4 sides of a node's enclosing box
    by searching a local ROI for Hough line segments, rather than relying on
    global contour connectivity (see module docstring for why that fails on
    ruled-paper photos). Returns {"top"/"bottom"/"left"/"right": px-position
    in full-image coords, or None if that side wasn't resolved}."""
    h_img, w_img = gray.shape
    x0 = max(0, tx - LINE_SEARCH_PAD_PX)
    y0 = max(0, ty - LINE_SEARCH_PAD_PX)
    x1 = min(w_img, tx + tw + LINE_SEARCH_PAD_PX)
    y1 = min(h_img, ty + th + LINE_SEARCH_PAD_PX)
    roi = gray[y0:y1, x0:x1]

    edges_img = cv2.Canny(roi, 50, 150, apertureSize=3)
    min_len = max(8, int(min(tw, th) * LINE_MIN_LENGTH_RATIO))
    raw = cv2.HoughLinesP(edges_img, 1, np.pi / 180, threshold=25,
                           minLineLength=min_len, maxLineGap=15)
    sides = {"top": None, "bottom": None, "left": None, "right": None}
    if raw is None:
        return sides

    # Text bbox in ROI-local coordinates.
    ltx, lty = tx - x0, ty - y0

    best_dist = {"top": None, "bottom": None, "left": None, "right": None}
    for line in raw:
        sx1, sy1, sx2, sy2 = (int(v) for v in line.flatten())
        length = math.hypot(sx2 - sx1, sy2 - sy1)
        angle = math.degrees(math.atan2(sy2 - sy1, sx2 - sx1)) % 180
        is_h = angle <= LINE_ANGLE_TOL_DEG or angle >= 180 - LINE_ANGLE_TOL_DEG
        is_v = abs(angle - 90) <= LINE_ANGLE_TOL_DEG

        # Bucketed by which side of the text bbox's CENTER a candidate falls
        # on, not by whether it's strictly outside the bbox edge — a
        # detected text region is often already over-inclusive of the real
        # box border (verified: PaddleOCR's "CPU" region on
        # hand_drawn_buses_image.jpeg already extends to within ~20px of
        # the box's true bottom wall), so requiring a wall to sit outside
        # the full text bbox misses it and reaches past it to a wrong,
        # farther line instead. Distance is still measured to the nearest
        # true bbox edge (not the center), so a wall genuinely outside the
        # bbox is still preferred by proximity within its bucket.
        center_y = lty + th / 2
        center_x = ltx + tw / 2

        if is_h and length >= tw * LINE_MIN_LENGTH_RATIO:
            xs = sorted((sx1, sx2))
            overlap = min(xs[1], ltx + tw) - max(xs[0], ltx)
            if overlap >= tw * LINE_MIN_OVERLAP_RATIO:
                mid_y = (sy1 + sy2) / 2
                if mid_y < center_y:
                    d = abs(mid_y - lty)
                    if best_dist["top"] is None or d < best_dist["top"]:
                        best_dist["top"], sides["top"] = d, y0 + mid_y
                else:
                    d = abs(mid_y - (lty + th))
                    if best_dist["bottom"] is None or d < best_dist["bottom"]:
                        best_dist["bottom"], sides["bottom"] = d, y0 + mid_y

        if is_v and length >= th * LINE_MIN_LENGTH_RATIO:
            ys = sorted((sy1, sy2))
            overlap = min(ys[1], lty + th) - max(ys[0], lty)
            if overlap >= th * LINE_MIN_OVERLAP_RATIO:
                mid_x = (sx1 + sx2) / 2
                if mid_x < center_x:
                    d = abs(mid_x - ltx)
                    if best_dist["left"] is None or d < best_dist["left"]:
                        best_dist["left"], sides["left"] = d, x0 + mid_x
                else:
                    d = abs(mid_x - (ltx + tw))
                    if best_dist["right"] is None or d < best_dist["right"]:
                        best_dist["right"], sides["right"] = d, x0 + mid_x

    return sides


def binarize(gray: np.ndarray) -> np.ndarray:
    """Inverted OTSU binary (ink = 255, paper = 0) — the one binarization
    step every pixel-geometry pass in this repo starts from.

    Shared with core/table_extractor.py rather than re-written there.
    OTSU specifically, NOT cv2.adaptiveThreshold: on a clean synthetic /
    flat-scanned image with a near-uniform background, an adaptive-mean
    threshold with a negative C makes essentially EVERY pixel foreground
    (each pixel sits within C of its own neighbourhood mean), which reads
    downstream as "the whole page is ink" rather than as an error. Measured
    on media/tables/images/ during the table-extractor spike: adaptive
    thresholding put 312 of 376 image rows over a 30%-ink line-detection
    threshold; OTSU put exactly the 5 real ruling lines over it.
    """
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


# --- shape classification ----------------------------------------------
#
# EXTENT — the contour's own filled area divided by its bounding-box area —
# is the primary discriminator, NOT vertex count and NOT circularity.
#
# Vertex count alone cannot tell a rectangle from a diamond: both
# approxPolyDP to exactly 4 vertices (measured: 4 for every rectangle AND
# every diamond in media/diagram_benchmark/). A diamond is a rotated
# quadrilateral, and its rotation is precisely the information a vertex
# count throws away.
#
# Circularity (4*pi*area/perimeter^2) cannot be used either, because it
# falls with ASPECT RATIO, not with roundness: measured over the same set,
# the 2:1 rectangles score 0.68-0.70 and the 1.8:1 ellipses 0.77-0.78 —
# barely a tenth apart, and they would cross entirely for a square box.
#
# Extent separates all three cleanly, and the measured values sit on their
# geometric ideals with wide gaps between clusters
# (media/diagram_benchmark/, 40 shapes, both handwriting styles):
#
#     shape       ideal extent   measured range   n vertices
#     rectangle       1.000       0.972 - 0.990       4
#     ellipse         0.785       0.769 - 0.781       8
#     diamond         0.500       0.515 - 0.519       4
#
# Thresholds below are set in the MIDDLE of those gaps, not at the edge of
# an observed range, so a shakier hand than the benchmark's jitter still
# lands in the right bucket. Vertex count is kept as a secondary guard
# only, to stop a 3-sided or wildly irregular contour being confidently
# called one of the three.

#: At or above this extent, a contour fills its bounding box: a rectangle.
RECT_MIN_EXTENT = 0.88
#: Range around pi/4 (0.785) that reads as an ellipse/circle.
ELLIPSE_EXTENT_RANGE = (0.64, 0.88)
#: Range around 0.5 that reads as a diamond (a quadrilateral standing on a
#: vertex — the flowchart decision shape).
DIAMOND_EXTENT_RANGE = (0.36, 0.64)
#: A diamond must still be roughly quadrilateral. The upper bound is loose
#: because a hand-drawn corner can approximate to two vertices.
DIAMOND_VERTEX_RANGE = (4, 6)
#: An ellipse's outline approximates to more vertices than a polygon's
#: corners — below this it is some other shape that happens to fill ~3/4 of
#: its box.
ELLIPSE_MIN_VERTICES = 5


def _classify_shape(contour) -> str:
    """Classifies one enclosing contour as
    "rectangle" | "diamond" | "ellipse" | "polygon" | "other".

    See the block comment above for why this is decided on extent. Returns
    "polygon" for a shape that is none of the three known ones but is still
    a plausible closed figure, and "other" for something too degenerate to
    call — neither is an error: core/diagram_extractor.py reports shape_type
    as provenance, and nothing in core/diagram_evaluator.py scores on it.
    """
    peri = cv2.arcLength(contour, True)
    if peri <= 0:
        return "other"

    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    n = len(approx)
    if n <= 3:
        return "other"

    x, y, w, h = cv2.boundingRect(contour)
    box_area = w * h
    if box_area <= 0:
        return "other"
    extent = cv2.contourArea(contour) / box_area

    if extent >= RECT_MIN_EXTENT:
        return "rectangle"
    if (DIAMOND_EXTENT_RANGE[0] <= extent < DIAMOND_EXTENT_RANGE[1]
            and DIAMOND_VERTEX_RANGE[0] <= n <= DIAMOND_VERTEX_RANGE[1]):
        return "diamond"
    if (ELLIPSE_EXTENT_RANGE[0] <= extent < ELLIPSE_EXTENT_RANGE[1]
            and n >= ELLIPSE_MIN_VERTICES):
        return "ellipse"
    return "polygon"


def pair_shapes(gray: np.ndarray, nodes: list[dict]) -> list[dict]:
    """Mutates and returns `nodes`, adding "shape_bbox" ([x, y, w, h]) and
    "shape_type" (str | None) to each. See module docstring for the
    matching rule and its known limitation."""
    binary = binarize(gray)
    closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, _MORPH_CLOSE_KERNEL)
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    img_area = gray.shape[0] * gray.shape[1]

    for node in nodes:
        tx, ty, tw, th = node["bbox"]
        text_area = tw * th
        best_contour, best_area = None, None

        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            if not (x <= tx and y <= ty and x + w >= tx + tw and y + h >= ty + th):
                continue
            margins = (tx - x, ty - y, (x + w) - (tx + tw), (y + h) - (ty + th))
            if min(margins) < SHAPE_MIN_MARGIN_PX:
                continue
            area = w * h
            if area < text_area * SHAPE_MIN_AREA_RATIO or area > img_area * 0.5:
                continue
            if best_area is None or area < best_area:
                best_area = area
                best_contour = c

        if best_contour is not None:
            x, y, w, h = cv2.boundingRect(best_contour)
            node["shape_bbox"] = [x, y, w, h]
            node["shape_type"] = _classify_shape(best_contour)
            continue

        # Contour method found nothing (see module docstring — expected on
        # ruled-paper photos, not just a "hard" image). Try resolving each
        # side independently via local line search before giving up.
        sides = _find_box_by_lines(gray, tx, ty, tw, th)
        resolved = sum(1 for v in sides.values() if v is not None)
        box_x0 = sides["left"] if sides["left"] is not None else tx - FALLBACK_MARGIN_PX
        box_y0 = sides["top"] if sides["top"] is not None else ty - FALLBACK_MARGIN_PX
        box_x1 = sides["right"] if sides["right"] is not None else tx + tw + FALLBACK_MARGIN_PX
        box_y1 = sides["bottom"] if sides["bottom"] is not None else ty + th + FALLBACK_MARGIN_PX

        node["shape_bbox"] = [int(box_x0), int(box_y0), int(box_x1 - box_x0), int(box_y1 - box_y0)]
        # "rectangle" only when most sides are real detected lines, not
        # margin filler — see module docstring on open-sided elements
        # (this repo's bus rows) legitimately resolving fewer than 4.
        node["shape_type"] = "rectangle" if resolved >= 3 else None

    return nodes


# --- edge/arrow detection ----------------------------------------------

# How far past a node's shape_bbox (or the whole page, for the "extend"
# fallback) a line endpoint may sit and still count as touching that node.
REACH_PX = 20

# How far to project a line beyond its own visible endpoint, along its own
# direction, to bridge the small gap most diagrams leave before a node's
# border. Applied only if REACH_PX alone doesn't resolve a match.
EXTEND_PX = 60

# Segments shorter than this are almost always OCR/JPEG noise or a box's
# own (unmasked) internal decoration, not a real connector.
MIN_EDGE_LENGTH_PX = 15

# Merge two Hough segments into one logical line if their angles agree
# within this many degrees...
MERGE_ANGLE_TOL_DEG = 6
# ...and an endpoint of one sits within this many px of an endpoint of the
# other (chains multiple short Hough fragments of the same drawn line).
MERGE_DIST_TOL_PX = 10

# Radius of the disk sampled for arrowhead density, at each raw segment
# endpoint and at the segment midpoint.
ARROW_SAMPLE_RADIUS_PX = 9
# An endpoint's dark-pixel density must exceed the midpoint's by at least
# this much to be called an arrowhead. See module docstring: this margin is
# NOT reliably clear of false positives from box-border ink at the endpoint
# — that's a real gap in this heuristic, not tuned away, because widening
# the margin to dodge it made genuine arrowheads undetectable in testing
# against media/diagrams/buses.webp. Ambiguous cases report "undirected"
# with low confidence rather than a guessed direction.
ARROW_DENSITY_MARGIN = 0.12


def _segment_angle(seg: tuple[int, int, int, int]) -> float:
    x1, y1, x2, y2 = seg
    return math.degrees(math.atan2(y2 - y1, x2 - x1)) % 180


def _merge_segments(segs: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    used = [False] * len(segs)
    merged = []
    for i, s in enumerate(segs):
        if used[i]:
            continue
        group = [s]
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, t in enumerate(segs):
                if used[j]:
                    continue
                for g in group:
                    da = abs(_segment_angle(g) - _segment_angle(t))
                    if da > MERGE_ANGLE_TOL_DEG and abs(da - 180) > MERGE_ANGLE_TOL_DEG:
                        continue
                    pts_g = [(g[0], g[1]), (g[2], g[3])]
                    pts_t = [(t[0], t[1]), (t[2], t[3])]
                    close = any(
                        math.hypot(a[0] - b[0], a[1] - b[1]) < MERGE_DIST_TOL_PX
                        for a in pts_g for b in pts_t
                    )
                    if close:
                        group.append(t)
                        used[j] = True
                        changed = True
                        break
                if changed:
                    break
        pts = [(p[0], p[1]) for p in group] + [(p[2], p[3]) for p in group]
        pts.sort()
        merged.append((pts[0][0], pts[0][1], pts[-1][0], pts[-1][1]))
    return merged


def _dist_point_to_bbox(px: float, py: float, bbox: list[int]) -> float:
    x, y, w, h = bbox
    dx = max(x - px, 0, px - (x + w))
    dy = max(y - py, 0, py - (y + h))
    return math.hypot(dx, dy)


def _nearest_node(px: float, py: float, nodes: list[dict]) -> tuple[str | None, float | None]:
    best_id, best_d = None, None
    for n in nodes:
        d = _dist_point_to_bbox(px, py, n["shape_bbox"])
        if best_d is None or d < best_d:
            best_d, best_id = d, n["node_id"]
    return best_id, best_d


def _match_endpoint(x: float, y: float, dx: float, dy: float, nodes: list[dict]) -> str | None:
    """dx, dy is the unit direction pointing away from the segment, used to
    project past the endpoint if REACH_PX alone doesn't find a node."""
    node_id, d = _nearest_node(x, y, nodes)
    if d is not None and d <= REACH_PX:
        return node_id
    ex, ey = x + dx * EXTEND_PX, y + dy * EXTEND_PX
    node_id2, d2 = _nearest_node(ex, ey, nodes)
    if d2 is not None and d2 <= REACH_PX:
        return node_id2
    return None


def _arrow_density(x: float, y: float, gray: np.ndarray, radius: int = ARROW_SAMPLE_RADIUS_PX) -> float:
    x0, y0 = max(0, int(x - radius)), max(0, int(y - radius))
    x1, y1 = min(gray.shape[1], int(x + radius)), min(gray.shape[0], int(y + radius))
    patch = gray[y0:y1, x0:x1]
    if patch.size == 0:
        return 0.0
    return float((patch < 128).sum()) / patch.size


# --- curved connectors --------------------------------------------------
#
# WHY A SECOND DETECTOR RATHER THAN LOOSER HOUGH SETTINGS. HoughLinesP finds
# STRAIGHT segments; an arc is not one. It does get fragmented into a chain
# of short chords, but _merge_segments deliberately only chains fragments
# whose angles agree within MERGE_ANGLE_TOL_DEG (6 degrees) — which is what
# keeps two different straight connectors that happen to touch from being
# welded into one. Widening that tolerance far enough to reabsorb an arc
# would break exactly the case the straight path already gets right.
# Measured, before this pass existed: the curved family of
# media/diagram_benchmark/ scored edge F1 = 0.000, with ZERO edges detected
# on either style — not "some", none. It is a missing capability, not a
# tuning gap.
#
# The contour pass works from the other end: on the same node-masked image,
# whatever ink survives IS the connectors, so each connector is one
# connected contour whose two extreme points are its two ends — no
# straightness assumption anywhere. It runs IN ADDITION to the Hough pass,
# not instead of it: the Hough path is what this repo's real images were
# validated against, and detect_edges()' existing per-node-pair dedup means
# a straight connector found by both is one edge, at the better confidence.

#: A contour shorter than this (perimeter, so roughly twice the stroke's
#: length) is a stray mark or leftover shape ink, not a connector. Kept
#: consistent with MIN_EDGE_LENGTH_PX, which bounds the same thing for the
#: Hough path.
MIN_CONNECTOR_PERIMETER_PX = 2 * MIN_EDGE_LENGTH_PX

#: approxPolyDP epsilon, as a fraction of the contour's perimeter. Much
#: tighter than the 0.02 used for shape classification: there, the point is
#: to collapse an outline to its corners; here, the polyline must still
#: follow the arc closely enough that its extreme points are the real ends.
CONNECTOR_APPROX_EPS_RATIO = 0.005

#: Radius around an endpoint whose contour points are averaged to get the
#: local tangent — the direction the connector is heading as it leaves its
#: visible end, used to project toward a node the way the Hough path
#: projects along its segment's own direction (EXTEND_PX).
TANGENT_SAMPLE_RADIUS_PX = 25

#: path length / straight-line distance between the two ends. At/below the
#: lower bound the stroke is straight (the Hough path's business, though
#: this pass will find it too and the dedup sorts it out); above the upper
#: bound the contour has doubled back on itself so far that its "two ends"
#: are not meaningfully the ends of one connector — a blob, a closed loop,
#: or two crossing strokes fused into one contour. Both are guards, not
#: tuning: nothing between them is rejected.
MAX_CONNECTOR_CURVINESS = 4.0


def _farthest_pair(points: np.ndarray) -> tuple[int, int]:
    """Indices of the two points furthest apart. O(n^2) on the
    approxPolyDP-reduced polyline (tens of points, not thousands), which is
    why the approximation happens before this and not after."""
    best = (0, 0)
    best_d = -1.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            d = math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1])
            if d > best_d:
                best_d, best = d, (i, j)
    return best


def _outward_tangent(contour_pts: np.ndarray, end: np.ndarray) -> tuple[float, float]:
    """Unit vector pointing away from the stroke at `end`, computed as
    (end - mean of the contour points near end). Uses the RAW contour rather
    than the approximation so the tangent reflects the actual local
    curvature; falls back to (0, 0) — i.e. no projection — if the
    neighbourhood is degenerate, rather than inventing a direction."""
    deltas = contour_pts - end
    near = contour_pts[np.hypot(deltas[:, 0], deltas[:, 1]) <= TANGENT_SAMPLE_RADIUS_PX]
    if len(near) < 2:
        return (0.0, 0.0)
    centre = near.mean(axis=0)
    dx, dy = float(end[0] - centre[0]), float(end[1] - centre[1])
    norm = math.hypot(dx, dy)
    if norm == 0:
        return (0.0, 0.0)
    return (dx / norm, dy / norm)


def _detect_connector_contours(gray: np.ndarray, masked: np.ndarray,
                                nodes: list[dict]) -> list[dict]:
    """Finds connectors of ANY shape as connected ink on the node-masked
    image. Returns candidate dicts in the same form the Hough path produces,
    for detect_edges() to merge and dedup.

    Direction uses the same arrowhead-density heuristic as the straight
    path, sampled at the two ends against the point furthest along the
    stroke from both — the curved equivalent of "the segment's midpoint",
    and subject to exactly the same documented unreliability
    (ARROW_DENSITY_MARGIN).
    """
    binary = binarize(masked)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        if peri < MIN_CONNECTOR_PERIMETER_PX:
            continue

        approx = cv2.approxPolyDP(contour, CONNECTOR_APPROX_EPS_RATIO * peri, True)
        pts = approx.reshape(-1, 2)
        if len(pts) < 2:
            continue

        i, j = _farthest_pair(pts)
        p_a, p_b = pts[i], pts[j]
        chord = math.hypot(float(p_a[0] - p_b[0]), float(p_a[1] - p_b[1]))
        if chord < MIN_EDGE_LENGTH_PX:
            continue

        # An open stroke's contour runs out along one side and back along
        # the other, so its perimeter is ~2x the stroke's own length.
        curviness = (peri / 2) / chord
        if curviness > MAX_CONNECTOR_CURVINESS:
            continue

        raw_pts = contour.reshape(-1, 2)
        ux_a, uy_a = _outward_tangent(raw_pts, p_a)
        ux_b, uy_b = _outward_tangent(raw_pts, p_b)

        a_id = _match_endpoint(float(p_a[0]), float(p_a[1]), ux_a, uy_a, nodes)
        b_id = _match_endpoint(float(p_b[0]), float(p_b[1]), ux_b, uy_b, nodes)
        if a_id is None or b_id is None or a_id == b_id:
            continue

        # Furthest point from both ends — "the middle of the stroke" for a
        # shape that has no midpoint in the straight-line sense.
        mid = max(pts, key=lambda p: min(
            math.hypot(float(p[0] - p_a[0]), float(p[1] - p_a[1])),
            math.hypot(float(p[0] - p_b[0]), float(p[1] - p_b[1]))))

        score_a = _arrow_density(float(p_a[0]), float(p_a[1]), gray)
        score_b = _arrow_density(float(p_b[0]), float(p_b[1]), gray)
        mid_score = _arrow_density(float(mid[0]), float(mid[1]), gray, radius=6)
        head_a = score_a > mid_score + ARROW_DENSITY_MARGIN
        head_b = score_b > mid_score + ARROW_DENSITY_MARGIN

        if head_a and not head_b:
            direction, src, dst = "directed", b_id, a_id
        elif head_b and not head_a:
            direction, src, dst = "directed", a_id, b_id
        elif head_a and head_b:
            direction, src, dst = "bidirectional", a_id, b_id
        else:
            direction, src, dst = "undirected", a_id, b_id

        # Same confidence formula as the straight path, over the stroke's
        # own length rather than a chord, so "needs_review" means the same
        # thing however an edge was found.
        length_conf = min(1.0, (peri / 2) / 100)
        direction_conf = 0.7 if direction != "undirected" else 0.5
        candidates.append({
            "a_id": a_id, "b_id": b_id,
            "from_node": src, "to_node": dst, "direction": direction,
            "confidence": round(length_conf * direction_conf, 3),
            "detector": "contour",
            "curviness": round(curviness, 3),
        })

    return candidates


def detect_edges(gray: np.ndarray, nodes: list[dict]) -> list[dict]:
    """Returns a list of edge dicts: {"edge_id", "from_node", "to_node",
    "direction" ("directed"|"bidirectional"|"undirected"), "confidence",
    "needs_review"}. `nodes` must already have "shape_bbox" set (see
    pair_shapes) — nodes are masked out before line detection using it."""
    h, w = gray.shape
    masked = gray.copy()
    mask_pad = 4
    for node in nodes:
        x, y, bw, bh = node["shape_bbox"]
        x0, y0 = max(0, x - mask_pad), max(0, y - mask_pad)
        x1, y1 = min(w, x + bw + mask_pad), min(h, y + bh + mask_pad)
        masked[y0:y1, x0:x1] = 255

    edges_img = cv2.Canny(masked, 50, 150, apertureSize=3)
    raw = cv2.HoughLinesP(edges_img, 1, np.pi / 180, threshold=30,
                           minLineLength=25, maxLineGap=8)

    if raw is None:
        # No straight segments anywhere is NOT "no connectors" — it is the
        # normal result for an all-curved diagram. Fall through to the
        # contour pass instead of returning early, which is what this
        # function used to do (and why the curved family scored zero).
        merged = []
    else:
        segs = [tuple(int(v) for v in line.flatten()) for line in raw]
        merged = _merge_segments(segs)
        merged = [m for m in merged if math.hypot(m[2] - m[0], m[3] - m[1]) >= MIN_EDGE_LENGTH_PX]

    # node_pair -> best candidate edge (by confidence), so a line broken
    # into near-duplicate merged segments (or detected from both directions
    # of the same drawn arrow) doesn't produce duplicate edges between the
    # same two nodes. The contour pass below feeds the same dict, so a
    # straight connector found by BOTH detectors is still one edge — kept at
    # whichever detector was more confident about it.
    best_by_pair: dict[frozenset, dict] = {}

    for x1, y1, x2, y2 in merged:
        length = math.hypot(x2 - x1, y2 - y1)
        ux, uy = (x1 - x2) / length, (y1 - y2) / length  # unit dir away from p2, through p1

        a_id = _match_endpoint(x1, y1, ux, uy, nodes)
        b_id = _match_endpoint(x2, y2, -ux, -uy, nodes)
        if a_id is None or b_id is None or a_id == b_id:
            continue

        score_a = _arrow_density(x1, y1, gray)
        score_b = _arrow_density(x2, y2, gray)
        mid_score = _arrow_density((x1 + x2) / 2, (y1 + y2) / 2, gray, radius=6)
        head_a = score_a > mid_score + ARROW_DENSITY_MARGIN
        head_b = score_b > mid_score + ARROW_DENSITY_MARGIN

        if head_a and not head_b:
            direction, src, dst = "directed", b_id, a_id
        elif head_b and not head_a:
            direction, src, dst = "directed", a_id, b_id
        elif head_a and head_b:
            direction, src, dst = "bidirectional", a_id, b_id
        else:
            direction, src, dst = "undirected", a_id, b_id

        length_conf = min(1.0, length / 100)
        direction_conf = 0.7 if direction != "undirected" else 0.5
        confidence = round(length_conf * direction_conf, 3)

        pair_key = frozenset((a_id, b_id))
        existing = best_by_pair.get(pair_key)
        if existing is None or confidence > existing["confidence"]:
            best_by_pair[pair_key] = {
                "from_node": src, "to_node": dst, "direction": direction,
                "confidence": confidence, "detector": "hough", "curviness": None,
            }

    for candidate in _detect_connector_contours(gray, masked, nodes):
        pair_key = frozenset((candidate["a_id"], candidate["b_id"]))
        existing = best_by_pair.get(pair_key)
        if existing is None or candidate["confidence"] > existing["confidence"]:
            best_by_pair[pair_key] = {
                "from_node": candidate["from_node"], "to_node": candidate["to_node"],
                "direction": candidate["direction"], "confidence": candidate["confidence"],
                "detector": "contour", "curviness": candidate["curviness"],
            }

    edges = []
    for i, edge in enumerate(best_by_pair.values()):
        edges.append({
            "edge_id": f"e{i + 1}",
            "from_node": edge["from_node"],
            "to_node": edge["to_node"],
            "direction": edge["direction"],
            "confidence": edge["confidence"],
            # Which detector won this edge, and (contour pass only) how far
            # the stroke deviates from straight. Provenance only — nothing
            # scores on it — but it is what lets a bad edge be blamed on the
            # right half of this module, the same reason each node carries
            # the OCR engine that read it.
            "detector": edge["detector"],
            "curviness": edge["curviness"],
            # Same convention as answer_blocks.confidence_score (PROJECT_CONTEXT.md
            # §5): a low-confidence detection is routed for human review, not
            # trusted or silently dropped.
            "needs_review": edge["confidence"] < 0.5,
        })
    return edges
