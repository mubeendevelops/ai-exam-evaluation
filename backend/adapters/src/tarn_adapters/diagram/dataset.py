"""Training and test material for the shape-and-arrow detector, in one format whatever the
source: a manifest (JSON lines), one record per image.

Record: ``image`` (path relative to the manifest), ``width``, ``height``, ``source``
(``fc_skeleton``, ``fc_scan``, ``fc3b``, ``synthetic``), ``split`` (``train``, ``val``, ``test``),
``objects`` (``class`` and ``box`` [x0, y0, x1, y1]), ``arrows`` (``box``, ``tail``, ``head``,
``has_head``, ``derived``: the head point was inferred, not annotated) and ``edges``
(``[i, j]`` indices into the shape objects, tail → head; only where the source gives the true
graph). Arrow heads are objects of class ``arrow_head``. Data lives under ``var/`` and is never
committed."""

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tarn_core.domain.diagram import NodeShape

CLASSES: tuple[str, ...] = (
    "terminal",
    "process",
    "decision",
    "io",
    "circle",
    "arrow",
    "arrow_head",
)
SHAPE_CLASSES = CLASSES[:5]
CLASS_TO_SHAPE = {name: NodeShape(name) for name in SHAPE_CLASSES}
SPLITS = ("train", "val", "test")

type BoxList = tuple[float, float, float, float]
type PointList = tuple[float, float]


@dataclass(frozen=True, slots=True)
class LabelledObject:
    cls: str
    box: BoxList

    def __post_init__(self) -> None:
        if self.cls not in CLASSES:
            raise ValueError(f"unknown class {self.cls!r}")
        x0, y0, x1, y1 = self.box
        if not (x1 > x0 and y1 > y0):
            raise ValueError("a box needs a positive width and height")


@dataclass(frozen=True, slots=True)
class ArrowTruth:
    box: BoxList
    tail: PointList
    head: PointList
    has_head: bool = True
    derived: bool = False


@dataclass(frozen=True, slots=True)
class Sample:
    image: str
    width: int
    height: int
    source: str
    split: str
    objects: tuple[LabelledObject, ...]
    arrows: tuple[ArrowTruth, ...] = ()
    edges: tuple[tuple[int, int], ...] | None = None
    """Indices into ``shapes``; None when the source has no true graph."""
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def shapes(self) -> tuple[LabelledObject, ...]:
        return tuple(o for o in self.objects if o.cls in SHAPE_CLASSES)


def split_for(key: str, *, val: float = 0.15, test: float = 0.15) -> str:
    """A stable split from a name (the same file always lands in the same split)."""
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    if h < test:
        return "test"
    if h < test + val:
        return "val"
    return "train"


def _round(values: Iterable[float]) -> list[float]:
    return [round(float(v), 1) for v in values]


def to_json(s: Sample) -> dict[str, object]:
    return {
        "image": s.image,
        "width": s.width,
        "height": s.height,
        "source": s.source,
        "split": s.split,
        "objects": [{"class": o.cls, "box": _round(o.box)} for o in s.objects],
        "arrows": [
            {
                "box": _round(a.box),
                "tail": _round(a.tail),
                "head": _round(a.head),
                "has_head": a.has_head,
                "derived": a.derived,
            }
            for a in s.arrows
        ],
        "edges": None if s.edges is None else [list(e) for e in s.edges],
        **({"extra": s.extra} if s.extra else {}),
    }


def from_json(o: dict[str, object]) -> Sample:
    def box(v: object) -> BoxList:
        x0, y0, x1, y1 = (float(x) for x in v)  # type: ignore[attr-defined]
        return (x0, y0, x1, y1)

    def point(v: object) -> PointList:
        x, y = (float(p) for p in v)  # type: ignore[attr-defined]
        return (x, y)

    edges = o.get("edges")
    return Sample(
        image=str(o["image"]),
        width=int(o["width"]),  # type: ignore[call-overload]
        height=int(o["height"]),  # type: ignore[call-overload]
        source=str(o["source"]),
        split=str(o["split"]),
        objects=tuple(
            LabelledObject(cls=str(x["class"]), box=box(x["box"]))
            for x in o["objects"]  # type: ignore[attr-defined]
        ),
        arrows=tuple(
            ArrowTruth(
                box=box(a["box"]),
                tail=point(a["tail"]),
                head=point(a["head"]),
                has_head=bool(a["has_head"]),
                derived=bool(a.get("derived", False)),
            )
            for a in o.get("arrows", [])  # type: ignore[attr-defined]
        ),
        edges=None if edges is None else tuple((int(i), int(j)) for i, j in edges),  # type: ignore[attr-defined]
        extra=dict(o.get("extra", {})),  # type: ignore[call-overload]
    )


def write_manifest(path: Path, samples: Iterable[Sample]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as out:
        for s in samples:
            out.write(json.dumps(to_json(s)) + "\n")
            count += 1
    tmp.replace(path)
    return count


def read_manifest(path: Path) -> Iterator[Sample]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield from_json(json.loads(line))


def image_path(manifest: Path, sample: Sample) -> Path:
    p = Path(sample.image)
    return p if p.is_absolute() else manifest.parent / p
