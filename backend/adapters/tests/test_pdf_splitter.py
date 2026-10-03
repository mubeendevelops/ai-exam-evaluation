# mypy: disable-error-code="no-untyped-call, attr-defined"
# (PyMuPDF ships no type information for its document API.)
"""``PyMuPdfSplitter`` on PDFs generated here: embedded scans at their own resolution, the
placement matrix honoured, typed pages rendered, and unreadable files refused."""

import cv2
import numpy as np
import pymupdf
import pytest

from tarn_adapters.imaging import testing as synth
from tarn_adapters.imaging.pdf import PyMuPdfSplitter, _turns_from_matrix
from tarn_core.errors import InvariantError, UnreadableFileError

PDF = "application/pdf"
SPLITTER = PyMuPdfSplitter()


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def marked_image(width: int = 600, height: int = 800) -> np.ndarray:
    """White with a red block in the top-left corner and a blue block in the top-right: the
    corners tell which way a page was turned."""
    image = np.full((height, width, 3), 255, np.uint8)
    image[:100, :100] = (0, 0, 255)  # red (BGR)
    image[:100, -100:] = (255, 0, 0)  # blue
    return image


def pdf_with_images(placements: list[tuple[np.ndarray, int]], *, page_size=(595, 842)) -> bytes:  # type: ignore[no-untyped-def]
    document = pymupdf.open()
    for image, rotate in placements:
        page = document.new_page(width=page_size[0], height=page_size[1])
        page.insert_image(page.rect, stream=synth.jpeg(image), rotate=rotate)
    data: bytes = document.tobytes()
    return data


def corner_colours(image: np.ndarray) -> tuple[str, str]:
    def name(patch: np.ndarray) -> str:
        b, r = float(patch[..., 0].mean()), float(patch[..., 2].mean())
        return "red" if r > 200 and b < 80 else "blue" if b > 200 and r < 80 else "white"

    return name(image[10:60, 10:60]), name(image[10:60, -60:-10])


def test_an_embedded_scan_is_taken_at_its_own_resolution() -> None:
    scan = synth.ruled_page(1500, 2000)
    (page,) = SPLITTER.split(pdf_with_images([(scan, 0)]), PDF, 10)
    assert page.index == 0 and page.media_type == "image/jpeg"
    assert decode(page.data).shape[:2] == (2000, 1500)  # not the 595x842 page size


def test_pages_come_in_order_and_one_per_pdf_page() -> None:
    images = [synth.ruled_page(600, 800, seed=s, lines=8) for s in (1, 2, 3)]
    pages = SPLITTER.split(pdf_with_images([(img, 0) for img in images]), PDF, 10)
    assert [p.index for p in pages] == [0, 1, 2]
    for page, original in zip(pages, images, strict=True):
        got = cv2.cvtColor(decode(page.data), cv2.COLOR_BGR2GRAY).astype(float)
        want = cv2.cvtColor(original, cv2.COLOR_BGR2GRAY).astype(float)
        assert abs(got - want).mean() < 6  # same picture (JPEG twice), same order


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_the_placement_matrix_decides_which_way_up_the_page_is(rotate: int) -> None:
    """PyMuPDF's ``rotate`` turns the picture counter-clockwise on the page; the page must come
    out as a viewer shows it, whatever the stored picture looks like."""
    source = marked_image()
    (page,) = SPLITTER.split(pdf_with_images([(source, rotate)]), PDF, 5)
    image = decode(page.data)
    expected = np.rot90(source, k=rotate // 90)
    assert image.shape == expected.shape
    difference = cv2.absdiff(
        cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.cvtColor(expected, cv2.COLOR_BGR2GRAY)
    )
    assert float(difference.mean()) < 3
    assert corner_colours(image) == corner_colours(expected)


def test_a_landscape_photo_turned_by_the_matrix_comes_out_portrait() -> None:
    """The iPhone case: a 800x600 picture stored sideways on a portrait page."""
    landscape = np.rot90(marked_image(600, 800), k=1).copy()  # 800 wide, 600 high
    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_image(page.rect, stream=synth.jpeg(landscape), rotate=270)
    (out,) = SPLITTER.split(document.tobytes(), PDF, 5)
    image = decode(out.data)
    assert image.shape[0] > image.shape[1]


@pytest.mark.parametrize(
    ("matrix", "turns", "mirrored"),
    [
        ((1, 0, 0, 1), 0, False),
        ((0, 1, -1, 0), 1, False),  # the iPhone export: right → down, down → left
        ((-1, 0, 0, -1), 2, False),
        ((0, -1, 1, 0), 3, False),
        ((-1, 0, 0, 1), 0, True),  # mirrored picture
        ((529.15, 0, -0.0, 792.0), 0, False),  # real placements carry the page size
        ((0, 3264.0, -2448.0, -0.0), 1, False),
        ((0, 0, 0, 0), 0, False),  # degenerate: leave as stored
    ],
)
def test_turns_from_a_placement_matrix(
    matrix: tuple[float, ...], turns: int, mirrored: bool
) -> None:
    assert _turns_from_matrix(matrix) == (turns, mirrored)


def test_a_typed_page_without_a_picture_is_rendered() -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "Typed answer sheet", fontsize=24)
    (out,) = SPLITTER.split(document.tobytes(), PDF, 5)
    image = decode(out.data)
    assert max(image.shape[:2]) in range(2100, 2300)
    assert (image < 128).any()  # the text was drawn


def test_a_small_logo_on_a_typed_page_does_not_stand_in_for_the_page() -> None:
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(20, 20, 80, 60), stream=synth.png(marked_image(120, 80)))
    page.insert_text((72, 300), "Typed text under a small logo", fontsize=20)
    (out,) = SPLITTER.split(document.tobytes(), PDF, 5)
    assert max(decode(out.data).shape[:2]) >= 2000  # the page, not the 120 px logo


def test_a_picture_with_a_transparency_mask_is_rendered() -> None:
    rgba = np.dstack([marked_image(300, 400), np.full((400, 300), 200, np.uint8)])
    document = pymupdf.open()
    page = document.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=cv2.imencode(".png", rgba)[1].tobytes())
    (out,) = SPLITTER.split(document.tobytes(), PDF, 5)
    assert decode(out.data).size > 0


def test_too_many_pages_is_refused() -> None:
    document = pymupdf.open()
    for _ in range(4):
        document.new_page()
    with pytest.raises(InvariantError):
        SPLITTER.split(document.tobytes(), PDF, 3)
    assert len(SPLITTER.split(document.tobytes(), PDF, 4)) == 4


def test_unreadable_pdfs_are_refused() -> None:
    document = pymupdf.open()
    document.new_page()
    good = document.tobytes()
    encrypted = document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    for junk in (b"%PDF-1.7\nnot really", b"", good[: len(good) // 3], b"hello"):
        with pytest.raises(UnreadableFileError):
            SPLITTER.split(junk, PDF, 5)
    with pytest.raises(UnreadableFileError):
        SPLITTER.split(encrypted, PDF, 5)
    with pytest.raises(UnreadableFileError):
        SPLITTER.split(good, "image/png", 5)  # only PDFs are split
