"""Shadow reduction, quality measurements and compression of a cleaned page."""

import cv2
import numpy as np
from numpy.typing import NDArray

from tarn_adapters.imaging.locate import Image, shrink

_SHARPNESS_EDGE = 1400
_GLARE_LEVEL = 250
_FLAT_WHITE_MEDIAN = 245
_MIN_CONTRAST = 40.0


def reduce_shadows(image: Image) -> Image:
    """Divide each channel by its slowly varying background (paper and shadow, not ink), so
    the paper becomes even white and the writing keeps its colour."""
    height, width = image.shape[:2]
    scale = 400 / max(height, width)
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    background = cv2.dilate(small, kernel)  # ink is dark: dilating removes it
    background = cv2.medianBlur(background, 21)
    background = cv2.GaussianBlur(background, (0, 0), 8)
    background = cv2.resize(background, (width, height), interpolation=cv2.INTER_LINEAR)
    flat = image.astype(np.float32) / np.maximum(background.astype(np.float32), 1.0)
    return np.asarray(np.clip(flat * 255.0 * 0.97, 0, 255), dtype=np.uint8)


def glare_share(image: Image) -> float:
    """Share of the page in large blown-out highlights. A page whose paper is white all over
    (a flat scan) has none: white is its background, not a reflection."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small, _ = shrink(gray, 600) if gray.ndim == 2 else (gray, 1.0)
    if float(np.median(small)) >= _FLAT_WHITE_MEDIAN:
        return 0.0
    blown: Image = np.asarray(small >= _GLARE_LEVEL, dtype=np.uint8)
    blown = cv2.morphologyEx(blown, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    return float(blown.mean())


def sharpness(image: Image) -> float | None:
    """How sharp the page is: the 98th percentile of the gradient magnitude at a fixed scale,
    relative to the page's contrast (paper to darkest ink), times 100. A sharp stroke is a steep
    edge; blur flattens every edge, however faint the writing has become, so this keeps falling
    with blur where a stroke detector would simply find nothing. It does not depend on how much
    is written, as long as the page has some writing. None when the page is (nearly) blank: no
    contrast to judge by."""
    small, _ = shrink(image, _SHARPNESS_EDGE)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 1))
    if contrast < _MIN_CONTRAST:
        return None
    magnitude = np.hypot(
        cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    )
    return float(np.percentile(magnitude, 98) / contrast * 100.0)


def encode_jpeg(image: Image, max_edge: int, max_bytes: int) -> tuple[bytes, Image]:
    """JPEG of at most ``max_bytes`` and ``max_edge`` pixels on the long side: quality steps
    down to 60, then the image shrinks 10% at a time. Returns the bytes and the image encoded."""
    current, _ = shrink(image, max_edge)
    while True:
        for quality in (88, 80, 72, 65, 60):
            ok, encoded = cv2.imencode(".jpg", current, [cv2.IMWRITE_JPEG_QUALITY, quality])
            if not ok:
                raise ValueError("JPEG encoding failed")
            data: NDArray[np.uint8] = encoded
            if len(data) <= max_bytes:
                return data.tobytes(), current
        if max(current.shape[:2]) <= 600:
            return data.tobytes(), current
        current = cv2.resize(current, None, fx=0.9, fy=0.9, interpolation=cv2.INTER_AREA)
