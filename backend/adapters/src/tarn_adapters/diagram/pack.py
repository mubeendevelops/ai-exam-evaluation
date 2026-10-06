"""A self-contained training bundle for a cloud GPU (``tarn diagram data pack``): every
prepared image already in its training form (FC skeleton strokes thickened, FC pages cropped
to the drawing with a fifth of margin, the longer side at most ``max_side`` pixels), one
manifest with relative paths, and the code training needs (``tarn_core`` and
``tarn_adapters``, pure Python). Only public and synthetic material goes in: no student data
(nothing from ``samples/`` or the database). The bundle is a zip under ``var/diagrams``."""

import shutil
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from tarn_adapters.diagram.dataset import (
    ArrowTruth,
    LabelledObject,
    Sample,
    read_manifest,
    write_manifest,
)
from tarn_adapters.diagram.train import content_box, cropped, read_image

CROP_MARGIN = 0.2


def _prepare(manifest: Path, sample: Sample, max_side: int) -> tuple[np.ndarray, Sample]:
    image = read_image(manifest, sample)
    x0 = y0 = 0
    if cropped(sample):
        x0, y0, x1, y1 = content_box(sample, CROP_MARGIN)
        image = image[y0:y1, x0:x1]
    h, w = image.shape[:2]
    f = min(1.0, max_side / max(h, w))
    if f < 1.0:
        image = cv2.resize(
            image, (max(1, round(w * f)), max(1, round(h * f))), interpolation=cv2.INTER_AREA
        )

    def box(b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return ((b[0] - x0) * f, (b[1] - y0) * f, (b[2] - x0) * f, (b[3] - y0) * f)

    def point(p: tuple[float, float]) -> tuple[float, float]:
        return ((p[0] - x0) * f, (p[1] - y0) * f)

    out = replace(
        sample,
        width=image.shape[1],
        height=image.shape[0],
        objects=tuple(LabelledObject(cls=o.cls, box=box(o.box)) for o in sample.objects),
        arrows=tuple(
            ArrowTruth(
                box=box(a.box),
                tail=point(a.tail),
                head=point(a.head),
                has_head=a.has_head,
                derived=a.derived,
            )
            for a in sample.arrows
        ),
        extra={**sample.extra, "prepared": True},
    )
    return image, out


def pack(
    manifests: Sequence[Path],
    out: Path,
    code_roots: Sequence[Path],
    *,
    max_side: int = 1280,
    log: Callable[[str], None] = print,
) -> Path:
    """Write ``out`` (a zip) and return it. ``code_roots`` are the package folders to include
    (``.../tarn_core``, ``.../tarn_adapters``)."""
    work = out.with_suffix("")
    if work.exists():
        shutil.rmtree(work)
    (work / "data" / "images").mkdir(parents=True)
    samples: list[Sample] = []
    count = 0
    for manifest in manifests:
        for sample in read_manifest(manifest):
            image, prepared = _prepare(manifest, sample, max_side)
            name = f"images/{count:06d}.jpg"
            cv2.imwrite(str(work / "data" / name), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
            samples.append(replace(prepared, image=name))
            count += 1
            if count % 500 == 0:
                log(f"{count} images packed")
    write_manifest(work / "data" / "manifest.jsonl", samples)
    for root in code_roots:
        shutil.copytree(
            root, work / "code" / root.name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:  # JPEGs do not compress further
        for f in sorted(work.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(work))
    shutil.rmtree(work)
    log(f"{count} images; bundle {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out
