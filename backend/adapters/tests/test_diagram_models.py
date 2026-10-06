"""The trained detector (``make test-models``): it reads a synthetic flowchart drawn here into
a graph whose structure matches the drawing, on the GPU when there is one and on the CPU.
Skipped when no trained model is installed (``tarn diagram train`` / ``publish``)."""

import random

import cv2
import pytest

from tarn_adapters.compute import CPU_ONLY, detect_device
from tarn_adapters.config import Settings
from tarn_adapters.diagram.detector import MANIFEST, ShapeDetector
from tarn_adapters.diagram.synth import render
from tarn_adapters.diagram.wiring import model_path
from tarn_core.domain.common import EngineRef
from tarn_core.services.diagrams.build import GraphBuilder
from tarn_core.services.diagrams.compare import GraphComparator
from tarn_core.services.diagrams.graph_json import graph_to_json

FOLDER = model_path(Settings())

pytestmark = [
    pytest.mark.models,
    pytest.mark.skipif(not (FOLDER / MANIFEST).exists(), reason="no trained detector installed"),
]


def _truth_graph(sample: object) -> object:
    from tarn_core.domain.diagram import DiagramEdge, DiagramGraph, DiagramNode, NodeShape

    shapes = sample.shapes  # type: ignore[attr-defined]
    return DiagramGraph(
        nodes=tuple(DiagramNode(id=f"t{i}", shape=NodeShape(s.cls)) for i, s in enumerate(shapes)),
        edges=tuple(
            DiagramEdge(id=f"x{k}", source=f"t{i}", target=f"t{j}")
            for k, (i, j) in enumerate(sample.edges)  # type: ignore[attr-defined]
        ),
    )


@pytest.mark.parametrize("device", ["auto", "cpu"])
def test_the_detector_reads_a_drawn_flowchart(device: str) -> None:
    image, sample = render(random.Random(21), "flowchart")  # noqa: S311  (test data)
    ok, png = cv2.imencode(".png", image)
    assert ok
    info = detect_device("auto") if device == "auto" else CPU_ONLY
    detector = ShapeDetector(FOLDER, device=info)
    detection = detector.recognize(png.tobytes())
    graph = GraphBuilder().build(detection, [], recognizer=EngineRef(name="t", version="1"))
    assert graph_to_json(graph)["schema_version"] == "1.0"
    assert sorted(n.shape.value for n in graph.nodes) == sorted(s.cls for s in sample.shapes)
    result = GraphComparator().compare(_truth_graph(sample), graph, [])  # type: ignore[arg-type]
    assert result.sub_scores.nodes >= 0.9
    assert result.sub_scores.edges >= 0.6
    if info.kind == "cuda":
        assert detector.peak_gpu_bytes < 4 * 1024**3  # fits the 4 GB development GPU
