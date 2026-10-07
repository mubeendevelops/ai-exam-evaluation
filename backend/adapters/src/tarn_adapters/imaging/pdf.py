"""``PageSplitter`` on PyMuPDF: a PDF becomes one image per page, in reading order.

A scanned booklet's page *is* a picture, so the embedded picture is taken at its own resolution
(rendering would resample it and draw a scanner's watermark over the writing). Its placement
matrix is honoured: iPhone and Android exports often store a landscape photo and turn it with
the matrix, and the page must come out as the PDF viewer shows it. A page with no dominant
picture (typed pages, vector drawings) is rendered."""

import cv2
import numpy as np
import pymupdf

from tarn_adapters.imaging.cleaner import MAX_PIXELS
from tarn_adapters.imaging.locate import Image
from tarn_core.errors import InvariantError, UnreadableFileError
from tarn_core.ports.storage import PageImage

_MIN_COVERAGE = 0.3
_RENDER_EDGE = 2200
_JPEG_QUALITY = 95


def _turns_from_matrix(matrix: tuple[float, ...]) -> tuple[int, bool]:
    """Clockwise quarter turns, and whether to mirror first, that map the stored picture onto
    the page as the matrix places it (``(a, b)`` is where the picture's right goes, ``(c, d)``
    where its down goes, in page coordinates with y down)."""
    a, b, c, d = matrix[:4]
    right = np.array([a, b], dtype=float)
    down = np.array([c, d], dtype=float)
    if np.linalg.norm(right) == 0 or np.linalg.norm(down) == 0:
        return 0, False
    right, down = right / np.linalg.norm(right), down / np.linalg.norm(down)
    mirrored = float(right[0] * down[1] - right[1] * down[0]) < 0
    if mirrored:
        right = -right
    direction = (round(right[0]), round(right[1]))
    turns = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}.get(direction, 0)
    return turns, mirrored


def _encode(image: Image) -> PageImage:
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not ok:
        raise UnreadableFileError("a page could not be encoded")
    return PageImage(index=0, data=encoded.tobytes(), media_type="image/jpeg")


def _decode(data: bytes) -> Image | None:
    """Decode without applying EXIF: the PDF matrix is the only orientation that counts."""
    try:
        image = cv2.imdecode(
            np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
        )
    except cv2.error:
        return None
    if image is None:
        return None
    if image.shape[0] * image.shape[1] > MAX_PIXELS:
        raise UnreadableFileError("a page image is too large")
    return image


def _embedded(document: pymupdf.Document, page: pymupdf.Page) -> Image | None:
    infos = [info for info in page.get_image_info(xrefs=True) if info.get("xref", 0) > 0]
    if not infos:
        return None
    info = max(infos, key=lambda i: (i["bbox"][2] - i["bbox"][0]) * (i["bbox"][3] - i["bbox"][1]))
    box = info["bbox"]
    coverage = (box[2] - box[0]) * (box[3] - box[1]) / max(page.rect.width * page.rect.height, 1.0)
    if coverage < _MIN_COVERAGE:
        return None
    # The stored size, before PyMuPDF inflates the stream: a few compressed bytes can declare
    # billions of pixels (a decompression bomb).
    if int(info.get("width", 0)) * int(info.get("height", 0)) > MAX_PIXELS:
        raise UnreadableFileError("a page image is too large")
    extracted = document.extract_image(info["xref"])
    if not extracted or extracted.get("smask"):
        return None  # a transparency mask: only rendering gets it right
    image = _decode(extracted["image"])
    if image is None:
        return None
    turns, mirrored = _turns_from_matrix(tuple(info["transform"]))
    if mirrored:
        image = cv2.flip(image, 1)
    code = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}
    return image if turns == 0 else cv2.rotate(image, code[turns])


def _rendered(page: pymupdf.Page) -> Image:
    zoom = min(4.0, _RENDER_EDGE / max(page.rect.width, page.rect.height, 1.0))
    pixmap = page.get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False
    )
    rgb = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, 3)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


class PyMuPdfSplitter:
    def split(self, data: bytes, media_type: str, max_pages: int) -> list[PageImage]:
        if media_type != "application/pdf":
            raise UnreadableFileError("only PDFs are split")
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception:  # PyMuPDF raises several error types for damaged files
            raise UnreadableFileError("the PDF could not be opened") from None
        try:
            if document.needs_pass or document.is_encrypted:
                raise UnreadableFileError("the PDF is password protected")
            if document.is_repaired:
                # Cut short in transit (or damaged): pages may be missing without a trace.
                raise UnreadableFileError("the PDF is damaged; upload it again")
            if document.page_count == 0:
                raise UnreadableFileError("the PDF has no pages")
            if document.page_count > max_pages:
                raise InvariantError(f"the PDF has more than {max_pages} pages")
            pages = []
            for index in range(document.page_count):
                try:
                    page = document.load_page(index)
                    image = _embedded(document, page)
                    if image is None:
                        image = _rendered(page)
                except UnreadableFileError:
                    raise
                except Exception:
                    raise UnreadableFileError(f"page {index + 1} could not be read") from None
                encoded = _encode(image)
                pages.append(PageImage(index=index, data=encoded.data, media_type="image/jpeg"))
            return pages
        finally:
            document.close()
