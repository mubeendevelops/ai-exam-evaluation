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

CONFIRMED SCOPE (checked against the real diagram content in this repo —
media/diagrams/{buses.webp,hand_drawn_buses_image.jpeg},
reference_diagrams/*.json — before writing this): every diagram seen here is
a rectangular block/box diagram with straight-line (possibly double-headed)
arrow connectors — CPU/Memory/Bus style computer-architecture diagrams, not
circuit schematics, flowcharts with decision diamonds, or mind maps. Shape
classification below still reports "ellipse" for round shapes since that
costs nothing extra and other subjects' diagrams (e.g. a water-cycle cycle
diagram with oval stage labels) plausibly need it, but the whole pipeline
has only been tuned/validated against box+straight-arrow diagrams. Treat
non-rectangular, non-straight-line diagram families (circuits, curved
connectors) as unvalidated until tested against a real example.

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
   per the task's own instruction), runs Canny + HoughLinesP on what's
   left, merges near-collinear/adjacent segments into logical lines, then
   matches each segment's two endpoints to the nearest node (within
   REACH_PX, or after projecting up to EXTEND_PX further along the line's
   own direction — bridges the small gap most hand/digital diagrams leave
   between a line's visible end and the box it terminates against).
   Segments that don't resolve to two distinct nodes are dropped, not
   guessed.

   Arrow direction: samples dark-pixel density in a small disk at each
   raw (pre-extension) endpoint and at the segment's midpoint. An
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


def _classify_shape(contour) -> str:
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    n = len(approx)
    if n == 4:
        return "rectangle"
    if n <= 3:
        return "other"
    # Many vertices + low deviation from its own bounding circle reads as
    # round; a rough vertex-count cutoff is enough for the box-diagram
    # domain this has been validated against (see module docstring) without
    # adding a circularity computation this domain doesn't need yet.
    return "ellipse" if n > 6 else "polygon"


def pair_shapes(gray: np.ndarray, nodes: list[dict]) -> list[dict]:
    """Mutates and returns `nodes`, adding "shape_bbox" ([x, y, w, h]) and
    "shape_type" (str | None) to each. See module docstring for the
    matching rule and its known limitation."""
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
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
        return []

    segs = [tuple(int(v) for v in line.flatten()) for line in raw]
    merged = _merge_segments(segs)
    merged = [m for m in merged if math.hypot(m[2] - m[0], m[3] - m[1]) >= MIN_EDGE_LENGTH_PX]

    # node_pair -> best candidate edge (by confidence), so a line broken
    # into near-duplicate merged segments (or detected from both directions
    # of the same drawn arrow) doesn't produce duplicate edges between the
    # same two nodes.
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
                "confidence": confidence,
            }

    edges = []
    for i, edge in enumerate(best_by_pair.values()):
        edges.append({
            "edge_id": f"e{i + 1}",
            "from_node": edge["from_node"],
            "to_node": edge["to_node"],
            "direction": edge["direction"],
            "confidence": edge["confidence"],
            # Same convention as answer_blocks.confidence_score (PROJECT_CONTEXT.md
            # §5): a low-confidence detection is routed for human review, not
            # trusted or silently dropped.
            "needs_review": edge["confidence"] < 0.5,
        })
    return edges
