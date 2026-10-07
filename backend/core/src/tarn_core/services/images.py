"""Image sizes read from the file header, before anything decodes the pixels (P21).

A PNG or JPEG of a few kilobytes can declare a picture of billions of pixels (a
"decompression bomb"): decoding it first and measuring afterwards already costs the memory.
``image_dimensions`` reads only the header (PNG ``IHDR``, JPEG ``SOFn``), so uploads and every
decoder can refuse such a file up front."""

import struct

from tarn_core.errors import UnreadableFileError

MAX_IMAGE_PIXELS = 120_000_000
"""About 11 000 × 11 000: far above any phone camera (50 MP) or a 600 dpi A3 scan."""

_PNG = b"\x89PNG\r\n\x1a\n"
# SOF0..SOF15 carry the frame size; C4 (DHT), C8 (JPG extension) and CC (DAC) do not.
_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_STANDALONE = frozenset({0x01, *range(0xD0, 0xD9)})


def _png(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg(data: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in _STANDALONE:
            i += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image, or scan data before any frame header
            return None
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if length < 2:
            return None
        if marker in _SOF:
            if i + 9 > n:
                return None
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """``(width, height)`` from a PNG or JPEG header; None if it is neither or the header is
    unreadable."""
    if data[:8] == _PNG:
        return _png(data)
    if data[:3] == b"\xff\xd8\xff":
        return _jpeg(data)
    return None


def check_image_size(data: bytes, max_pixels: int = MAX_IMAGE_PIXELS) -> tuple[int, int]:
    """The picture's size, or ``UnreadableFileError`` when the header cannot be read, a side
    is zero, or it has more than ``max_pixels`` pixels."""
    size = image_dimensions(data)
    if size is None:
        raise UnreadableFileError("the image header could not be read")
    width, height = size
    if width == 0 or height == 0:
        raise UnreadableFileError("the image has no pixels")
    if width * height > max_pixels:
        raise UnreadableFileError("the image is too large")
    return size
