"""Booklet ingestion — scanned answer-booklet PDF to stored page images.

The first stage of the full-booklet pipeline (Task 5). Everything upstream of
this module in the repo assumed a single pre-cropped image of a single answer;
this module turns one multi-page PDF into a list of deskewed, denoised page
images with stable "bucket/key" storage refs, ready for
core/booklet_segmenter.py to cut into regions.

PAGE RASTERIZATION uses pypdfium2, deliberately over pdf2image: pdf2image
shells out to poppler's `pdftoppm`, an OS binary that is not pip-installable
and would become a second setup step of exactly the kind
core/ocr_engines/tesseract_engine.py already has to apologize for. pypdfium2
bundles its own libpdfium and needs nothing installed outside the venv.

PREPROCESSING is deliberately mild — a deskew that refuses to act when it is
unsure, plus a light denoise. This is a considered position, not laziness:
CLAUDE_CONTEXT.md §7/§7B record that this project's real input distribution is
photographs of ruled notebook paper, and that aggressive preprocessing is what
previously destroyed handwriting on it. A page that is slightly crooked reads
fine; a page that has been rotated 40 degrees because a diagram's dominant ink
direction fooled minAreaRect does not.

MEASURED SCOPE: on media/booklets/sample_booklet.pdf, whose pages 2 and 4 are
rendered deliberately crooked at -1.6 and +1.1 degrees, deskew recovers +1.60
and -1.10 and leaves a residual skew of 0.00 on all four pages. NOT validated
on true camera photos of booklets — no such fixture exists in this repo — so
perspective correction is not attempted at all, only in-plane rotation.
Stating that here rather than letting the absence read as coverage.
"""
import io
import os
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from core import storage
from core.diagram_shapes import binarize


# --- tunables ----------------------------------------------------------
#
# DPI: 200 is the point where PaddleOCR's recognizer stops losing thin
# handwriting strokes on A4 without quadrupling inference time. 300 is the
# scanning convention but produces ~2.2x the pixels for no measured gain on
# this repo's fixtures; 150 visibly degrades small sub-part markers ("a)").
DEFAULT_DPI = 200

# Deskew refuses to rotate beyond this. A real scan/photo is off by a few
# degrees; anything larger is far more likely to be minAreaRect locking onto
# a diagram's dominant edge than a genuinely sideways page. See _detect_skew.
MAX_DESKEW_DEGREES = 15.0

# Below this the rotation costs an interpolation pass and gains nothing.
MIN_DESKEW_DEGREES = 0.15

# fastNlMeansDenoising strength. 7 is gentle; the default 10 was measurably
# too aggressive on Caveat-font thin strokes during the fixture spike.
DENOISE_STRENGTH = 7

STORAGE_KEY_PREFIX_PAGES = "booklets/pages"
STORAGE_KEY_PREFIX_SOURCE = "booklets/source"


def render_pdf_pages(pdf_path, *, dpi: int = DEFAULT_DPI) -> list[Image.Image]:
    """Rasterize every page of `pdf_path` to an RGB PIL Image at `dpi`.

    pypdfium2's scale is in points-per-pixel units where 1.0 == 72 DPI.
    Raises ValueError on a PDF with zero pages rather than silently
    returning an empty booklet that later reads as "nothing was detected".
    """
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if len(pdf) == 0:
            raise ValueError(f"PDF has no pages: {pdf_path}")
        scale = dpi / 72.0
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            bitmap = page.render(scale=scale)
            pages.append(bitmap.to_pil().convert("RGB"))
        return pages
    finally:
        pdf.close()


def _skew_score(ink: np.ndarray, angle: float) -> float:
    """Sharpness of the horizontal projection profile at `angle`.

    When a page is straight, every text line's pixels pile into a few rows and
    the row-sum profile becomes a series of tall spikes; when it is crooked the
    same pixels smear across many rows and the profile flattens. The sum of
    squared first differences measures exactly that spikiness, so the angle
    that maximizes it is the angle that straightens the page.
    """
    height, width = ink.shape
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(ink, matrix, (width, height),
                             flags=cv2.INTER_NEAREST, borderValue=0)
    profile = rotated.sum(axis=1, dtype=np.float64)
    return float(np.square(np.diff(profile)).sum())


def _detect_skew(gray: np.ndarray) -> float:
    """Estimate the page's in-plane rotation in degrees, by projection profile.

    Returns the angle to pass to getRotationMatrix2D to CORRECT the page.

    Chosen over the cheaper minAreaRect-over-all-ink classic, which this
    module used first and which measurably failed: on the benchmark booklet's
    page 2 (a wide ruled table plus prose, rendered at -1.6 degrees) the ink
    cloud's minimum-area rectangle is dominated by the table's own axis-aligned
    extent, and the estimate came back 0.0 -- a crooked page reported as
    straight. A projection profile keys on the text baselines themselves, so a
    table helps it rather than defeating it.

    Two-stage search (coarse 1.0 deg, then fine 0.1 deg around the winner) so
    the cost stays ~35 warpAffines on a downscaled image rather than 300.
    """
    ink = binarize(gray)

    # Downscale: skew is a global property and full resolution buys nothing
    # but time. Cap the long edge at ~1000px.
    scale = min(1.0, 1000.0 / max(ink.shape))
    if scale < 1.0:
        ink = cv2.resize(ink, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    if cv2.countNonZero(ink) < 50:
        return 0.0

    coarse = np.arange(-MAX_DESKEW_DEGREES, MAX_DESKEW_DEGREES + 0.5, 1.0)
    best = max(coarse, key=lambda a: _skew_score(ink, a))

    fine = np.arange(best - 1.0, best + 1.0 + 0.05, 0.1)
    best = max(fine, key=lambda a: _skew_score(ink, a))
    return float(best)


def deskew(image: Image.Image) -> tuple[Image.Image, float]:
    """Correct a page's in-plane rotation. Returns (image, applied_degrees).

    applied_degrees is 0.0 when the correction was skipped — either because
    the page is already straight (< MIN_DESKEW_DEGREES) or because the
    estimate exceeded MAX_DESKEW_DEGREES and was rejected as untrustworthy.
    The caller gets the applied value, not the raw estimate, so the ingest
    report never claims a rotation that did not happen.
    """
    array = np.array(image)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)
    angle = _detect_skew(gray)

    # The search range is already bounded by MAX_DESKEW_DEGREES, so the
    # upper guard only fires if that constant and the search disagree.
    if abs(angle) < MIN_DESKEW_DEGREES or abs(angle) > MAX_DESKEW_DEGREES:
        return image, 0.0

    height, width = gray.shape
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), angle, 1.0)
    rotated = cv2.warpAffine(
        array, matrix, (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),   # white, so the fill reads as paper
    )
    return Image.fromarray(rotated), float(angle)


def denoise(image: Image.Image) -> Image.Image:
    """Light speckle removal. Colored non-local-means, then nothing else —
    no sharpening, no adaptive threshold, no morphology. The stored page must
    stay a faithful readable scan, because it is both the OCR input AND what
    a human reviewer looks at when a region gets flagged."""
    array = np.array(image)
    cleaned = cv2.fastNlMeansDenoisingColored(
        array, None,
        h=DENOISE_STRENGTH, hColor=DENOISE_STRENGTH,
        templateWindowSize=7, searchWindowSize=21,
    )
    return Image.fromarray(cleaned)


def prepare_page(image: Image.Image, *, skip_denoise: bool = False) -> tuple[Image.Image, dict]:
    """deskew + denoise, returning the prepared page and its provenance.

    skip_denoise exists because denoising is by far the slowest step here
    (~1s per A4 page at 200 DPI) and is pure waste on a rendered PDF that has
    no scan noise — scripts/generate_booklet_benchmark.py output, and the
    test suite.
    """
    deskewed, applied = deskew(image)
    prepared = deskewed if skip_denoise else denoise(deskewed)
    return prepared, {
        "deskew_degrees": round(applied, 2),
        "denoised": not skip_denoise,
        "width": prepared.width,
        "height": prepared.height,
    }


def _upload(local_path: str, key_prefix: str, *, storage_mode: str, asset_id: str) -> str:
    """Upload through core/storage.py, branching on storage_mode inline.

    There is no dummy-vs-minio factory in this repo; scripts/load_exam_bank.py
    branches the same way. Kept in one private helper here so the three call
    sites in this module cannot drift apart.
    """
    if storage_mode == "dummy":
        return storage.dummy_upload(local_path, key_prefix, asset_id)
    if storage_mode == "minio":
        suffix = Path(local_path).suffix.lower()
        content_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".pdf": "application/pdf",
        }.get(suffix, "application/octet-stream")
        return storage.upload_file(local_path, key_prefix, content_type=content_type)
    raise ValueError(f"unknown storage_mode {storage_mode!r} (expected 'dummy' or 'minio')")


def ingest_booklet(
    pdf_path,
    *,
    storage_mode: str = "dummy",
    dpi: int = DEFAULT_DPI,
    skip_denoise: bool = False,
) -> dict:
    """Rasterize, preprocess and store every page of a booklet PDF.

    Returns:
        {
          "source_pdf_url": "<bucket>/<key>",     # the untouched original
          "page_count": int,
          "pages": [
            {"page_number": 1,                    # 1-based
             "image": <PIL.Image>,                # in memory, for segmentation
             "page_image_url": "<bucket>/<key>",
             "deskew_degrees": 0.0, "denoised": bool,
             "width": int, "height": int},
            ...
          ],
        }

    The prepared PIL Image is handed back in memory rather than re-downloaded
    from storage by the next stage: the segmenter runs immediately after, and
    a round-trip through MinIO to fetch a file we already have would be pure
    latency. It also lets the whole pipeline run under --storage dummy, where
    there is no object to download (core/diagram_extractor.py::_load_image
    raises on "dummy-storage/" refs for exactly that reason).
    """
    pdf_path = str(pdf_path)
    if not os.path.exists(pdf_path):
        raise ValueError(f"PDF not found: {pdf_path}")

    rendered = render_pdf_pages(pdf_path, dpi=dpi)
    source_pdf_url = _upload(
        pdf_path, STORAGE_KEY_PREFIX_SOURCE,
        storage_mode=storage_mode, asset_id=str(uuid.uuid4()),
    )

    pages = []
    for index, raw in enumerate(rendered, start=1):
        prepared, provenance = prepare_page(raw, skip_denoise=skip_denoise)

        # Storage uploads from a path, so the prepared page has to land on
        # disk somewhere. A NamedTemporaryFile that we delete ourselves keeps
        # it out of the repo tree entirely.
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            temp_path = handle.name
        try:
            prepared.save(temp_path, format="PNG")
            page_image_url = _upload(
                temp_path, STORAGE_KEY_PREFIX_PAGES,
                storage_mode=storage_mode, asset_id=str(uuid.uuid4()),
            )
        finally:
            os.unlink(temp_path)

        pages.append({
            "page_number": index,
            "image": prepared,
            "page_image_url": page_image_url,
            **provenance,
        })

    return {
        "source_pdf_url": source_pdf_url,
        "page_count": len(pages),
        "pages": pages,
    }
