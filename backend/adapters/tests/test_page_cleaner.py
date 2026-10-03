"""The OpenCV page cleaner on generated pages (never real student pages): crop and flatten a
photo from a table, leave a scan whole, cut a neighbouring page of a spread, turn a sideways
page, level the lines, measure blur and glare, and compress."""

from itertools import pairwise

import cv2
import numpy as np
import pytest

from tarn_adapters.imaging import cleaner as cleaner_module
from tarn_adapters.imaging import testing as synth
from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
from tarn_core.domain.booklet import RetakeReason
from tarn_core.errors import UnreadableFileError
from tarn_core.services.pipeline import QualityPolicy

PAGE = synth.ruled_page()
CLEANER = OpenCvPageCleaner()


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None
    return image


def corner_brightness(image: np.ndarray) -> float:
    """Mean brightness of the four corner patches: the table, if any was left."""
    n = 20
    patches = [image[:n, :n], image[:n, -n:], image[-n:, :n], image[-n:, -n:]]
    return float(np.mean([p.mean() for p in patches]))


def test_a_page_on_a_dark_table_is_cropped_and_the_table_is_gone() -> None:
    photo = synth.photograph(PAGE, angle=4, tilt=0.02)
    result = CLEANER.clean(synth.jpeg(photo))
    m = result.metrics
    assert m.cropped and m.page_found and not m.neighbour_removed
    assert 0.5 < m.page_area_share < 0.95
    assert (m.source_width, m.source_height) == (1500, 2000)
    assert result.width < 1500 and result.height < 2000
    # A4 portrait, near enough, once the table is gone.
    assert 0.65 < result.width / result.height < 0.8
    assert corner_brightness(decode(result.image)) > 200  # white paper, not dark table


def test_a_shadow_across_the_page_is_evened_out() -> None:
    photo = synth.photograph(PAGE, shadow=True)
    cleaned = decode(CLEANER.clean(synth.jpeg(photo)).image)
    gray = cv2.cvtColor(cleaned, cv2.COLOR_BGR2GRAY).astype(float)
    left, right = gray[:, : gray.shape[1] // 5], gray[:, -gray.shape[1] // 5 :]
    assert abs(np.median(left) - np.median(right)) < 12
    shadowed = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY).astype(float)
    assert abs(np.median(shadowed[:, 100:300]) - np.median(shadowed[:, -300:-100])) > 40


def test_a_perspective_photo_is_flattened_into_a_rectangle() -> None:
    photo = synth.photograph(PAGE, tilt=0.05, margin=0.1)
    result = CLEANER.clean(synth.jpeg(photo))
    assert result.metrics.cropped
    assert 0.6 < result.width / result.height < 0.85


def test_a_page_that_fills_the_frame_is_left_whole() -> None:
    result = CLEANER.clean(synth.jpeg(synth.scan(PAGE)))
    m = result.metrics
    assert not m.cropped and m.page_found and m.page_area_share == 1.0
    assert (result.width, result.height) == (1240, 1754)


def test_a_bleached_document_scan_is_never_cropped() -> None:
    white = np.clip(PAGE.astype(np.int32) + 40, 0, 255).astype(np.uint8)  # paper 255
    border = cv2.copyMakeBorder(white, 60, 60, 60, 60, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    result = CLEANER.clean(synth.jpeg(border))
    assert not result.metrics.cropped and result.metrics.glare_share == 0.0


def test_the_neighbouring_page_of_a_spread_is_cut_away() -> None:
    shot = synth.spread(PAGE, strip=0.15)
    result = CLEANER.clean(synth.jpeg(shot))
    assert result.metrics.neighbour_removed and result.metrics.cropped
    assert result.width < 0.92 * shot.shape[1]
    assert result.width > 0.7 * shot.shape[1]  # the main page is kept whole


def test_a_single_page_with_a_red_margin_line_is_not_mistaken_for_a_spread() -> None:
    shot = synth.scan(PAGE, size=(1500, 2000))  # one page filling the shot, margin and all
    assert not CLEANER.clean(synth.jpeg(shot)).metrics.neighbour_removed


def test_a_sideways_page_is_turned_and_reported_as_a_guess() -> None:
    upright = synth.scan(PAGE)
    sideways = cv2.rotate(upright, cv2.ROTATE_90_CLOCKWISE)
    result = CLEANER.clean(synth.jpeg(sideways))
    m = result.metrics
    assert m.rotation_degrees == 270 and m.rotation_guessed
    assert result.height > result.width  # portrait again
    # The default direction is counter-clockwise, which undoes this clockwise turn exactly.
    back = decode(result.image)
    reference = cv2.resize(upright, (back.shape[1], back.shape[0]))
    difference = cv2.absdiff(
        cv2.cvtColor(back, cv2.COLOR_BGR2GRAY), cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY)
    )
    assert float(difference.mean()) < 25


def test_an_upright_page_is_not_turned_and_is_not_a_guess() -> None:
    for photo in (
        synth.photograph(PAGE),
        synth.scan(PAGE),
        synth.photograph(synth.ruled_page(ruled=False, seed=4)),
    ):
        m = CLEANER.clean(synth.jpeg(photo)).metrics
        assert m.rotation_degrees == 0 and not m.rotation_guessed


def test_tilted_text_lines_are_levelled() -> None:
    h, w = PAGE.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), 4.0, 1.0)
    tilted = cv2.warpAffine(PAGE, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
    result = CLEANER.clean(synth.jpeg(tilted))
    assert 3.0 <= abs(result.metrics.skew_degrees) <= 5.0
    straight = CLEANER.clean(synth.jpeg(PAGE)).metrics
    assert abs(straight.skew_degrees) < 1.0


def test_sharpness_falls_with_blur_and_the_gate_asks_for_a_retake() -> None:
    policy = QualityPolicy()
    sharpness = []
    for sigma in (0, 1.5, 3, 5):
        photo = synth.photograph(PAGE, margin=0.05)
        shot = photo if sigma == 0 else cv2.GaussianBlur(photo, (0, 0), sigma)
        result = CLEANER.clean(synth.jpeg(shot))
        sharpness.append(result.metrics.sharpness)
        reasons = policy.reasons(result.metrics, result.width, result.height)
        assert (RetakeReason.BLURRY in reasons) == (sigma >= 3)
    values = [v for v in sharpness if v is not None]
    assert len(values) == len(sharpness)
    assert all(a > b for a, b in pairwise(values))


def test_a_blown_out_reflection_is_measured_as_glare() -> None:
    photo = synth.photograph(PAGE, margin=0.05)
    spot = photo.copy()
    cv2.ellipse(spot, (750, 700), (320, 260), 0, 0, 360, (255, 255, 255), -1, cv2.LINE_AA)
    clean_glare = CLEANER.clean(synth.jpeg(photo)).metrics.glare_share
    glare = CLEANER.clean(synth.jpeg(spot)).metrics.glare_share
    assert clean_glare < 0.02 and glare > 0.06
    reasons = QualityPolicy().reasons(CLEANER.clean(synth.jpeg(spot)).metrics, 1200, 1700)
    assert RetakeReason.GLARE in reasons


def test_a_nearly_blank_page_has_no_sharpness_to_judge_and_is_not_called_blurry() -> None:
    blank = np.full((1754, 1240, 3), 238, np.uint8)
    cv2.putText(blank, "x", (600, 900), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (130, 50, 20), 2)
    result = CLEANER.clean(synth.jpeg(synth.photograph(blank)))
    assert result.metrics.sharpness is None
    assert RetakeReason.BLURRY not in QualityPolicy().reasons(
        result.metrics, result.width, result.height
    )


def test_the_cleaned_page_respects_the_configured_size() -> None:
    small = OpenCvPageCleaner(max_edge_px=1000, max_bytes=120_000)
    result = small.clean(synth.jpeg(synth.photograph(PAGE)))
    assert max(result.width, result.height) <= 1000
    assert len(result.image) <= 120_000
    assert result.media_type == "image/jpeg"
    assert decode(result.image).shape[:2] == (result.height, result.width)


def test_png_input_works_too() -> None:
    result = CLEANER.clean(synth.png(synth.photograph(PAGE)))
    assert result.metrics.cropped


def test_exif_orientation_of_a_phone_jpeg_is_honoured() -> None:
    image = np.zeros((100, 200, 3), np.uint8)
    data = synth.jpeg(image)
    # APP1/Exif with one IFD0 entry: Orientation (0x0112) = 6 ("rotate 90 degrees clockwise").
    tiff = (
        b"II*\x00\x08\x00\x00\x00"
        + b"\x01\x00"
        + b"\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )
    exif = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    with_exif = data[:2] + app1 + data[2:]
    assert cleaner_module.decode(with_exif).shape[:2] == (200, 100)
    assert cleaner_module.decode(data).shape[:2] == (100, 200)


def test_a_file_that_is_not_an_image_is_refused() -> None:
    for junk in (
        b"",
        b"not an image",
        b"\xff\xd8\xff garbage after a jpeg start",
        synth.jpeg(PAGE)[:200],
    ):
        with pytest.raises(UnreadableFileError):
            CLEANER.clean(junk)


def test_a_huge_image_is_refused_before_it_is_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cleaner_module, "MAX_PIXELS", 10_000)
    with pytest.raises(UnreadableFileError):
        CLEANER.clean(synth.jpeg(PAGE))


def test_the_cleaner_names_its_version() -> None:
    ref = CLEANER.ref
    assert ref.name == "opencv-page-cleaner" and cv2.__version__ in ref.version
