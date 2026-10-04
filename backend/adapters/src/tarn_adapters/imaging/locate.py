"""Find the main page in an upright photo: its outline, whether a neighbouring page shares the
shot, and how much of the frame it covers.

Cues, because no single one survives shadows, dark tables and notebook spreads:

* *content*: thin dark strokes (ink and ruled lines) found with a black-hat filter divided by
  the local brightness, so a shadow does not hide them; the dense core of them is the writing;
* *paper*: the brightest / most paper-coloured region (two segmentations: lightness, chroma);
  a clear edge is trusted, a faint one is accepted only if it covers the content core, so an
  outline that cuts off a shadowed corner of the writing is refused;
* *crease*: the dark valley or abrupt step where a neighbouring page of a notebook spread meets
  the main page, measured in the red channel (the red margin line is bright there).

A bleached document scan is left whole. A photo whose border is all paper (a page that fills
the shot) is not cropped unless a crease is found. When no paper outline passes, the
content's outline is used."""

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

# OpenCV's own typing returns loosely-typed arrays; pixels are always uint8 in practice.
Image = NDArray[Any]
Quad = NDArray[Any]

SMALL_EDGE = 900
_STROKE_THRESHOLD = 0.12
_MIN_CORE_SHARE = 0.03
_MIN_PAGE_SHARE = 0.2
_MIN_COVERAGE = 0.97
_MIN_CONTRAST = 1.2
_SURE_CONTRAST = 2.5
_FULL_FRAME_SHARE = 0.95
_BLEACHED_LEVEL = 245
_EXPAND = 0.05
_MIN_CONTENT_CROP_SHARE = 0.6
_CREASE_STRONG = 0.12
_CREASE_WEAK = 0.06
_CREASE_WEAK_DENSITY = 0.7
_STRIP_MAX_SHARE = 0.25
_MARGIN_REDNESS = 30.0
_STRIP_MIN_SHARE = 0.03


@dataclass(frozen=True, slots=True)
class PageLocation:
    quad: Quad | None
    """Corners (top-left, top-right, bottom-right, bottom-left) in the small image; None when
    the whole frame is the page."""
    found: bool
    area_share: float
    neighbour_removed: bool
    perspective: bool


def shrink(image: Image, edge: int = SMALL_EDGE) -> tuple[Image, float]:
    scale = edge / max(image.shape[:2])
    if scale >= 1:
        return image, 1.0
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), scale


def stroke_map(gray: Image) -> Image:
    """1.0 where a thin dark stroke is, relative to the local brightness."""
    local = cv2.GaussianBlur(gray, (0, 0), 20).astype(np.float32)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    hat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel).astype(np.float32)
    return (hat / (local + 20) > _STROKE_THRESHOLD).astype(np.float32)


def _largest(mask: Image) -> Image:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return np.zeros_like(mask)
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.asarray(labels == best, dtype=np.uint8)


def _core(strokes: Image) -> Image:
    dense = cv2.boxFilter(strokes, -1, (31, 31))
    core: Image = np.asarray(dense > 0.06, dtype=np.uint8) * 255
    core = cv2.morphologyEx(core, cv2.MORPH_CLOSE, np.ones((41, 41), np.uint8))
    core = cv2.morphologyEx(core, cv2.MORPH_OPEN, np.ones((25, 25), np.uint8))
    return _largest(core)


def _hull_mask(mask: Image) -> Image:
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    out = np.zeros(mask.shape[:2], np.uint8)
    if contours:
        cv2.fillConvexPoly(out, cv2.convexHull(max(contours, key=cv2.contourArea)), 1)
    return out


def _is_flat(gray: Image) -> bool:
    """The outer ring of the shot is all paper: nothing to crop away."""
    height, width = gray.shape
    ring = max(2, int(0.02 * min(height, width)))
    border = np.concatenate(
        [gray[:ring].ravel(), gray[-ring:].ravel(), gray[:, :ring].ravel(), gray[:, -ring:].ravel()]
    )
    paper = float(
        np.percentile(gray[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4], 70)
    )
    return float((border >= 0.8 * paper).mean()) >= 0.92


def _is_bleached(gray: Image) -> bool:
    """A document scan: the paper has been whitened to the maximum. Scanner apps crop to one
    page, so there is no table to cut away and no neighbouring page to find."""
    height, width = gray.shape
    centre = gray[height // 4 : 3 * height // 4, width // 4 : 3 * width // 4]
    return float(np.percentile(centre, 75)) >= _BLEACHED_LEVEL


def _paper_regions(small: Image) -> list[tuple[Image, float]]:
    """Convex paper outlines with their contrast to the surroundings (lightness, chroma)."""
    height, width = small.shape[:2]
    blurred = cv2.medianBlur(cv2.GaussianBlur(small, (0, 0), 1.5), 21)
    lab = cv2.cvtColor(blurred, cv2.COLOR_BGR2LAB).astype(np.float32)
    centre = (
        slice(int(height * 0.35), int(height * 0.65)),
        slice(int(width * 0.35), int(width * 0.65)),
    )
    segmentations: list[Image] = []

    lightness = cv2.normalize(lab[:, :, 0], None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)  # type: ignore[call-overload]  # stubs reject dst=None
    _, bright = cv2.threshold(lightness, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    segmentations.append(bright)

    chroma = lab.copy()
    chroma[:, :, 0] *= 0.35
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    cv2.setRNGSeed(1)
    _, _, centres = cv2.kmeans(  # type: ignore[call-overload]  # stubs reject bestLabels=None
        chroma[::4, ::4].reshape(-1, 3), 2, None, criteria, 3, cv2.KMEANS_PP_CENTERS
    )
    labels = np.stack([np.linalg.norm(chroma - c, axis=2) for c in centres], axis=2).argmin(2)
    paper_label = int(np.argmax([(labels[centre] == i).mean() for i in range(2)]))
    segmentations.append((labels == paper_label).astype(np.uint8) * 255)

    regions = []
    for seg in segmentations:
        if (seg[centre] > 0).mean() < 0.5:
            seg = 255 - seg
        seg = cv2.morphologyEx(seg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        seg = cv2.morphologyEx(seg, cv2.MORPH_OPEN, np.ones((21, 21), np.uint8))
        hull = _hull_mask(_largest(seg))
        regions.append((hull, _contrast(lab, hull)))
    return regions


def _contrast(lab: NDArray[np.float32], region: Image) -> float:
    inside, outside = lab[region == 1], lab[region == 0]
    if len(inside) < 100:
        return 0.0
    if len(outside) < 0.05 * region.size:
        return float("inf")  # the paper fills the frame
    spread = np.sqrt(inside.var(0).sum() + outside.var(0).sum()) + 1e-6
    return float(np.linalg.norm(inside.mean(0) - outside.mean(0)) / spread)


def _find_crease(small: Image, strokes: Image, x0: int, x1: int) -> tuple[int, bool] | None:
    """Column of a crease with a narrow neighbouring page beyond it, as ``(column,
    strip_on_left)``, searched between columns ``x0`` and ``x1``.

    The score is how far the red channel falls into the crease: a valley relative to the main
    page (strong), or an abrupt step (strong); a weaker fall counts only when the strip's rules
    are visibly sparser than the main page's (the neighbour is tilted and foreshortened)."""
    height, frame_width = small.shape[:2]
    width = x1 - x0
    rows = slice(int(height * 0.15), int(height * 0.85))
    # Medians over the rows: ink is sparse, so they follow the paper (and a crease, which runs
    # the whole height) and not the writing, whose dark strokes would fake steps.
    blue, green, red_channel = (
        np.median(small[rows, :, i], axis=0).astype(np.float32) for i in range(3)
    )
    red = np.convolve(red_channel, np.ones(3) / 3, "same")
    gray = np.median(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)[rows], axis=0).astype(np.float32)
    density = strokes[rows].mean(axis=0)
    redness = red_channel - (blue + green) / 2
    reaches_left, reaches_right = x0 <= 0.02 * frame_width, x1 >= 0.98 * frame_width
    window = max(8, int(0.06 * width))
    best: tuple[float, int, bool] | None = None
    for left in (True, False):
        if not (reaches_left if left else reaches_right):
            continue  # a neighbour's page runs on beyond the edge of the shot
        lo, hi = (
            (x0 + int(_STRIP_MIN_SHARE * width), x0 + int(_STRIP_MAX_SHARE * width))
            if left
            else (x1 - int(_STRIP_MAX_SHARE * width), x1 - int(_STRIP_MIN_SHARE * width))
        )
        for c in range(max(lo, window + 10), min(hi, small.shape[1] - window - 10)):
            if left:
                main = red[c + 4 : c + window + 4].mean()
                step = (red[c - 9 : c - 1].mean() - red[c + 1 : c + 9].mean()) / red[
                    c - 9 : c - 1
                ].mean()
                strip = slice(x0, c - 3)
                rest = slice(c + 4, x1)
            else:
                main = red[c - window - 4 : c - 4].mean()
                step = (red[c - 9 : c - 1].mean() - red[c + 1 : c + 9].mean()) / red[
                    c - 9 : c - 1
                ].mean()
                strip = slice(c + 4, x1)
                rest = slice(x0, c - 3)
            score = max((main - float(red[c - 2 : c + 3].min())) / main, step)
            if score < _CREASE_WEAK:
                continue
            nearby = np.r_[redness[c - 12 : c - 5], redness[c + 6 : c + 13]].mean()
            if float(redness[c - 2 : c + 3].max() - nearby) > _MARGIN_REDNESS:
                continue  # a red line: the booklet's margin, not a crease
            strip_density, main_density = density[strip].mean(), density[rest].mean()
            if gray[strip].mean() < 0.6 * gray[rest].mean() or strip_density < 0.35 * main_density:
                continue
            if score < _CREASE_STRONG and strip_density > _CREASE_WEAK_DENSITY * main_density:
                continue
            if best is None or score > best[0]:
                best = (score, c, left)
    return None if best is None else (best[1], best[2])


def _order(points: Quad) -> Quad:
    sums, diffs = points.sum(axis=1), np.diff(points, axis=1).ravel()
    return np.array(
        [
            points[np.argmin(sums)],
            points[np.argmin(diffs)],
            points[np.argmax(sums)],
            points[np.argmax(diffs)],
        ],
        dtype=np.float32,
    )


def _quad(region: Image) -> tuple[Quad, bool]:
    """Corners of the region and whether they are a true four-sided outline (so a perspective
    correction is safe). Anything else becomes the enclosing rotated rectangle: it may keep a
    sliver of background but never bends the writing."""
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    area, perimeter = cv2.contourArea(hull), cv2.arcLength(hull, True)
    for epsilon in (0.01, 0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(hull, epsilon * perimeter, True)
        if len(approx) == 4 and cv2.contourArea(approx) >= 0.95 * area:
            return _order(approx.reshape(4, 2).astype(np.float32)), True
    return _order(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)), False


def _expand(quad: Quad, factor: float, width: int, height: int) -> Quad:
    centre = quad.mean(axis=0)
    grown = centre + (quad - centre) * (1 + factor)
    grown[:, 0] = np.clip(grown[:, 0], 0, width - 1)
    grown[:, 1] = np.clip(grown[:, 1], 0, height - 1)
    return np.asarray(grown, dtype=np.float32)


def locate_page(small: Image) -> PageLocation:
    height, width = small.shape[:2]
    frame = float(height * width)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    strokes = stroke_map(gray)
    core = _core(strokes)
    has_core = core.sum() >= _MIN_CORE_SHARE * frame

    if _is_bleached(gray):
        return PageLocation(None, True, 1.0, False, False)

    region: Image | None = None
    expand = 0.0
    if _is_flat(gray):
        region = np.ones((height, width), np.uint8)
    else:
        for candidate, contrast in sorted(_paper_regions(small), key=lambda r: -r[1]):
            if contrast < _MIN_CONTRAST or candidate.sum() < _MIN_PAGE_SHARE * frame:
                continue
            # A clear edge is trusted (a table's grain pollutes the content core, so coverage
            # means nothing there); a faint one must not cut off the writing.
            if (
                contrast < _SURE_CONTRAST
                and has_core
                and candidate[core == 1].mean() < _MIN_COVERAGE
            ):
                continue
            region = candidate
            break
        if region is None and has_core:
            hull = _hull_mask(core)
            if hull.sum() >= _MIN_CONTENT_CROP_SHARE * frame:
                region, expand = hull, _EXPAND
            else:
                region = np.ones((height, width), np.uint8)
    if region is None:
        return PageLocation(None, has_core, 0.0, False, False)

    columns = np.nonzero(region.any(axis=0))[0]
    removed = False
    crease = _find_crease(small, strokes, int(columns[0]), int(columns[-1]) + 1)
    if crease is not None:
        column, strip_on_left = crease
        region = region.copy()
        if strip_on_left:
            region[:, :column] = 0
        else:
            region[:, column:] = 0
        removed = True
    share = float(region.sum()) / frame
    if share >= _FULL_FRAME_SHARE and not removed:
        return PageLocation(None, True, 1.0, False, False)
    quad, perspective = _quad(_hull_mask(_largest(region)))
    quad = _expand(quad, expand, width, height)
    return PageLocation(quad, True, min(share, 1.0), removed, perspective and expand == 0.0)
