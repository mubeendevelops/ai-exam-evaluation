"""Synthetic hand-drawn diagrams with exact labels: the only training and test material for
block diagrams, trees and networks (the public sets are flowcharts), and phone-photo-like
pages (ruled paper, blue or black pen, noise, blur) closer to the booklets than the public
scans.

Every stroke wobbles a little, shapes vary in size and proportion, arrows are straight or bent
once with an open or filled head, trees and networks use plain lines (no head) or arrows, and
words are written inside shapes, beside arrows and as stray captions, so the detector learns
that text is not a shape. All drawn by OpenCV from a seeded random generator: the same seed
gives the same set."""

import itertools
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from tarn_adapters.diagram.dataset import (
    ArrowTruth,
    BoxList,
    LabelledObject,
    Sample,
    write_manifest,
)
from tarn_adapters.ocr.images import as_image

type Image = NDArray[np.uint8]
KINDS = ("flowchart", "block", "tree", "network")
_WORDS = (
    "start", "stop", "end", "read n", "print n", "sum = a + b", "i = i + 1", "x > 0 ?",
    "n % 2 == 0", "input x", "output y", "process", "init", "load data", "train", "sensor",
    "controller", "plant", "filter", "A", "B", "C", "D", "E", "root", "x1", "x2", "w1", "f(x)",
    "is n > 0?", "count < 10", "return", "error", "valid?", "update", "display",
)  # fmt: skip
_FONTS = (cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_PLAIN)


@dataclass
class _Shape:
    cls: str
    cx: float
    cy: float
    w: float
    h: float
    box: BoxList = (0.0, 0.0, 1.0, 1.0)

    def anchor(self, toward: tuple[float, float]) -> tuple[float, float]:
        """The middle of the side facing ``toward`` (a circle: its edge on the centre line)."""
        dx, dy = toward[0] - self.cx, toward[1] - self.cy
        if self.cls == "circle":
            r = self.w / 2
            d = math.hypot(dx, dy) or 1.0
            return (self.cx + dx / d * r, self.cy + dy / d * r)
        if abs(dx) * self.h > abs(dy) * self.w:
            return (self.cx + math.copysign(self.w / 2, dx), self.cy)
        return (self.cx, self.cy + math.copysign(self.h / 2, dy))


class Painter:
    def __init__(self, rng: random.Random, image: Image) -> None:
        self.rng = rng
        self.image = image
        self.ink = rng.choice([(30, 30, 30), (140, 40, 20), (120, 30, 30), (60, 60, 60)])
        self.thickness = rng.choice([1, 2, 2, 3])
        self.wobble = rng.uniform(0.3, 1.8)

    def stroke(
        self, points: Sequence[tuple[float, float]], *, closed: bool = False
    ) -> list[tuple[float, float]]:
        pts = list(points) + ([points[0]] if closed else [])
        drawn: list[tuple[float, float]] = []
        for (x0, y0), (x1, y1) in itertools.pairwise(pts):
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / 12))
            for k in range(n):
                t = k / n
                drawn.append(
                    (
                        x0 + (x1 - x0) * t + self.rng.gauss(0, self.wobble),
                        y0 + (y1 - y0) * t + self.rng.gauss(0, self.wobble),
                    )
                )
        drawn.append(pts[-1])
        arr = np.array([[round(x), round(y)] for x, y in drawn], np.int32)
        cv2.polylines(self.image, [arr], False, self.ink, self.thickness, cv2.LINE_AA)
        return drawn

    def text(self, word: str, cx: float, cy: float, max_w: float, max_h: float) -> BoxList | None:
        font = self.rng.choice(_FONTS)
        scale = 1.0
        (tw, th), _ = cv2.getTextSize(word, font, scale, 1)
        scale = min(max_w / max(tw, 1), max_h / max(th, 1), 1.4) * self.rng.uniform(0.6, 0.9)
        if scale < 0.3:
            return None
        (tw, th), _ = cv2.getTextSize(word, font, scale, 1)
        x, y = int(cx - tw / 2), int(cy + th / 2)
        cv2.putText(
            self.image, word, (x, y), font, scale, self.ink, max(1, self.thickness - 1), cv2.LINE_AA
        )
        return (float(x), float(y - th), float(x + tw), float(y + 4))


def _bbox(points: Sequence[tuple[float, float]], pad: float, w: int, h: int) -> BoxList:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (
        max(0.0, min(xs) - pad),
        max(0.0, min(ys) - pad),
        min(float(w - 1), max(xs) + pad),
        min(float(h - 1), max(ys) + pad),
    )


def _outline(s: _Shape, rng: random.Random) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = s.cx - s.w / 2, s.cy - s.h / 2, s.cx + s.w / 2, s.cy + s.h / 2
    if s.cls == "process":
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if s.cls == "decision":
        return [(s.cx, y0), (x1, s.cy), (s.cx, y1), (x0, s.cy)]
    if s.cls == "io":
        k = s.w * rng.uniform(0.12, 0.22)
        return [(x0 + k, y0), (x1, y0), (x1 - k, y1), (x0, y1)]
    n = 40
    rx, ry = s.w / 2, s.h / 2
    if s.cls == "terminal" and rng.random() < 0.5:  # a stadium instead of an ellipse
        r = ry
        pts = [
            (x1 - r + r * math.cos(a), s.cy + r * math.sin(a))
            for a in np.linspace(-math.pi / 2, math.pi / 2, n // 2)
        ]
        pts += [
            (x0 + r + r * math.cos(a), s.cy + r * math.sin(a))
            for a in np.linspace(math.pi / 2, 3 * math.pi / 2, n // 2)
        ]
        return pts
    return [
        (s.cx + rx * math.cos(a), s.cy + ry * math.sin(a))
        for a in np.linspace(0, 2 * math.pi, n, endpoint=False)
    ]


def _overlap(a: _Shape, b: _Shape, margin: float) -> bool:
    return (
        abs(a.cx - b.cx) < (a.w + b.w) / 2 + margin and abs(a.cy - b.cy) < (a.h + b.h) / 2 + margin
    )


def _layout(
    kind: str, rng: random.Random, width: int, height: int
) -> tuple[list[_Shape], list[tuple[int, int]], bool]:
    """Shapes, edges (tail → head) and whether edges have heads."""
    shapes: list[_Shape] = []
    edges: list[tuple[int, int]] = []
    if kind == "flowchart":
        n = rng.randint(3, 7)
        col = width * rng.uniform(0.3, 0.45)
        step = (height - 120) / n
        for k in range(n):
            if k in (0, n - 1):
                cls = "terminal"
            else:
                cls = rng.choice(["process", "process", "io", "decision"])
            w = rng.uniform(110, 220) if cls != "decision" else rng.uniform(130, 200)
            h = (
                rng.uniform(40, min(70, step * 0.55))
                if cls != "decision"
                else min(step * 0.7, rng.uniform(70, 110))
            )
            shapes.append(_Shape(cls, col + rng.uniform(-10, 10), 60 + step * (k + 0.5), w, h))
            if k:
                edges.append((k - 1, k))
        for k, s in enumerate(shapes):
            if s.cls == "decision" and k + 1 < n:
                side = _Shape(
                    rng.choice(["process", "io"]),
                    min(width - 120, s.cx + rng.uniform(230, 330)),
                    s.cy + rng.uniform(-10, 60),
                    rng.uniform(100, 170),
                    rng.uniform(40, 60),
                )
                if any(_overlap(side, other, 20) for other in shapes):
                    continue  # two decisions in a row: no room for a second side box
                shapes.append(side)
                edges.append((k, len(shapes) - 1))
                edges.append((len(shapes) - 1, rng.randint(k + 1, n - 1)))
        return shapes, edges, True
    if kind == "block":
        rows, cols = rng.randint(1, 3), rng.randint(2, 4)
        for r in range(rows):
            for c in range(cols):
                shapes.append(
                    _Shape(
                        "process",
                        (c + 0.5) * width / cols + rng.uniform(-15, 15),
                        (r + 0.5) * height / rows + rng.uniform(-15, 15),
                        rng.uniform(0.45, 0.65) * width / cols,
                        rng.uniform(0.25, 0.4) * height / rows,
                    )
                )
        for r in range(rows):
            for c in range(cols - 1):
                edges.append((r * cols + c, r * cols + c + 1))
        for _ in range(rng.randint(0, rows)):
            a, b = rng.sample(range(len(shapes)), 2)
            if (a, b) not in edges and (b, a) not in edges:
                edges.append((a, b))
        return shapes, edges, True
    if kind == "tree":
        depth = rng.randint(2, 4)
        radius = rng.uniform(18, 32)
        levels: list[list[int]] = [[0]]
        shapes.append(_Shape("circle", width / 2, 60, 2 * radius, 2 * radius))
        for d in range(1, depth):
            nxt: list[int] = []
            for parent in levels[-1]:
                for _ in range(rng.randint(1 if d > 1 else 2, 3)):
                    nxt.append(len(shapes))
                    shapes.append(
                        _Shape(
                            "circle",
                            0,
                            60 + d * (height - 120) / (depth - 1),
                            2 * radius,
                            2 * radius,
                        )
                    )
                    edges.append((parent, len(shapes) - 1))
            for k, i in enumerate(nxt):
                shapes[i].cx = (k + 0.5) * width / len(nxt)
            levels.append(nxt)
            if len(nxt) > 12:
                break
        return shapes, edges, rng.random() < 0.3
    n = rng.randint(4, 9)
    radius = rng.uniform(18, 34)
    tries = 0
    while len(shapes) < n and tries < 500:
        tries += 1
        cx, cy = rng.uniform(60, width - 60), rng.uniform(60, height - 60)
        if all(math.hypot(cx - s.cx, cy - s.cy) > 5 * radius for s in shapes):
            shapes.append(_Shape("circle", cx, cy, 2 * radius, 2 * radius))
    for i in range(1, len(shapes)):
        edges.append((rng.randrange(i), i))
    for _ in range(rng.randint(0, len(shapes))):
        a, b = rng.sample(range(len(shapes)), 2)
        if (a, b) not in edges and (b, a) not in edges:
            edges.append((a, b))
    return shapes, edges, rng.random() < 0.5


def _arrow_path(a: _Shape, b: _Shape, rng: random.Random, bend: bool) -> list[tuple[float, float]]:
    start = a.anchor((b.cx, b.cy))
    if (
        bend
        and a.cls != "circle"
        and b.cls != "circle"
        and abs(a.cx - b.cx) > 40
        and abs(a.cy - b.cy) > 40
    ):
        # out of the side, then down/up into the target's top/bottom
        start = (a.cx + math.copysign(a.w / 2, b.cx - a.cx), a.cy)
        end = (b.cx, b.cy - math.copysign(b.h / 2, b.cy - a.cy))
        return [start, (end[0], start[1]), end]
    end = b.anchor((a.cx, a.cy))
    return [start, end]


def render(rng: random.Random, kind: str | None = None) -> tuple[Image, Sample]:
    kind = kind or rng.choice(KINDS)
    width, height = rng.randint(700, 1300), rng.randint(600, 1300)
    paper = rng.randint(215, 255)
    image = np.full((height, width, 3), paper, np.uint8)
    image[:, :, 0] = np.clip(image[:, :, 0].astype(int) - rng.randint(0, 15), 0, 255).astype(
        np.uint8
    )
    if rng.random() < 0.5:  # ruled paper
        gap = rng.randint(28, 45)
        for y in range(rng.randint(5, gap), height, gap):
            cv2.line(image, (0, y), (width, y), (200, 170, 150), 1)
    p = Painter(rng, image)
    shapes, edges, heads = _layout(kind, rng, width, height)
    objects: list[LabelledObject] = []
    for s in shapes:
        drawn = p.stroke(_outline(s, rng), closed=True)
        s.box = _bbox(drawn, p.thickness / 2 + 1, width, height)
        objects.append(LabelledObject(cls=s.cls, box=s.box))
        if kind != "network" or rng.random() < 0.7:
            p.text(rng.choice(_WORDS), s.cx, s.cy, s.w * 0.7, s.h * 0.45)
    arrows: list[ArrowTruth] = []
    heads_out: list[LabelledObject] = []
    kept: list[tuple[int, int]] = []
    for a, b in edges:
        path = _arrow_path(shapes[a], shapes[b], rng, bend=rng.random() < 0.5)
        drawn = p.stroke(path)
        points = list(drawn)
        if heads:
            (x0, y0), (x1, y1) = path[-2], path[-1]
            angle = math.atan2(y1 - y0, x1 - x0)
            size, spread = rng.uniform(9, 18), math.radians(rng.uniform(20, 35))
            wings = [
                (x1 - size * math.cos(angle - spread), y1 - size * math.sin(angle - spread)),
                (x1 - size * math.cos(angle + spread), y1 - size * math.sin(angle + spread)),
            ]
            if rng.random() < 0.5:
                tri = np.array(
                    [[round(x1), round(y1)], *[[round(x), round(y)] for x, y in wings]], np.int32
                )
                cv2.fillPoly(image, [tri], p.ink, cv2.LINE_AA)
            else:
                p.stroke([wings[0], (x1, y1), wings[1]])
            head_pts = [*wings, (x1, y1)]
            points += head_pts
            heads_out.append(
                LabelledObject(cls="arrow_head", box=_bbox(head_pts, 2, width, height))
            )
        box = _bbox(points, p.thickness / 2 + 1, width, height)
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            box = (box[0], box[1], max(box[2], box[0] + 3), max(box[3], box[1] + 3))
        objects.append(LabelledObject(cls="arrow", box=box))
        arrows.append(ArrowTruth(box=box, tail=path[0], head=path[-1], has_head=heads))
        kept.append((a, b))
        if kind == "flowchart" and shapes[a].cls == "decision" and rng.random() < 0.8:
            mx, my = path[0]
            p.text(rng.choice(["yes", "no", "Y", "N", "T", "F"]), mx + 25, my - 12, 50, 20)
    for _ in range(rng.randint(0, 2)):  # stray captions
        p.text(
            rng.choice(_WORDS), rng.uniform(80, width - 80), rng.uniform(20, height - 20), 160, 25
        )
    objects += heads_out
    noise = np.random.default_rng(rng.randrange(1 << 30)).normal(0, rng.uniform(0, 8), image.shape)
    image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        image = as_image(cv2.GaussianBlur(image, (3, 3), rng.uniform(0.3, 1.0)))
    sample = Sample(
        image="",
        width=width,
        height=height,
        source="synthetic",
        split="train",
        objects=tuple(objects),
        arrows=tuple(arrows),
        edges=tuple(kept),
        extra={"kind": kind},
    )
    return image, sample


def write_synthetic(out_dir: Path, count: int, *, seed: int = 14) -> Path:
    """``count`` drawings as PNG plus ``manifest.jsonl`` (80 % train, 10 % val, 10 % test)."""
    rng = random.Random(seed)  # noqa: S311  (test data, not secrets)
    images = out_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    samples: list[Sample] = []
    for k in range(count):
        image, sample = render(rng, KINDS[k % len(KINDS)])
        name = f"images/synth-{k:05d}.png"
        cv2.imwrite(str(out_dir / name), image)
        split = "test" if k % 10 == 9 else "val" if k % 10 == 8 else "train"
        samples.append(replace(sample, image=name, split=split))
    manifest = out_dir / "manifest.jsonl"
    write_manifest(manifest, samples)
    return manifest
