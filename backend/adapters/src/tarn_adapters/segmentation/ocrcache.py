"""The OCR of a booklet file, read once and kept in a local folder (student data: the folder is
under git-ignored ``var/``, files mode 600), so the segmentation benchmark can be re-run without
the 10-30 s a page of reading.

``<dir>/ocr.json``: the file's SHA-256 and, per page in upload order, the cleaned page's size,
its rotation and every region (kind, box, chosen text, table parent/row/col). ``<dir>/pages/
NNN.jpg``: the cleaned, oriented page the engines read (for looking at it while labelling)."""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from tarn_adapters.ocr.batch import page_ocr, read_pages
from tarn_adapters.ocr.wiring import OcrSetup
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.services.ocr.selector import Lexicon

SCHEMA = 1


@dataclass(frozen=True, slots=True)
class CachedRegion:
    kind: RegionKind
    box: Box
    text: str | None
    flagged: bool
    parent: int | None = None
    """Index (in the page's region list) of the table this cell belongs to."""
    row: int | None = None
    col: int | None = None


@dataclass(frozen=True, slots=True)
class CachedPage:
    index: int
    width: int
    height: int
    rotation_degrees: int
    regions: tuple[CachedRegion, ...]


def _write_private(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o600)
    tmp.replace(path)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_into(
    source: Path, out: Path, setup: OcrSetup, lexicon: Lexicon, *, max_pages: int = 60
) -> Iterator[CachedPage]:
    """Read ``source`` page by page into ``out`` (written once all pages are read)."""
    (out / "pages").mkdir(parents=True, exist_ok=True)
    ocr = page_ocr(setup)
    pages: list[dict[str, object]] = []
    for read in read_pages(source.read_bytes(), setup, ocr, lexicon, max_pages=max_pages):
        width, height = setup.transform.size(read.image)
        _write_private(out / "pages" / f"{read.index + 1:03d}.jpg", read.image)
        regions = []
        for region in read.text.regions:
            choice = region.choice
            text = (
                None
                if choice is None or choice.chosen is None
                else region.readings[choice.chosen].text
            )
            regions.append(
                CachedRegion(
                    kind=region.kind,
                    box=region.box,
                    text=text,
                    flagged=bool(choice is None or choice.flagged),
                    parent=region.parent,
                    row=region.row,
                    col=region.col,
                )
            )
        page = CachedPage(read.index, width, height, read.rotation_degrees, tuple(regions))
        pages.append(_page_json(page))
        yield page
    document = {"schema": SCHEMA, "sha256": sha256_of(source), "pages": pages}
    _write_private(out / "ocr.json", json.dumps(document, ensure_ascii=False).encode())


def _page_json(page: CachedPage) -> dict[str, object]:
    return {
        "index": page.index,
        "width": page.width,
        "height": page.height,
        "rotation_degrees": page.rotation_degrees,
        "regions": [
            {
                "kind": r.kind.value,
                "box": [r.box.x0, r.box.y0, r.box.x1, r.box.y1],
                "text": r.text,
                "flagged": r.flagged,
                "parent": r.parent,
                "row": r.row,
                "col": r.col,
            }
            for r in page.regions
        ],
    }


def load(out: Path, source: Path | None = None) -> list[CachedPage] | None:
    """The cached pages, or None when there is no cache (or it is of another file)."""
    path = out / "ocr.json"
    if not path.exists():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA:
        return None
    if source is not None and document.get("sha256") != sha256_of(source):
        return None
    pages = []
    for p in document["pages"]:
        regions = tuple(
            CachedRegion(
                kind=RegionKind(r["kind"]),
                box=Box(x0=r["box"][0], y0=r["box"][1], x1=r["box"][2], y1=r["box"][3]),
                text=r["text"],
                flagged=r["flagged"],
                parent=r["parent"],
                row=r["row"],
                col=r["col"],
            )
            for r in p["regions"]
        )
        pages.append(
            CachedPage(p["index"], p["width"], p["height"], p["rotation_degrees"], regions)
        )
    return pages


__all__ = ["CachedPage", "CachedRegion", "load", "read_into", "sha256_of"]
