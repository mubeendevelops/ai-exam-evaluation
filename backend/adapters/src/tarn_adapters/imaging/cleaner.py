"""``PageCleaner`` on OpenCV: orient, crop to the main page, flatten, level, de-shadow, compress."""

import cv2
import numpy as np

from tarn_adapters.imaging import enhance, locate, orient
from tarn_core.domain.booklet import PageMetrics
from tarn_core.domain.common import EngineRef
from tarn_core.errors import UnreadableFileError
from tarn_core.ports.pages import CleanedPage

MAX_PIXELS = 120_000_000
"""Refuse images above this size before decoding (a decompression bomb guard)."""
_CLEANER_VERSION = "1"


def decode(data: bytes) -> locate.Image:
    """Decode an image file (EXIF orientation applied). Raises ``UnreadableFileError``."""
    if not data:
        raise UnreadableFileError("the image is empty")
    try:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    except cv2.error:
        raise UnreadableFileError("the image could not be decoded") from None
    if image is None:
        raise UnreadableFileError("the image could not be decoded")
    if image.shape[0] * image.shape[1] > MAX_PIXELS:
        raise UnreadableFileError("the image is too large")
    return image


def _warp(image: locate.Image, quad: locate.Quad, scale: float) -> locate.Image:
    full = quad / scale
    top_left, top_right, bottom_right, bottom_left = full
    width = int(
        max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left))
    )
    height = int(
        max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right))
    )
    width, height = max(width, 16), max(height, 16)
    target = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], np.float32
    )
    matrix = cv2.getPerspectiveTransform(full.astype(np.float32), target)
    return cv2.warpPerspective(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


class OpenCvPageCleaner:
    def __init__(self, *, max_edge_px: int = 2200, max_bytes: int = 800_000) -> None:
        self._max_edge = max_edge_px
        self._max_bytes = max_bytes

    @property
    def ref(self) -> EngineRef:
        return EngineRef(
            name="opencv-page-cleaner", version=f"{_CLEANER_VERSION}+cv{cv2.__version__}"
        )

    def clean(self, data: bytes) -> CleanedPage:
        source = decode(data)
        source_height, source_width = source.shape[:2]

        sideways = orient.is_sideways(source)
        turns = orient.GUESSED_TURNS if sideways else 0
        upright = orient.rotate(source, turns)

        small, scale = locate.shrink(upright)
        location = locate.locate_page(small)
        page = upright
        if location.quad is not None:
            page = _warp(upright, location.quad, scale)
        cropped = location.quad is not None

        degrees = orient.skew_degrees(page)
        page = orient.deskew(page, degrees)
        glare = enhance.glare_share(page)
        page = enhance.reduce_shadows(page)
        quality = enhance.sharpness(page)
        encoded, final = enhance.encode_jpeg(page, self._max_edge, self._max_bytes)

        metrics = PageMetrics(
            sharpness=None if quality is None else round(quality, 2),
            glare_share=round(glare, 4),
            page_found=location.found,
            page_area_share=round(location.area_share, 3),
            source_width=source_width,
            source_height=source_height,
            rotation_degrees=90 * turns,
            rotation_guessed=sideways,
            skew_degrees=round(degrees, 2),
            cropped=cropped,
            perspective_corrected=location.perspective,
            neighbour_removed=location.neighbour_removed,
        )
        return CleanedPage(
            image=encoded,
            media_type="image/jpeg",
            width=int(final.shape[1]),
            height=int(final.shape[0]),
            metrics=metrics,
        )
