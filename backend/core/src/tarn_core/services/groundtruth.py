"""Ground-truth pages: the JSON-lines format and the service on top of a store.

**Format** (one file per page, ``<page id>.jsonl``, one JSON object per line of text, UTF-8):

    {"schema": 1, "page": "b-ci2-p04", "image": "b-ci2-p04.jpg", "page_size": [1650, 2200],
     "capture": "scanning_app", "source": "B-CI2", "box": [x0, y0, x1, y1], "text": "...",
     "class": "cursive", "status": "verified", "origin": "transcription",
     "prefill": "...", "region_id": null, "college_id": null}

Every record carries the page's fields, so a single record is self-contained. ``image``, ``box``,
``text`` and ``class`` are the names of the calibration manifest (``tarn ocr calibrate``): the
verified records of a set are that manifest. Errors never repeat the text (it is student data).

**Feeding from the review UI (P16).** ``record_correction`` is the one call the review screen
makes when a teacher edits a reading: it finds the ground-truth line under the corrected region
(or adds one), stores the teacher's text as verified, and creates the page (with its image) the
first time. The store for a college's corrections is the college's own; the calibrations fitted
from them hold numbers only."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

from tarn_core.domain.common import Box
from tarn_core.domain.groundtruth import (
    SCHEMA_VERSION,
    CaptureType,
    TruthLine,
    TruthOrigin,
    TruthPage,
    TruthStatus,
)
from tarn_core.domain.ocr import ContentClass
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ports.groundtruth import GroundTruthStore
from tarn_core.services.ocr.align import iou

MATCH_IOU = 0.5
"""A corrected region is the ground-truth line it overlaps by at least this much."""


# --- format -----------------------------------------------------------------------------------


def line_record(page: TruthPage, line: TruthLine) -> dict[str, object]:
    box = line.box
    return {
        "schema": SCHEMA_VERSION,
        "page": page.id,
        "image": page.image,
        "page_size": [page.width, page.height],
        "capture": page.capture.value,
        "source": page.source,
        "box": [box.x0, box.y0, box.x1, box.y1],
        "text": line.text,
        "class": line.content_class.value,
        "status": line.status.value,
        "origin": line.origin.value,
        "prefill": line.prefill,
        "region_id": None if line.region_id is None else str(line.region_id),
        "college_id": None if page.college_id is None else str(page.college_id),
    }


def page_to_jsonl(page: TruthPage) -> str:
    return "".join(
        json.dumps(line_record(page, ln), ensure_ascii=False) + "\n" for ln in page.lines
    )


def page_from_jsonl(text: str) -> TruthPage:
    """Raises ``InvariantError`` (with the line number, never the content) on a bad file."""
    records: list[tuple[int, Mapping[str, object]]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except ValueError:
            raise InvariantError(f"line {number}: not JSON") from None
        if not isinstance(item, dict):
            raise InvariantError(f"line {number}: not a JSON object")
        records.append((number, item))
    if not records:
        raise InvariantError("an empty ground-truth file")
    first = records[0][1]
    try:
        header = {
            key: first[key]
            for key in ("page", "image", "capture", "page_size", "source", "college_id")
            if key in first
        }
        lines = []
        for number, item in records:
            for key in ("page", "image", "capture", "page_size"):
                if item.get(key) != first.get(key):
                    raise InvariantError(f"line {number}: {key} differs from the first line")
            lines.append(line_from_record(item, number))
        size = header["page_size"]
        if not (isinstance(size, list) and len(size) == 2):
            raise InvariantError("page_size is [width, height]")
        college = header.get("college_id")
        return TruthPage(
            id=str(header["page"]),
            capture=CaptureType(str(header["capture"])),
            image=str(header["image"]),
            width=int(size[0]),
            height=int(size[1]),
            lines=tuple(lines),
            source=str(header.get("source") or ""),
            college_id=None if college is None else UUID(str(college)),
        )
    except (KeyError, ValueError, TypeError) as error:
        if isinstance(error, InvariantError):
            raise
        raise InvariantError(f"bad ground-truth record ({type(error).__name__})") from None


def line_from_record(item: Mapping[str, object], number: int) -> TruthLine:
    try:
        box = item["box"]
        if not (isinstance(box, list) and len(box) == 4):
            raise InvariantError(f"line {number}: box is [x0, y0, x1, y1]")
        prefill = item.get("prefill")
        region = item.get("region_id")
        return TruthLine(
            box=Box(x0=int(box[0]), y0=int(box[1]), x1=int(box[2]), y1=int(box[3])),
            text=str(item["text"]),
            content_class=ContentClass(str(item["class"])),
            status=TruthStatus(str(item.get("status", TruthStatus.PREFILLED))),
            origin=TruthOrigin(str(item.get("origin", TruthOrigin.TRANSCRIPTION))),
            prefill=None if prefill is None else str(prefill),
            region_id=None if region is None else UUID(str(region)),
        )
    except InvariantError as error:
        if str(error).startswith(f"line {number}"):
            raise
        raise InvariantError(f"line {number}: {error}") from None
    except (KeyError, ValueError, TypeError):
        raise InvariantError(f"line {number}: missing or invalid field") from None


# --- service ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class PrefillLine:
    """A line as the OCR read it: the box, the reading to start from and the predicted class."""

    box: Box
    text: str
    content_class: ContentClass


@dataclass(frozen=True, slots=True)
class Progress:
    pages: int = 0
    lines: int = 0
    prefilled: int = 0
    verified: int = 0
    ignored: int = 0
    by_capture: dict[CaptureType, int] = field(default_factory=dict)
    """Verified lines per capture type."""
    by_class: dict[ContentClass, int] = field(default_factory=dict)
    """Verified lines per content class."""


class GroundTruthService:
    def __init__(self, store: GroundTruthStore) -> None:
        self._store = store

    def pages(self) -> list[TruthPage]:
        return [self._store.get(page_id) for page_id in self._store.page_ids()]

    def add_prefilled(
        self,
        *,
        page_id: str,
        capture: CaptureType,
        image: bytes,
        image_name: str,
        width: int,
        height: int,
        lines: Sequence[PrefillLine],
        source: str = "",
        replace_prefill: bool = False,
    ) -> TruthPage:
        """Store a page with the OCR's readings as starting text. A page already stored is
        refused unless ``replace_prefill`` is set, and even then it is never replaced once a
        person has verified or ignored a line (their work is not overwritten)."""
        if page_id in self._store.page_ids():
            existing = self._store.get(page_id)
            if not replace_prefill:
                raise InvariantError(f"page {page_id} is already in the set")
            if any(ln.status is not TruthStatus.PREFILLED for ln in existing.lines):
                raise InvariantError(f"page {page_id} has verified lines: not overwritten")
        page = TruthPage(
            id=page_id,
            capture=capture,
            image=image_name,
            width=width,
            height=height,
            source=source,
            lines=tuple(
                TruthLine(
                    box=ln.box,
                    text=_one_line(ln.text),
                    content_class=ln.content_class,
                    status=TruthStatus.PREFILLED,
                    prefill=_one_line(ln.text),
                )
                for ln in lines
            ),
        )
        self._store.save(page, image)
        return page

    def save_lines(self, page_id: str, lines: Sequence[TruthLine]) -> TruthPage:
        """The transcription tool saves a page's lines as edited. Lines keep the pre-fill they
        were created with: the tool sends it back, and a line without one is a line the person
        added (no pre-fill)."""
        page = self._store.get(page_id).with_lines(tuple(lines))
        self._store.save(page)
        return page

    def record_correction(
        self,
        *,
        page_id: str,
        box: Box,
        text: str,
        content_class: ContentClass,
        region_id: UUID | None = None,
        new_page: "NewPage | None" = None,
    ) -> TruthPage:
        """A teacher corrected the reading under ``box``. The matching ground-truth line (best
        overlap, at least ``MATCH_IOU``) becomes verified with the teacher's text; without one a
        new line is added. ``new_page`` (image, capture, size) is needed the first time a page
        is seen. An empty text ignores the line instead (the teacher found no text there)."""
        status = TruthStatus.VERIFIED if text.strip() else TruthStatus.IGNORED
        stored = page_id in self._store.page_ids()
        if not stored and new_page is None:
            raise NotFoundError(f"page {page_id} is not in the set and no image was given")
        existing = list(self._store.get(page_id).lines) if stored else []
        index = _best_match(existing, box)
        updated = TruthLine(
            box=box if index is None else existing[index].box,
            text=_one_line(text),
            content_class=content_class,
            status=status,
            origin=TruthOrigin.TEACHER_CORRECTION,
            prefill=None if index is None else existing[index].prefill,
            region_id=region_id,
        )
        if index is None:
            existing.append(updated)
        else:
            existing[index] = updated
        if stored:
            page = replace(self._store.get(page_id), lines=tuple(existing))
            self._store.save(page)
            return page
        assert new_page is not None  # noqa: S101  (checked above)
        page = TruthPage(
            id=page_id,
            capture=new_page.capture,
            image=new_page.image_name,
            width=new_page.width,
            height=new_page.height,
            source=new_page.source,
            college_id=new_page.college_id,
            lines=tuple(existing),
        )
        self._store.save(page, new_page.image)
        return page

    def progress(self) -> Progress:
        pages = lines = prefilled = verified = ignored = 0
        by_capture: dict[CaptureType, int] = {}
        by_class: dict[ContentClass, int] = {}
        for page in self.pages():
            pages += 1
            for line in page.lines:
                lines += 1
                if line.status is TruthStatus.PREFILLED:
                    prefilled += 1
                elif line.status is TruthStatus.IGNORED:
                    ignored += 1
                else:
                    verified += 1
                    by_capture[page.capture] = by_capture.get(page.capture, 0) + 1
                    by_class[line.content_class] = by_class.get(line.content_class, 0) + 1
        return Progress(pages, lines, prefilled, verified, ignored, by_capture, by_class)


@dataclass(frozen=True, slots=True, kw_only=True)
class NewPage:
    image: bytes
    image_name: str
    capture: CaptureType
    width: int
    height: int
    source: str = ""
    college_id: UUID | None = None


def _best_match(lines: Sequence[TruthLine], box: Box) -> int | None:
    best, best_iou = None, 0.0
    for i, line in enumerate(lines):
        value = iou(line.box, box)
        if value > best_iou:
            best, best_iou = i, value
    return best if best is not None and best_iou >= MATCH_IOU else None


def _one_line(text: str) -> str:
    return " ".join(text.split())
