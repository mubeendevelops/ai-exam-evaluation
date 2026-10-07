# mypy: disable-error-code="no-untyped-call"
# (PyMuPDF ships no type information for its document API.)
"""P21 security review, adapter side: pepper rotation on real argon2id, image bombs inside a PDF
and a tar stream, the access log without query strings, the earlier peppers from secrets."""

import io
import logging
import tarfile
from uuid import UUID

import cv2
import numpy as np
import pymupdf
import pytest

from tarn_adapters.auth.hashing import Argon2Hasher
from tarn_adapters.auth.secrets import PEPPER_PREVIOUS, SettingsSecrets, previous_peppers
from tarn_adapters.config import Settings
from tarn_adapters.imaging.cleaner import decode
from tarn_adapters.imaging.pdf import PyMuPdfSplitter
from tarn_adapters.logging_setup import DropQueryString, configure_logging
from tarn_adapters.sources import StreamPageSource
from tarn_core.domain.identity import HashParams
from tarn_core.errors import UnreadableFileError, UploadTooLargeError
from tarn_core.ids import BookletId, CollegeId
from tarn_core.testing import fake_image

COLLEGE, BOOKLET = CollegeId(UUID(int=1)), BookletId(UUID(int=2))
FAST = HashParams(time_cost=1, memory_cost=1024, parallelism=1)
OLD, NEW = b"o" * 32, b"n" * 32


# --- pepper rotation on argon2id ----------------------------------------------------------------


def test_a_rotation_accepts_the_old_pepper_and_says_it_is_old() -> None:
    encoded = Argon2Hasher(OLD).hash("correct horse battery staple", FAST)
    rotating = Argon2Hasher(NEW, previous=(OLD,))
    assert rotating.verify(encoded, "correct horse battery staple")
    assert not rotating.verify(encoded, "wrong")
    assert not rotating.pepper_is_current(encoded, "correct horse battery staple")
    rehashed = rotating.hash("correct horse battery staple", FAST)
    assert rotating.pepper_is_current(rehashed, "correct horse battery staple")
    assert rehashed.startswith("$argon2id$v=19$")  # the schema's format is kept
    finished = Argon2Hasher(NEW)
    assert finished.verify(rehashed, "correct horse battery staple")
    assert not finished.verify(encoded, "correct horse battery staple")
    assert finished.pepper_is_current(encoded, "anything")  # no rotation: nothing to move


def test_earlier_peppers_come_from_settings_or_are_absent() -> None:
    none = Settings(_env_file=None)
    assert previous_peppers(SettingsSecrets(none)) == ()
    two = Settings(_env_file=None, password_pepper_previous="a" * 20 + ", " + "b" * 20)
    assert previous_peppers(SettingsSecrets(two)) == (b"a" * 20, b"b" * 20)

    class Missing:
        def get(self, name: str) -> bytes:
            assert name == PEPPER_PREVIOUS
            raise type("NotFound", (Exception,), {})()

    assert previous_peppers(Missing()) == ()

    class Broken:
        def get(self, name: str) -> bytes:
            raise PermissionError("denied")

    with pytest.raises(PermissionError):
        previous_peppers(Broken())


def test_a_short_earlier_pepper_is_refused() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        Argon2Hasher(NEW, previous=(b"short",))


# --- decompression bombs ---------------------------------------------------------------------


def _pdf_with_declared_image(width: int, height: int) -> bytes:
    """A one-page PDF whose page-filling image says it is ``width`` × ``height``."""
    ok, png = cv2.imencode(".png", np.full((40, 30, 3), 255, np.uint8))
    assert ok
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=400)
    page.insert_image(page.rect, stream=png.tobytes())
    xref = page.get_images()[0][0]
    doc.xref_set_key(xref, "Width", str(width))
    doc.xref_set_key(xref, "Height", str(height))
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_a_pdf_image_declaring_billions_of_pixels_is_refused_before_it_is_inflated() -> None:
    # 144 megapixels: above our limit, below MuPDF's own ("Overly large image").
    with pytest.raises(UnreadableFileError, match="too large"):
        PyMuPdfSplitter().split(_pdf_with_declared_image(12_000, 12_000), "application/pdf", 5)
    with pytest.raises(UnreadableFileError):
        PyMuPdfSplitter().split(_pdf_with_declared_image(70_000, 70_000), "application/pdf", 5)


def test_an_ordinary_pdf_page_still_splits() -> None:
    pages = PyMuPdfSplitter().split(_pdf_with_declared_image(30, 40), "application/pdf", 5)
    assert len(pages) == 1


def test_the_cleaner_refuses_a_png_bomb_without_decoding_it() -> None:
    import struct
    import zlib

    ihdr = struct.pack(">IIBBBBB", 50_000, 50_000, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    bomb = (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk))
    )
    with pytest.raises(UnreadableFileError, match="too large"):
        decode(bomb)


def _tar(members: list[tuple[str, bytes]], *, sparse_size: int = 0) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        if sparse_size:
            sparse = tarfile.TarInfo("page9.jpg")
            sparse.type = tarfile.GNUTYPE_SPARSE
            archive.addfile(sparse)
    raw = bytearray(buffer.getvalue())
    if sparse_size:
        # The sparse member's header: its "real size" field says how big it expands.
        offset = raw.rfind(b"page9.jpg")
        raw[offset + 483 : offset + 495] = b"%011o\0" % sparse_size
        header = raw[offset : offset + 512]
        header[148:156] = b" " * 8
        raw[offset + 148 : offset + 156] = b"%06o\0 " % sum(header)
    return bytes(raw)


def test_a_sparse_tar_member_is_skipped_not_expanded() -> None:
    data = _tar([("page1.jpg", fake_image("1"))], sparse_size=8 * 1024**3)  # "8 GiB"
    pages = StreamPageSource(io.BytesIO(data)).pages(COLLEGE, BOOKLET)
    assert len(pages) == 1


def test_tar_members_larger_than_the_cap_are_refused() -> None:
    data = _tar([("page1.jpg", fake_image("1") + b"x" * 3000), ("page2.jpg", fake_image("2"))])
    source = StreamPageSource(io.BytesIO(data), max_bytes=len(data))
    source_small = StreamPageSource(io.BytesIO(data), max_bytes=2048)
    assert len(source.pages(COLLEGE, BOOKLET)) == 2
    with pytest.raises(UploadTooLargeError):
        source_small.pages(COLLEGE, BOOKLET)


# --- logs --------------------------------------------------------------------------------------


def test_the_access_log_drops_query_strings(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("INFO")
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, DropQueryString) for f in access.filters) == 1
    configure_logging("INFO")  # idempotent
    assert sum(isinstance(f, DropQueryString) for f in access.filters) == 1
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v1/students?q=Synthetic%20Name", "1.1", 200),
        None,
    )
    assert DropQueryString().filter(record)
    assert record.getMessage() == '127.0.0.1:5000 - "GET /api/v1/students HTTP/1.1" 200'
