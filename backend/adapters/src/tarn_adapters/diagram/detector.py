"""The shape-and-arrow detector (``shape-detector``): RT-DETR (``transformers``, Apache-2.0)
with a ResNet-18 backbone, fine-tuned by ``tarn diagram train`` from
``PekingU/rtdetr_r18vd_coco_o365`` (Apache-2.0) on the FC database, Flowchart 3b and the
synthetic set (D100). Classes: terminal, process, decision, io, circle, arrow, arrow_head.

The image (or the diagram region of a page) is letterboxed to a square of ``size`` pixels on
white; the model's boxes are mapped back to the image's pixels, thresholded per class and
de-duplicated per class (NMS). Each arrow takes the most confident head box inside it; its
tail and tip come from the ink (``geometry``). Weights load from ``var/models/diagram/<name>``
only (never from the hub at run time); on CUDA the model holds the GPU slot, and runs out of
memory → CPU."""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from tarn_adapters.compute import GPU_SLOT, DeviceInfo, ModelSlot
from tarn_adapters.diagram.dataset import CLASS_TO_SHAPE, CLASSES
from tarn_adapters.diagram.geometry import FBox, arrow_ends, head_for, nms, to_box, to_point
from tarn_adapters.ocr.images import as_image, decode
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.diagram import DetectedArrow, DetectedShape, DiagramDetection
from tarn_core.errors import EngineFailedError

NAME = "shape-detector"
DEFAULT_SIZE = 1024
DEFAULT_THRESHOLD = 0.35
MANIFEST = "tarn-detector.json"


@dataclass(frozen=True, slots=True)
class Detection:
    cls: str
    box: FBox
    score: float


def letterbox(image: NDArray[np.uint8], size: int) -> tuple[NDArray[np.float32], float]:
    """RGB in [0, 1], CHW, the image scaled to fit ``size`` and padded with white."""
    h, w = image.shape[:2]
    scale = size / max(h, w)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_AREA)
    canvas = np.full((size, size, 3), 255, np.uint8)
    canvas[:nh, :nw] = resized if resized.ndim == 3 else resized[:, :, None]
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    return (rgb.astype(np.float32) / 255.0).transpose(2, 0, 1), scale


def decode_outputs(
    logits: Any, boxes: Any, *, scale: float, size: int, offset: tuple[int, int], threshold: float
) -> list[Detection]:
    """One image's queries → detections above ``threshold`` in the original pixels."""
    probs = 1.0 / (1.0 + np.exp(-np.asarray(logits, np.float64)))  # sigmoid (focal loss)
    raw = np.asarray(boxes, np.float64)
    out: list[Detection] = []
    for q in range(probs.shape[0]):
        k = int(np.argmax(probs[q]))
        score = float(probs[q, k])
        if score < threshold:
            continue
        cx, cy, bw, bh = raw[q] * size / scale
        out.append(
            Detection(
                cls=CLASSES[k],
                box=(
                    cx - bw / 2 + offset[0],
                    cy - bh / 2 + offset[1],
                    cx + bw / 2 + offset[0],
                    cy + bh / 2 + offset[1],
                ),
                score=score,
            )
        )
    return out


SAME_OBJECT_IOU = {"arrow": 0.8, "arrow_head": 0.7}
"""Arrows leaving one node side by side, and heads meeting at one node, overlap a lot while
being different objects: they are merged only when nearly identical."""


def suppress(detections: Sequence[Detection], iou_threshold: float = 0.5) -> list[Detection]:
    """Per-class NMS (looser for arrows and heads); shapes of different classes on the same
    spot keep the most confident."""
    kept: list[Detection] = []
    for cls in CLASSES:
        group = [d for d in detections if d.cls == cls]
        limit = SAME_OBJECT_IOU.get(cls, iou_threshold)
        kept += [group[i] for i in nms([d.box for d in group], [d.score for d in group], limit)]
    shapes = [d for d in kept if d.cls in CLASS_TO_SHAPE]
    others = [d for d in kept if d.cls not in CLASS_TO_SHAPE]
    best = [shapes[i] for i in nms([d.box for d in shapes], [d.score for d in shapes], 0.7)]
    return best + others


def to_detection(
    detections: Sequence[Detection], gray: NDArray[np.uint8], width: int, height: int
) -> DiagramDetection:
    shapes: list[DetectedShape] = []
    for d in detections:
        if d.cls in CLASS_TO_SHAPE:
            box = to_box(d.box, width, height)
            if box is not None:
                shapes.append(
                    DetectedShape(
                        shape=CLASS_TO_SHAPE[d.cls], box=box, confidence=round(d.score, 4)
                    )
                )
    heads = [(d.box, d.score) for d in detections if d.cls == "arrow_head"]
    arrows: list[DetectedArrow] = []
    for d in detections:
        if d.cls != "arrow":
            continue
        box = to_box(d.box, width, height)
        if box is None:
            continue
        k = head_for(d.box, heads)
        head_box = None if k is None else heads[k][0]
        tail, tip = arrow_ends(gray, d.box, head_box)
        arrows.append(
            DetectedArrow(
                box=box,
                tail=to_point(tail, width, height),
                head=to_point(tip, width, height),
                has_head=head_box is not None,
                confidence=round(d.score, 4),
            )
        )
    return DiagramDetection(width=width, height=height, shapes=tuple(shapes), arrows=tuple(arrows))


@dataclass
class _Loaded:
    model: Any
    device: str


class ShapeDetector:
    """Raw detections (for the benchmark) and the recognizer port (``recognize``)."""

    def __init__(
        self,
        model_dir: Path,
        *,
        device: DeviceInfo,
        slot: ModelSlot = GPU_SLOT,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        manifest = json.loads((model_dir / MANIFEST).read_text())
        if tuple(manifest["classes"]) != CLASSES:
            raise EngineFailedError("the detector was trained for other classes")
        self._dir = model_dir
        self._device = device
        self._slot = slot
        self._threshold = threshold
        self.size = int(manifest.get("size", DEFAULT_SIZE))
        self.ref = EngineRef(name=NAME, version=str(manifest["version"]))
        self._cpu: _Loaded | None = None
        self.peak_gpu_bytes = 0

    def _load(self, device: str) -> _Loaded:
        import torch
        from transformers import RTDetrForObjectDetection

        model = RTDetrForObjectDetection.from_pretrained(self._dir, local_files_only=True)
        model.eval()
        return _Loaded(model=model.to(torch.device(device)), device=device)

    def _model(self) -> _Loaded:
        if self._device.kind == "cuda" and self._cpu is None:
            return self._slot.acquire(NAME, lambda: self._load("cuda"), _release)
        if self._cpu is None:
            self._cpu = self._load("cpu")
        return self._cpu

    def detect(
        self,
        image: NDArray[np.uint8],
        *,
        offset: tuple[int, int] = (0, 0),
        threshold: float | None = None,
    ) -> list[Detection]:
        """Detections in ``image`` (BGR), shifted by ``offset``, before NMS."""
        import torch

        pixels, scale = letterbox(image, self.size)
        loaded = self._model()
        try:
            with torch.inference_mode():
                batch = torch.from_numpy(pixels[None]).to(loaded.device)
                out = loaded.model(pixel_values=batch)
        except Exception as error:
            if loaded.device == "cuda" and "out of memory" in str(error).lower():
                self._slot.free()
                self._cpu = self._load("cpu")
                return self.detect(image, offset=offset, threshold=threshold)
            raise EngineFailedError("the shape detector failed") from error
        if loaded.device == "cuda":
            self.peak_gpu_bytes = max(self.peak_gpu_bytes, int(torch.cuda.max_memory_allocated()))
        return decode_outputs(
            out.logits[0].float().cpu().numpy(),
            out.pred_boxes[0].float().cpu().numpy(),
            scale=scale,
            size=self.size,
            offset=offset,
            threshold=self._threshold if threshold is None else threshold,
        )

    def recognize(self, image: bytes, box: Box | None = None) -> DiagramDetection:
        pixels = decode(image)
        height, width = pixels.shape[:2]
        offset = (0, 0)
        region = pixels
        if box is not None:
            pad = 10
            x0, y0 = max(0, box.x0 - pad), max(0, box.y0 - pad)
            x1, y1 = min(width, box.x1 + pad), min(height, box.y1 + pad)
            if x1 - x0 < 8 or y1 - y0 < 8:
                raise EngineFailedError("the diagram region is empty")
            region, offset = pixels[y0:y1, x0:x1], (x0, y0)
        started = time.perf_counter()
        found = suppress(self.detect(region, offset=offset))
        gray = as_image(cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY))
        self.last_seconds = time.perf_counter() - started
        return to_detection(found, gray, width, height)


def _release(loaded: _Loaded) -> None:
    import torch

    loaded.model.to("cpu")
    del loaded.model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
