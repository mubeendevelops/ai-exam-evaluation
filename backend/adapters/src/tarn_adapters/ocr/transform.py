"""The core's ``PageTransform`` with OpenCV: quarter turns and downscaling of page images."""

import cv2

from tarn_adapters.ocr.images import as_image, decode, encode_jpeg
from tarn_core.errors import InvariantError

_TURNS = {
    90: cv2.ROTATE_90_CLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
}


class OpenCvPageTransform:
    def rotate(self, image: bytes, degrees: int) -> bytes:
        if degrees not in _TURNS:
            raise InvariantError("pages turn by 90, 180 or 270 degrees")
        return encode_jpeg(as_image(cv2.rotate(decode(image), _TURNS[degrees])))

    def size(self, image: bytes) -> tuple[int, int]:
        height, width = decode(image).shape[:2]
        return width, height

    def downscale(self, image: bytes, max_edge: int) -> bytes:
        pixels = decode(image)
        height, width = pixels.shape[:2]
        scale = max_edge / max(height, width)
        if scale >= 1:
            return image
        small = cv2.resize(
            pixels, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
        )
        return encode_jpeg(as_image(small))
