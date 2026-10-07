"""Image helpers shared by the engines: decode, crop a line, encode."""

import cv2
import numpy as np
from numpy.typing import NDArray

from tarn_core.domain.common import Box
from tarn_core.errors import EngineFailedError, UnreadableFileError
from tarn_core.services.images import check_image_size

type Image = NDArray[np.uint8]


def decode(data: bytes) -> Image:
    """BGR pixels of a JPEG or PNG; ``EngineFailedError`` when the bytes are not an image or
    the header declares too many pixels (checked before decoding)."""
    try:
        check_image_size(data)
    except UnreadableFileError:
        raise EngineFailedError("the page image cannot be decoded") from None
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise EngineFailedError("the page image cannot be decoded")
    return as_image(image)


def as_image(pixels: object) -> Image:
    """OpenCV's results as 8-bit pixels (OpenCV's stubs type them loosely)."""
    return np.asarray(pixels, dtype=np.uint8)


def encode_jpeg(image: Image, quality: int = 92) -> bytes:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:  # pragma: no cover  (OpenCV encodes any 8-bit BGR image)
        raise EngineFailedError("the page image cannot be encoded")
    return bytes(encoded)


def clip(box: Box, width: int, height: int) -> Box | None:
    """The box inside the image, or None when nothing of it is left."""
    x0, y0 = max(0, box.x0), max(0, box.y0)
    x1, y1 = min(width, box.x1), min(height, box.y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return Box(x0=x0, y0=y0, x1=x1, y1=y1)


def crop(image: Image, box: Box, pad: int = 0) -> Image | None:
    """The line's pixels with ``pad`` pixels around it (clipped to the image)."""
    height, width = image.shape[:2]
    padded = clip(
        Box(
            x0=max(0, box.x0 - pad),
            y0=max(0, box.y0 - pad),
            x1=box.x1 + pad,
            y1=box.y1 + pad,
        ),
        width,
        height,
    )
    if padded is None:
        return None
    return image[padded.y0 : padded.y1, padded.x0 : padded.x1]


def polygon_box(points: list[tuple[float, float]], width: int, height: int) -> Box | None:
    """The enclosing box of a polygon in pixels, clipped to the image."""
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = int(np.floor(min(xs))), int(np.floor(min(ys)))
    x1, y1 = int(np.ceil(max(xs))), int(np.ceil(max(ys)))
    x0, y0 = max(0, x0), max(0, y0)
    if x1 <= x0 or y1 <= y0:
        return None
    return clip(Box(x0=x0, y0=y0, x1=x1, y1=y1), width, height)
