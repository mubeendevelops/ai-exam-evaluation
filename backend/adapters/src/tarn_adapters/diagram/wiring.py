"""The diagram recognizers of this process, per diagram kind (the plug-in point of design.md
"Scope"): the shape-and-arrow detector reads flowcharts, block diagrams, networks and trees;
circuits, plots and labelled drawings get their own recognizers later. A missing model folder
or library leaves the map empty with the reason (logged by the worker): drawings are then
stored without a graph and their criteria go to the teacher."""

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings
from tarn_core.domain.diagram import NODE_AND_EDGE_KINDS, DiagramKind
from tarn_core.ports.engines import DiagramRecognizer


@dataclass(frozen=True, slots=True)
class RecognizerSetup:
    recognizers: dict[DiagramKind, DiagramRecognizer] = field(default_factory=dict)
    skipped: str | None = None


def model_path(settings: Settings) -> Path:
    return settings.model_dir / "diagram" / settings.diagram_model


def build_recognizers(settings: Settings) -> RecognizerSetup:
    from tarn_adapters.diagram.detector import MANIFEST, ShapeDetector

    folder = model_path(settings)
    if not (folder / MANIFEST).exists():
        return RecognizerSetup(skipped=f"no trained detector at {folder} (tarn diagram train)")
    for library in ("torch", "transformers"):
        if importlib.util.find_spec(library) is None:
            return RecognizerSetup(skipped=f"{library} is not installed (dependency group ocr)")
    detector = ShapeDetector(
        folder, device=detect_device(settings.device), threshold=settings.diagram_threshold
    )
    return RecognizerSetup(recognizers=dict.fromkeys(NODE_AND_EDGE_KINDS, detector))
