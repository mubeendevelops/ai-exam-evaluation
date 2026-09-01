"""Tests for core/booklet_ingest.py.

Split by cost, following this suite's existing convention: everything here
runs on the committed media/booklets/sample_booklet.pdf with no model load and
no network, so the whole file is fast. Only the storage-mode branch touches
core/storage.py, and it uses dummy mode, which performs no I/O at all.
"""
from __future__ import annotations

import pathlib

import numpy as np
import pytest
from PIL import Image, ImageDraw

from core import booklet_ingest as bi

BOOKLET_PDF = (pathlib.Path(__file__).resolve().parent.parent
               / "media" / "booklets" / "sample_booklet.pdf")

pytestmark = pytest.mark.skipif(
    not BOOKLET_PDF.exists(),
    reason="run scripts/generate_booklet_benchmark.py to build the fixture",
)


@pytest.fixture(scope="module")
def pages():
    return bi.render_pdf_pages(BOOKLET_PDF, dpi=100)


def _text_page(angle: float = 0.0) -> Image.Image:
    """A synthetic page of ruled 'text' lines, optionally rotated. Bars rather
    than glyphs: deskew keys on the horizontal projection profile, which does
    not care whether the ink spells anything."""
    image = Image.new("RGB", (800, 1000), "white")
    draw = ImageDraw.Draw(image)
    for index in range(12):
        y = 80 + index * 70
        draw.rectangle([80, y, 720, y + 22], fill=(20, 20, 20))
    if angle:
        image = image.rotate(angle, resample=Image.BICUBIC, fillcolor="white")
    return image


def test_render_pdf_pages_returns_every_page(pages):
    assert len(pages) == 4
    assert all(page.mode == "RGB" for page in pages)


def test_dpi_scales_the_raster():
    low = bi.render_pdf_pages(BOOKLET_PDF, dpi=72)[0]
    high = bi.render_pdf_pages(BOOKLET_PDF, dpi=144)[0]
    assert high.width == pytest.approx(low.width * 2, rel=0.02)


def test_render_rejects_a_missing_pdf():
    with pytest.raises(Exception):
        bi.render_pdf_pages(BOOKLET_PDF.parent / "does-not-exist.pdf")


@pytest.mark.parametrize("angle", [-3.0, -1.5, 1.5, 3.0])
def test_deskew_recovers_a_known_rotation(angle):
    """The correction should be the negation of the applied rotation."""
    _, applied = bi.deskew(_text_page(angle))
    assert applied == pytest.approx(-angle, abs=0.35)


def test_deskew_leaves_a_straight_page_alone():
    image = _text_page(0.0)
    result, applied = bi.deskew(image)
    assert applied == 0.0
    assert result is image          # no interpolation pass at all


def test_deskew_output_has_no_residual_skew():
    corrected, _ = bi.deskew(_text_page(2.5))
    gray = np.array(corrected.convert("L"))
    assert abs(bi._detect_skew(gray)) < 0.35


def test_deskew_refuses_an_estimate_beyond_the_clamp(monkeypatch):
    """A wild estimate must be rejected, not applied — a diagram-dominated
    page is exactly where the estimator is least trustworthy."""
    monkeypatch.setattr(bi, "_detect_skew",
                        lambda gray: bi.MAX_DESKEW_DEGREES + 5.0)
    image = _text_page(0.0)
    result, applied = bi.deskew(image)
    assert applied == 0.0
    assert result is image


def test_deskew_handles_a_blank_page():
    blank = Image.new("RGB", (400, 400), "white")
    _, applied = bi.deskew(blank)
    assert applied == 0.0


def test_prepare_page_reports_provenance():
    prepared, provenance = bi.prepare_page(_text_page(2.0), skip_denoise=True)
    assert provenance["denoised"] is False
    assert provenance["width"] == prepared.width
    assert provenance["height"] == prepared.height
    assert provenance["deskew_degrees"] == pytest.approx(-2.0, abs=0.35)


def test_ingest_booklet_returns_stable_bucket_key_refs():
    result = bi.ingest_booklet(BOOKLET_PDF, storage_mode="dummy", dpi=72,
                               skip_denoise=True)
    assert result["page_count"] == 4
    assert len(result["pages"]) == 4

    # PROJECT_CONTEXT.md rule 1: stored refs are never presigned URLs.
    refs = [result["source_pdf_url"]] + [p["page_image_url"] for p in result["pages"]]
    for ref in refs:
        assert not ref.startswith("http")
        assert "?" not in ref            # no query string == no signature
        assert "/" in ref                # "bucket/key" shape

    assert [p["page_number"] for p in result["pages"]] == [1, 2, 3, 4]
    assert all(p["image"] is not None for p in result["pages"])


def test_ingest_booklet_rejects_a_missing_file():
    with pytest.raises(ValueError, match="PDF not found"):
        bi.ingest_booklet(BOOKLET_PDF.parent / "nope.pdf", storage_mode="dummy")


def test_unknown_storage_mode_is_rejected():
    with pytest.raises(ValueError, match="unknown storage_mode"):
        bi._upload("/tmp/x.png", "prefix", storage_mode="s3", asset_id="a")
