"""The detector report (``tarn diagram bench`` → ``docs/benchmarks/diagram-detector-<date>.md``).

Per test source and class: precision and recall at IoU ≥ 0.5 at the operating threshold, and
AP@0.5 (all-point interpolation over detections down to 0.05). Arrows: of the true arrows
matched by a detected arrow, the share whose tail and head points fall within 5 % of the image
diagonal (heads of Flowchart 3b are placed by direction, not annotated: reported apart). Graphs
(sources with the true graph: FC and synthetic): the core's ``GraphBuilder`` turns the
detections into edges; precision and recall of tail → head edges between shapes matched at
IoU ≥ 0.5 (and the same ignoring direction). Timing per image and peak GPU memory. Numbers
and class names only."""

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from tarn_adapters.diagram.dataset import CLASSES, SHAPE_CLASSES, Sample
from tarn_adapters.diagram.detector import Detection, suppress, to_detection
from tarn_adapters.diagram.geometry import iou
from tarn_adapters.diagram.train import content_box, cropped, read_image
from tarn_adapters.ocr.images import as_image
from tarn_core.domain.common import EngineRef
from tarn_core.services.diagrams.build import GraphBuilder

MATCH_IOU = 0.5
END_TOLERANCE = 0.05


class Detector(Protocol):
    def detect(
        self,
        image: NDArray[np.uint8],
        *,
        offset: tuple[int, int] = (0, 0),
        threshold: float | None = None,
    ) -> list[Detection]: ...


@dataclass
class ClassCounts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    scored: list[tuple[float, bool]] = field(default_factory=list)
    truths: int = 0

    @property
    def precision(self) -> float | None:
        return None if self.tp + self.fp == 0 else self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return None if self.tp + self.fn == 0 else self.tp / (self.tp + self.fn)

    @property
    def ap(self) -> float | None:
        if self.truths == 0:
            return None
        ranked = sorted(self.scored, key=lambda x: -x[0])
        tp = fp = 0
        points: list[tuple[float, float]] = []
        for _, hit in ranked:
            tp += hit
            fp += not hit
            points.append((tp / self.truths, tp / (tp + fp)))
        ap, prev_recall = 0.0, 0.0
        for k, (rec, _) in enumerate(points):
            best_precision = max(p for _, p in points[k:])
            ap += (rec - prev_recall) * best_precision
            prev_recall = rec
        return ap


@dataclass
class SourceResult:
    images: int = 0
    classes: dict[str, ClassCounts] = field(
        default_factory=lambda: {c: ClassCounts() for c in CLASSES}
    )
    arrows_matched: int = 0
    tails_ok: int = 0
    heads_ok: int = 0
    heads_derived: bool = False
    edges_true: int = 0
    edges_found: int = 0
    edges_right: int = 0
    edges_right_any_direction: int = 0
    graphs: int = 0
    seconds: list[float] = field(default_factory=list)


def match(
    truths: Sequence[tuple[float, float, float, float]],
    found: Sequence[Detection],
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy one-to-one matching by score: (truth, found) pairs, unmatched found, unmatched
    truths."""
    order = sorted(range(len(found)), key=lambda k: -found[k].score)
    used: set[int] = set()
    pairs: list[tuple[int, int]] = []
    unmatched: list[int] = []
    for k in order:
        best, best_iou = None, MATCH_IOU
        for t, box in enumerate(truths):
            if t in used:
                continue
            v = iou(box, found[k].box)
            if v >= best_iou:
                best, best_iou = t, v
        if best is None:
            unmatched.append(k)
        else:
            used.add(best)
            pairs.append((best, k))
    return pairs, unmatched, [t for t in range(len(truths)) if t not in used]


def evaluate(
    detector: Detector,
    items: Sequence[tuple[Path, Sample]],
    *,
    threshold: float,
    load: Callable[[Path, Sample], NDArray[np.uint8]] = read_image,
) -> dict[str, SourceResult]:
    results: dict[str, SourceResult] = {}
    builder = GraphBuilder()
    for manifest, sample in items:
        r = results.setdefault(sample.source, SourceResult())
        r.images += 1
        r.heads_derived = r.heads_derived or any(a.derived for a in sample.arrows)
        image = load(manifest, sample)
        offset = (0, 0)
        region = image
        if cropped(sample):  # the pipeline gives the detector the diagram's region
            x0, y0, x1, y1 = content_box(sample, 0.08)
            region, offset = image[y0:y1, x0:x1], (x0, y0)
        started = time.perf_counter()
        everything = detector.detect(region, offset=offset, threshold=0.05)
        r.seconds.append(time.perf_counter() - started)
        kept = suppress([d for d in everything if d.score >= threshold])
        for cls in CLASSES:
            truths = [o.box for o in sample.objects if o.cls == cls]
            c = r.classes[cls]
            c.truths += len(truths)
            pairs, extra, missed = match(truths, [d for d in kept if d.cls == cls])
            c.tp += len(pairs)
            c.fp += len(extra)
            c.fn += len(missed)
            ranked = suppress([d for d in everything if d.cls == cls])
            ranked_pairs, ranked_extra, _ = match(truths, ranked)
            c.scored += [(ranked[k].score, True) for _, k in ranked_pairs]
            c.scored += [(ranked[k].score, False) for k in ranked_extra]
        gray = as_image(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        detection = to_detection(kept, gray, sample.width, sample.height)
        diag = math.hypot(sample.width, sample.height)
        found_arrows = [
            Detection(cls="arrow", box=(a.box.x0, a.box.y0, a.box.x1, a.box.y1), score=a.confidence)
            for a in detection.arrows
        ]
        pairs, _, _ = match([a.box for a in sample.arrows], found_arrows)
        for t, k in pairs:
            truth, got = sample.arrows[t], detection.arrows[k]
            r.arrows_matched += 1
            ends = [(got.tail.x, got.tail.y), (got.head.x, got.head.y)]
            if not truth.has_head or not got.has_head:  # a line: ends in either order
                d1 = _d(ends[0], truth.tail) + _d(ends[1], truth.head)
                d2 = _d(ends[1], truth.tail) + _d(ends[0], truth.head)
                if d2 < d1:
                    ends.reverse()
            r.tails_ok += _d(ends[0], truth.tail) <= END_TOLERANCE * diag
            r.heads_ok += _d(ends[1], truth.head) <= END_TOLERANCE * diag
        if sample.edges is not None:
            r.graphs += 1
            graph = builder.build(detection, [], recognizer=EngineRef(name="bench", version="1"))
            shapes = sample.shapes
            node_boxes = [
                Detection(
                    cls="process", box=(n.box.x0, n.box.y0, n.box.x1, n.box.y1), score=n.confidence
                )
                for n in graph.nodes
                if n.box is not None
            ]
            node_pairs, _, _ = match([s.box for s in shapes], node_boxes)
            to_truth = {graph.nodes[k].id: t for t, k in node_pairs}
            truth_edges = set(sample.edges)
            undirected = {frozenset(e) for e in truth_edges}
            r.edges_true += len(truth_edges)
            for e in graph.edges:
                if e.source is None or e.target is None:
                    continue
                r.edges_found += 1
                a, b = to_truth.get(e.source), to_truth.get(e.target)
                if a is None or b is None:
                    continue
                r.edges_right += (a, b) in truth_edges
                r.edges_right_any_direction += frozenset((a, b)) in undirected
    return results


def _d(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _share(part: int, whole: int) -> float | None:
    return None if whole == 0 else part / whole


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.1f} %"


def render_report(
    results: dict[str, SourceResult],
    *,
    model: str,
    manifest: dict[str, object],
    threshold: float,
    device: str,
    peak_gpu_gb: float | None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    lines = [
        f"# Diagram detector report — {today.isoformat()}",
        "",
        f"Model `{model}` (RT-DETR R18, {len(CLASSES)} classes), operating threshold "
        f"{threshold}, matching at IoU ≥ {MATCH_IOU}. Test splits only; numbers and class "
        "names only. Generated by `tarn diagram bench`.",
        "",
        f"Device: {device}"
        + ("" if peak_gpu_gb is None else f"; peak GPU memory {peak_gpu_gb:.2f} GB"),
        "",
    ]
    images = manifest.get("training_images")
    if images:
        lines += [f"Training images per source: {images}.", ""]
    history = manifest.get("history")
    if isinstance(history, list) and history:
        last = history[-1]
        lines += [
            f"Training: {len(history)} epochs; last epoch {last}; peak training GPU memory "
            f"{max(float(h.get('peak_gpu_gb', 0)) for h in history):.2f} GB.",
            "",
        ]
    for source, r in sorted(results.items()):
        lines += [
            f"## {source} ({r.images} images)",
            "",
            "| Class | Truths | Precision | Recall | AP@0.5 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        for cls in CLASSES:
            c = r.classes[cls]
            if c.truths == 0 and c.tp + c.fp == 0:
                continue
            lines.append(
                f"| {cls} | {c.truths} | {_pct(c.precision)} | {_pct(c.recall)} | {_pct(c.ap)} |"
            )
        shape_tp = sum(r.classes[c].tp for c in SHAPE_CLASSES)
        shape_fp = sum(r.classes[c].fp for c in SHAPE_CLASSES)
        shape_fn = sum(r.classes[c].fn for c in SHAPE_CLASSES)
        lines += [
            f"| all shapes | {shape_tp + shape_fn} | "
            f"{_pct(shape_tp / (shape_tp + shape_fp) if shape_tp + shape_fp else None)} | "
            f"{_pct(shape_tp / (shape_tp + shape_fn) if shape_tp + shape_fn else None)} | |",
            "",
        ]
        if r.arrows_matched:
            note = " (heads placed by direction, not annotated)" if r.heads_derived else ""
            lines += [
                f"Arrow ends (of {r.arrows_matched} matched arrows, within "
                f"{int(END_TOLERANCE * 100)} % of the diagonal): tail "
                f"{_pct(r.tails_ok / r.arrows_matched)}, head "
                f"{_pct(r.heads_ok / r.arrows_matched)}{note}.",
                "",
            ]
        if r.graphs:
            lines += [
                f"Edges ({r.graphs} graphs, {r.edges_true} true edges, {r.edges_found} found): "
                f"precision {_pct(r.edges_right / r.edges_found if r.edges_found else None)}, "
                f"recall {_pct(r.edges_right / r.edges_true if r.edges_true else None)}; "
                "ignoring direction: precision "
                f"{_pct(_share(r.edges_right_any_direction, r.edges_found))}, "
                f"recall {_pct(_share(r.edges_right_any_direction, r.edges_true))}.",
                "",
            ]
        if r.seconds:
            lines += [
                f"Seconds per image (median): {sorted(r.seconds)[len(r.seconds) // 2]:.3f}.",
                "",
            ]
    lines += [
        "## Notes",
        "",
        "- No handwritten diagram exists in the sample booklets (C24): these are public "
        "flowchart sets and Tarn's synthetic drawings, not student answers.",
        "- The threshold and the arrow-end rule are placeholders until teacher-corrected graphs "
        "exist (the learning loop, O43).",
        "",
    ]
    return "\n".join(lines)
