"""Deterministic stand-ins for the page splitter and cleaner.

Test "files" are tiny byte strings: ``b"%PDF-fake:3#"`` is a PDF of 3 pages, ``b"%PDF-bad"`` an
unreadable one, and an image is ``b"\\xff\\xd8\\xff" + tag`` where the tag steers what the fake
cleaner measures: ``blur``, ``glare``, ``nopage``, ``small`` (low resolution), ``bad`` (cannot
be decoded), ``boom`` (an infrastructure error), ``guess`` (a quarter turn whose direction was
guessed)."""

from collections.abc import Sequence

from tarn_core.domain.booklet import PageMetrics
from tarn_core.domain.common import EngineRef
from tarn_core.errors import InvariantError, UnreadableFileError
from tarn_core.ports.pages import CleanedPage
from tarn_core.ports.storage import PageImage

JPEG = b"\xff\xd8\xff"


def fake_image(tag: str = "ok") -> bytes:
    return JPEG + tag.encode("ascii")


def fake_pdf(pages: int, tag: str = "") -> bytes:
    """A PDF of ``pages`` pages; ``tag`` makes its bytes (and so its hash) unique."""
    return b"%PDF-fake:" + str(pages).encode("ascii") + b"#" + tag.encode("ascii")


class FakePageSplitter:
    def split(self, data: bytes, media_type: str, max_pages: int) -> Sequence[PageImage]:
        if not data.startswith(b"%PDF-fake:"):
            raise UnreadableFileError("not a readable PDF")
        count = int(data.split(b":")[1].split(b"#")[0])
        if count > max_pages:
            raise InvariantError("too many pages")
        return [
            PageImage(index=i, data=fake_image(f"p{i}"), media_type="image/jpeg")
            for i in range(count)
        ]


class FakePageCleaner:
    ref = EngineRef(name="fake-cleaner", version="1")

    def __init__(self) -> None:
        self.calls: list[bytes] = []
        self.boom_times = 0
        """How many ``boom`` pages raise before one succeeds (retry tests)."""

    def clean(self, data: bytes) -> CleanedPage:
        self.calls.append(data)
        tag = data[len(JPEG) :].decode("ascii", "replace")
        if "bad" in tag:
            raise UnreadableFileError("not a readable image")
        if "boom" in tag and self.boom_times > 0:
            self.boom_times -= 1
            raise RuntimeError("storage unavailable")
        width, height = (300, 400) if "small" in tag else (1000, 1400)
        metrics = PageMetrics(
            sharpness=50.0 if "blur" in tag else 400.0,
            glare_share=0.2 if "glare" in tag else 0.0,
            page_found="nopage" not in tag,
            page_area_share=0.9,
            source_width=width,
            source_height=height,
            rotation_degrees=90 if "guess" in tag else 0,
            rotation_guessed="guess" in tag,
            skew_degrees=0.0,
            cropped=False,
            perspective_corrected=False,
            neighbour_removed=False,
        )
        return CleanedPage(
            image=b"CLEAN:" + data,
            media_type="image/jpeg",
            width=width,
            height=height,
            metrics=metrics,
        )
