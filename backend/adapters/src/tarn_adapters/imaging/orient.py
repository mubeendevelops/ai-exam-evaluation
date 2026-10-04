"""Orientation and tilt of a page.

Sideways pages are found reliably: text lines and ruled lines make the *rows* of an upright page
strongly periodic, and the *columns* of a sideways one. Which way to turn a sideways page, and
whether an upright-looking page is upside down, cannot be told from the strokes alone: the cues
tried (margin line, flush-left starts, ascender/descender balance) agreed with the truth on
about half of the sample pages. So a quarter turn is applied in a fixed direction and reported
as a guess, an upside-down page is left as it is, and the OCR step (P10) resolves both by
reading the page each way and keeping the one it reads with more confidence."""

import cv2
import numpy as np

from tarn_adapters.imaging.locate import Image, shrink, stroke_map

_ORIENT_EDGE = 800
_SIDEWAYS_RATIO = 1.5
_SKEW_RANGE = 8.0
_MIN_INK_SHARE = 0.01
# Counter-clockwise: how the sample's sideways pages (Adobe Scan, text running bottom to top)
# need turning. Not derivable from the strokes; see the module docstring.
GUESSED_TURNS = 3


def _periodicity(strokes: Image, axis: int) -> float:
    """How peaky the profile of stroke pixels is along ``axis`` (rows: axis=1)."""
    profile = strokes.sum(axis=axis)
    mean = float(profile.mean())
    return 0.0 if mean <= 0 else float(profile.std() / mean)


def is_sideways(image: Image) -> bool:
    """The writing runs vertically: its columns are much more periodic than its rows."""
    small, _ = shrink(image, _ORIENT_EDGE)
    strokes = stroke_map(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    rows, columns = _periodicity(strokes, 1), _periodicity(strokes, 0)
    return rows > 0 and columns / rows >= _SIDEWAYS_RATIO


def rotate(image: Image, turns: int) -> Image:
    """Clockwise quarter turns."""
    code = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return image if turns % 4 == 0 else cv2.rotate(image, code[turns % 4])


def skew_degrees(image: Image) -> float:
    """Angle (degrees, counter-clockwise positive) to rotate by so text lines are level, or 0."""
    small, _ = shrink(image, 600)
    strokes = stroke_map(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
    height, width = strokes.shape
    if float(strokes.mean()) < _MIN_INK_SHARE:
        return 0.0  # too little writing to fit a direction to
    centre = (width / 2, height / 2)

    def score(angle: float) -> float:
        matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
        turned = cv2.warpAffine(strokes, matrix, (width, height), flags=cv2.INTER_LINEAR)
        return _periodicity(turned, 1)

    baseline = score(0.0)
    coarse = max((float(a) for a in np.arange(-_SKEW_RANGE, _SKEW_RANGE + 0.01, 1.0)), key=score)
    fine = max((float(a) for a in np.arange(coarse - 0.9, coarse + 0.91, 0.3)), key=score)
    if abs(fine) < 0.4 or score(float(fine)) < baseline * 1.03:
        return 0.0
    return float(fine)


def deskew(image: Image, degrees: float) -> Image:
    if degrees == 0.0:
        return image
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    return cv2.warpAffine(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
