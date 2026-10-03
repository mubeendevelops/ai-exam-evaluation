"""Run files through the page stages (split, clean, quality gate) without a database or a
queue: ``tarn pages check``. Used to try a folder of real booklets and to read the measurements
(sharpness, glare) when setting the gate's thresholds."""

import json
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path

from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
from tarn_adapters.imaging.pdf import PyMuPdfSplitter
from tarn_core.errors import UnreadableFileError
from tarn_core.ports.pages import PageCleaner, PageSplitter
from tarn_core.ports.storage import PageImage
from tarn_core.services.pipeline import QualityPolicy
from tarn_core.services.uploads import sniff_media_type


@dataclass(frozen=True, slots=True)
class PageCheck:
    booklet: int
    """Position of the file among those given (1-based); names stay out of the report."""
    page: int
    source_size: str
    size: str
    kilobytes: int
    rotation_degrees: int
    rotation_guessed: bool
    skew_degrees: float
    cropped: bool
    perspective_corrected: bool
    neighbour_removed: bool
    page_found: bool
    sharpness: float | None
    glare_share: float
    reasons: tuple[str, ...]
    seconds: float

    def line(self) -> str:
        sharp = "n/a" if self.sharpness is None else f"{self.sharpness:.0f}"
        flags = "".join(
            [
                "C" if self.cropped else "-",
                "P" if self.perspective_corrected else "-",
                "N" if self.neighbour_removed else "-",
                "?" if self.rotation_guessed else "-",
            ]
        )
        reasons = f" RETAKE:{','.join(self.reasons)}" if self.reasons else ""
        return (
            f"booklet {self.booklet:>2} page {self.page:>2}  {self.source_size:>9} -> "
            f"{self.size:>9} {self.kilobytes:>4} KB  rot {self.rotation_degrees:>3} "
            f"skew {self.skew_degrees:+5.1f}  sharp {sharp:>4}  glare {self.glare_share:.3f}  "
            f"[{flags}] {self.seconds:.1f}s{reasons}"
        )


def _pages(data: bytes, splitter: PageSplitter, max_pages: int) -> Iterator[PageImage]:
    media_type = sniff_media_type(data)
    if media_type == "application/pdf":
        yield from splitter.split(data, media_type, max_pages)
    else:
        yield PageImage(index=0, data=data, media_type=media_type)


def check_files(
    paths: Iterable[Path],
    out_dir: Path | None,
    *,
    cleaner: PageCleaner | None = None,
    splitter: PageSplitter | None = None,
    policy: QualityPolicy | None = None,
    max_pages: int = 60,
) -> Iterator[PageCheck]:
    """Yield one ``PageCheck`` per page. A PDF is one booklet; every image file is its own page
    of the booklet made of all the image files given together (upload order = page order).
    Cleaned pages are written under ``out_dir/booklet-NN/`` when given."""
    cleaner = cleaner or OpenCvPageCleaner()
    splitter = splitter or PyMuPdfSplitter()
    policy = policy or QualityPolicy()
    for number, path in enumerate(paths, start=1):
        data = path.read_bytes()
        folder = None if out_dir is None else out_dir / f"booklet-{number:02d}"
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
        try:
            for page in _pages(data, splitter, max_pages):
                started = time.perf_counter()
                cleaned = cleaner.clean(page.data)
                seconds = time.perf_counter() - started
                m = cleaned.metrics
                if folder is not None:
                    (folder / f"{page.index + 1:03d}.jpg").write_bytes(cleaned.image)
                yield PageCheck(
                    booklet=number,
                    page=page.index + 1,
                    source_size=f"{m.source_width}x{m.source_height}",
                    size=f"{cleaned.width}x{cleaned.height}",
                    kilobytes=len(cleaned.image) // 1024,
                    rotation_degrees=m.rotation_degrees,
                    rotation_guessed=m.rotation_guessed,
                    skew_degrees=m.skew_degrees,
                    cropped=m.cropped,
                    perspective_corrected=m.perspective_corrected,
                    neighbour_removed=m.neighbour_removed,
                    page_found=m.page_found,
                    sharpness=m.sharpness,
                    glare_share=m.glare_share,
                    reasons=tuple(
                        r.value for r in policy.reasons(m, cleaned.width, cleaned.height)
                    ),
                    seconds=round(seconds, 2),
                )
        except UnreadableFileError as error:
            raise UnreadableFileError(f"booklet {number}: {error}") from None


def write_report(checks: Iterable[PageCheck], path: Path) -> None:
    path.write_text(json.dumps([asdict(c) for c in checks], indent=2), encoding="utf-8")
