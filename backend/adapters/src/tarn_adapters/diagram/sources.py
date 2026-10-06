"""Readers for the public flowchart sets, into the manifest format of ``dataset``.

- **FC database, offline extension** (Bresler, Průša, Hlaváč; CTU Prague; ``fc-offline`` in
  ``scripts/datasets/fetch.py``): the same 672 hand-drawn flowcharts (24 writers × 28) twice,
  in ``DA`` (source ``fc_skeleton``: strokes rasterised one pixel wide) and ``DB``
  (``fc_scan``: printed and scanned), each with ``images/`` and ``annotation/`` (XML:
  ``symbol`` elements with ``bounds``, arrows with ``arrowAnnotation`` — ``headBounds``,
  ``from``, ``to`` — and ``relation`` elements: ``arrow_connection`` joins an arrow and two
  symbols). Classes: terminator, process, decision, data, connection, arrow (text is left to
  the OCR). The arrow relations give the true graph, so these images also measure edges end
  to end. Splits go **by writer** (both parts of a drawing land in the same split): the last
  five writers in order are the test split, the three before them validation.
- **Flowchart 3b** (ISC UPIIZ students, Kaggle ``davbetm/flowchart-3b``): Pascal VOC XML per
  image; classes start_end, scan, print, decision, process and ``arrow_line_{up,down,left,
  right}``. The arrow's head is not annotated: it is placed at the box end the direction names
  (``derived``). No graph.

Splits: a list file of the source when present (``train.txt`` / ``val*.txt`` / ``test.txt``
naming image stems), else the folder names (``train``, ``val``/``valid``, ``test``), else a
stable hash of the file name (70/15/15)."""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path

from tarn_adapters.diagram.dataset import (
    ArrowTruth,
    BoxList,
    LabelledObject,
    PointList,
    Sample,
    split_for,
)

FC_CLASSES = {
    "terminator": "terminal",
    "process": "process",
    "decision": "decision",
    "data": "io",
    "connection": "circle",
}
VOC_CLASSES = {
    "start_end": "terminal",
    "scan": "io",
    "print": "io",
    "decision": "decision",
    "process": "process",
}
_ARROW_DIRECTIONS = {"arrow_line_up", "arrow_line_down", "arrow_line_left", "arrow_line_right"}
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".PNG", ".JPG", ".JPEG")


def _float(el: ET.Element, name: str) -> float:
    value = el.get(name)
    if value is None:
        raise ValueError(f"<{el.tag}> has no {name}")
    return float(value)


def _bounds(el: ET.Element) -> BoxList:
    x, y = _float(el, "x"), _float(el, "y")
    return (x, y, x + _float(el, "width"), y + _float(el, "height"))


def _point(el: ET.Element) -> PointList:
    return (_float(el, "x"), _float(el, "y"))


def _image_for(annotation: Path, images: Path) -> Path | None:
    for suffix in _IMAGE_SUFFIXES:
        candidate = images / f"{annotation.stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _list_splits(root: Path) -> dict[str, str]:
    """Image stem → split, from list files anywhere under ``root``."""
    found: dict[str, str] = {}
    for f in root.rglob("*.txt"):
        name = f.stem.lower()
        split = (
            "train"
            if "train" in name
            else "val"
            if "val" in name
            else "test"
            if "test" in name
            else None
        )
        if split is None:
            continue
        for line in f.read_text(errors="replace").splitlines():
            stem = Path(line.strip()).stem
            if stem:
                found.setdefault(stem, split)
    return found


def _folder_split(path: Path) -> str | None:
    for part in reversed([p.lower() for p in path.parts]):
        if part in ("train", "training"):
            return "train"
        if part in ("val", "valid", "validation"):
            return "val"
        if part in ("test", "testing"):
            return "test"
    return None


def read_fc_annotation(
    xml_text: str,
) -> tuple[list[LabelledObject], list[ArrowTruth], list[tuple[int, int]]]:
    """Objects, arrows and edges (shape indices, tail → head) of one FC annotation."""
    root = ET.fromstring(xml_text)  # noqa: S314  (a local dataset file, not user input)
    shapes: list[LabelledObject] = []
    shape_index: dict[str, int] = {}
    heads: list[LabelledObject] = []
    arrows: list[ArrowTruth] = []
    arrow_ids: dict[str, int] = {}
    for symbol in root.iter("symbol"):
        name = (symbol.get("name") or "").lower()
        bounds = symbol.find("bounds")
        if bounds is None:
            continue
        box = _bounds(bounds)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        sid = symbol.get("id") or ""
        if name in FC_CLASSES:
            shape_index[sid] = len(shapes)
            shapes.append(LabelledObject(cls=FC_CLASSES[name], box=box))
        elif name == "arrow":
            note = symbol.find("arrowAnnotation")
            if note is None:
                continue
            start, end, head = note.find("from"), note.find("to"), note.find("headBounds")
            if start is None or end is None:
                continue
            has_head = head is not None
            if head is not None:
                hb = _bounds(head)
                if hb[2] > hb[0] and hb[3] > hb[1]:
                    heads.append(LabelledObject(cls="arrow_head", box=hb))
            arrow_ids[sid] = len(arrows)
            arrows.append(
                ArrowTruth(box=box, tail=_point(start), head=_point(end), has_head=has_head)
            )
    edges: list[tuple[int, int]] = []
    groups = [*root.iter("relation"), *root.iter("symbolGroup")]
    for group in groups:
        refs = [v.get("symbolDataRef") or "" for v in group.iter("symbolView")]
        arrow_refs = [r for r in refs if r in arrow_ids]
        shape_refs = [r for r in refs if r in shape_index]
        if len(arrow_refs) != 1 or len(shape_refs) != 2:
            continue
        arrow = arrows[arrow_ids[arrow_refs[0]]]
        a, b = (shape_index[r] for r in shape_refs)
        # the tail's shape is the one nearer the arrow's start point
        if _distance(arrow.tail, shapes[b].box) < _distance(arrow.tail, shapes[a].box):
            a, b = b, a
        edges.append((a, b))
    arrow_objects = [LabelledObject(cls="arrow", box=a.box) for a in arrows]
    return [*shapes, *arrow_objects, *heads], arrows, edges


def _distance(p: PointList, box: BoxList) -> float:
    dx = max(box[0] - p[0], 0.0, p[0] - box[2])
    dy = max(box[1] - p[1], 0.0, p[1] - box[3])
    return float((dx * dx + dy * dy) ** 0.5)


FC_TEST_WRITERS = 5
FC_VAL_WRITERS = 3


def _writer(stem: str) -> str:
    return stem.split("_", 1)[0]


def read_fc(root: Path, image_size: "ImageSize") -> Iterator[Sample]:
    """Every annotated image of ``DA`` (source ``fc_skeleton``) and ``DB`` (``fc_scan``) under
    ``root`` (the unpacked ``FC_database_offline_1.0``, at any depth)."""
    listed = _list_splits(root)
    writers = sorted({_writer(x.stem) for x in root.rglob("annotation/*.xml")})
    test = set(writers[-FC_TEST_WRITERS:]) if len(writers) > FC_TEST_WRITERS else set()
    val = (
        set(writers[-FC_TEST_WRITERS - FC_VAL_WRITERS : -FC_TEST_WRITERS])
        if len(writers) > FC_TEST_WRITERS + FC_VAL_WRITERS
        else set()
    )
    for part, source in (("DA", "fc_skeleton"), ("DB", "fc_scan")):
        for folder in sorted(p for p in root.rglob(part) if p.is_dir()):
            annotations, images = folder / "annotation", folder / "images"
            if not annotations.is_dir() or not images.is_dir():
                continue
            for xml in sorted(annotations.glob("*.xml")):
                image = _image_for(xml, images)
                if image is None:
                    continue
                objects, arrows, edges = read_fc_annotation(xml.read_text(errors="replace"))
                width, height = image_size(image)
                w = _writer(xml.stem)
                split = listed.get(xml.stem) or (
                    "test" if w in test else "val" if w in val else "train"
                )
                if not test:
                    split = listed.get(xml.stem) or _folder_split(xml) or split_for(w)
                yield Sample(
                    image=str(image.resolve()),
                    width=width,
                    height=height,
                    source=source,
                    split=split,
                    objects=tuple(objects),
                    arrows=tuple(arrows),
                    edges=tuple(edges),
                )


def derived_head(direction: str, box: BoxList) -> tuple[PointList, PointList, BoxList]:
    """Tail, head and head box of a straight arrow from its direction class."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if direction.endswith("down"):
        side = min(h, max(w, 0.2 * h))
        return (cx, y0), (cx, y1), (x0, y1 - side, x1, y1)
    if direction.endswith("up"):
        side = min(h, max(w, 0.2 * h))
        return (cx, y1), (cx, y0), (x0, y0, x1, y0 + side)
    if direction.endswith("right"):
        side = min(w, max(h, 0.2 * w))
        return (x0, cy), (x1, cy), (x1 - side, y0, x1, y1)
    side = min(w, max(h, 0.2 * w))
    return (x1, cy), (x0, cy), (x0, y0, x0 + side, y1)


def read_voc_annotation(
    xml_text: str,
) -> tuple[str, int, int, list[LabelledObject], list[ArrowTruth]]:
    root = ET.fromstring(xml_text)  # noqa: S314  (a local dataset file, not user input)
    filename = (root.findtext("filename") or "").strip()
    size = root.find("size")
    width = int(float(size.findtext("width") or 0)) if size is not None else 0
    height = int(float(size.findtext("height") or 0)) if size is not None else 0
    objects: list[LabelledObject] = []
    heads: list[LabelledObject] = []
    arrows: list[ArrowTruth] = []
    for obj in root.iter("object"):
        name = (obj.findtext("name") or "").strip().lower()
        bb = obj.find("bndbox")
        if bb is None:
            continue
        box = (
            float(bb.findtext("xmin") or 0),
            float(bb.findtext("ymin") or 0),
            float(bb.findtext("xmax") or 0),
            float(bb.findtext("ymax") or 0),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        if name in VOC_CLASSES:
            objects.append(LabelledObject(cls=VOC_CLASSES[name], box=box))
        elif name in _ARROW_DIRECTIONS:
            tail, head, head_box = derived_head(name, box)
            objects.append(LabelledObject(cls="arrow", box=box))
            heads.append(LabelledObject(cls="arrow_head", box=head_box))
            arrows.append(ArrowTruth(box=box, tail=tail, head=head, derived=True))
    return filename, width, height, [*objects, *heads], arrows


def read_voc(root: Path, image_size: "ImageSize") -> Iterator[Sample]:
    """Flowchart 3b (Pascal VOC XML next to or near its images), source ``fc3b``. Its
    validation part is our test split; a stable tenth of its training part is validation."""
    listed = _list_splits(root)
    for xml in sorted(root.rglob("*.xml")):
        try:
            filename, width, height, objects, arrows = read_voc_annotation(
                xml.read_text(errors="replace")
            )
        except (ET.ParseError, ValueError):
            continue
        if not objects:
            continue
        image = _find_image(xml, filename)
        if image is None:
            continue
        if width <= 0 or height <= 0:
            width, height = image_size(image)
        split = listed.get(xml.stem) or _folder_split(xml) or split_for(xml.stem)
        if split == "val":
            split = "test"
        if split == "train" and split_for(xml.stem, val=0.1, test=0.0) == "val":
            split = "val"
        yield Sample(
            image=str(image.resolve()),
            width=width,
            height=height,
            source="fc3b",
            split=split,
            objects=tuple(objects),
            arrows=tuple(arrows),
        )


def _find_image(xml: Path, filename: str) -> Path | None:
    names = [filename] if filename else []
    names += [f"{xml.stem}{s}" for s in _IMAGE_SUFFIXES]
    for folder in (xml.parent, xml.parent.parent, xml.parent.parent / "images"):
        for name in names:
            candidate = folder / name
            if candidate.is_file():
                return candidate
    return None


class ImageSize:
    """Width and height of an image file, cached per path."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[int, int]] = {}

    def __call__(self, path: Path) -> tuple[int, int]:
        if path not in self._cache:
            import cv2

            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise ValueError(f"cannot read {path.name}")
            self._cache[path] = (int(image.shape[1]), int(image.shape[0]))
        return self._cache[path]
