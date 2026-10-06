"""Diagram adapters without a model (P14): the public-set readers on synthetic XML in their
documented shapes, the synthetic drawings, arrow ends from ink, the detector's decoding and
post-processing, the benchmark on a perfect and an empty detector, and the wiring without a
trained model. No dataset file is read and no sample image is used."""

import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from tarn_adapters.config import Settings
from tarn_adapters.diagram.bench import evaluate, render_report
from tarn_adapters.diagram.dataset import (
    CLASSES,
    ArrowTruth,
    LabelledObject,
    Sample,
    from_json,
    read_manifest,
    split_for,
    to_json,
    write_manifest,
)
from tarn_adapters.diagram.detector import (
    Detection,
    decode_outputs,
    letterbox,
    suppress,
    to_detection,
)
from tarn_adapters.diagram.geometry import arrow_ends, head_for, nms
from tarn_adapters.diagram.sources import (
    ImageSize,
    derived_head,
    read_fc,
    read_fc_annotation,
    read_voc,
    read_voc_annotation,
)
from tarn_adapters.diagram.synth import KINDS, render, write_synthetic
from tarn_adapters.diagram.wiring import build_recognizers
from tarn_core.domain.diagram import NodeShape

FC_XML = """<?xml version="1.0"?>
<diagram>
  <registration><scale>1.0</scale></registration>
  <symbol id="s1" name="terminator"><bounds x="100" y="50" width="200" height="60"/></symbol>
  <symbol id="s2" name="process"><bounds x="100" y="200" width="200" height="80"/></symbol>
  <symbol id="t1" name="text"><bounds x="150" y="70" width="80" height="20"/>
    <textMeaning>start</textMeaning></symbol>
  <symbol id="a1" name="arrow"><bounds x="190" y="110" width="20" height="90"/>
    <arrowAnnotation>
      <headBounds x="190" y="185" width="20" height="15"/>
      <from x="200" y="112"/><to x="200" y="198"/>
    </arrowAnnotation></symbol>
  <symbol id="c1" name="connection"><bounds x="400" y="400" width="30" height="30"/></symbol>
  <relation id="g1" arity="3" name="arrow_connection">
    <symbolView symbolDataRef="s2"/><symbolView symbolDataRef="a1"/>
    <symbolView symbolDataRef="s1"/>
  </relation>
  <symbolGroup id="g2" arity="2" name="text"><symbolView symbolDataRef="t1"/>
    <symbolView symbolDataRef="s1"/></symbolGroup>
</diagram>"""

VOC_XML = """<annotation>
  <folder>train</folder><filename>fc-001.jpg</filename>
  <size><width>640</width><height>480</height><depth>3</depth></size>
  <object><name>start_end</name><bndbox><xmin>10</xmin><ymin>10</ymin><xmax>110</xmax>
    <ymax>50</ymax></bndbox></object>
  <object><name>arrow_line_down</name><bndbox><xmin>55</xmin><ymin>50</ymin><xmax>65</xmax>
    <ymax>150</ymax></bndbox></object>
  <object><name>scan</name><bndbox><xmin>10</xmin><ymin>150</ymin><xmax>110</xmax>
    <ymax>190</ymax></bndbox></object>
  <object><name>something_else</name><bndbox><xmin>1</xmin><ymin>1</ymin><xmax>2</xmax>
    <ymax>2</ymax></bndbox></object>
</annotation>"""


def _png(path: Path, w: int = 640, h: int = 480) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.full((h, w, 3), 255, np.uint8))


# --- sources ---------------------------------------------------------------------------------


def test_fc_annotation_classes_heads_and_edges() -> None:
    objects, arrows, edges = read_fc_annotation(FC_XML)
    assert [o.cls for o in objects] == ["terminal", "process", "circle", "arrow", "arrow_head"]
    assert objects[1].box == (100.0, 200.0, 300.0, 280.0)
    (arrow,) = arrows
    assert arrow.tail == (200.0, 112.0) and arrow.head == (200.0, 198.0) and arrow.has_head
    # the relation lists the process first, but the arrow starts at the terminator
    assert edges == [(0, 1)]


def test_fc_folders_and_splits(tmp_path: Path) -> None:
    root = tmp_path / "FC_database_offline_1.0"
    for part in ("DA", "DB"):
        for stem in ("writer1_1", "writer2_1"):
            (root / part / "annotation").mkdir(parents=True, exist_ok=True)
            (root / part / "annotation" / f"{stem}.xml").write_text(FC_XML)
            _png(root / part / "images" / f"{stem}.png", 600, 500)
    (root / "test.txt").write_text("writer2_1\n")
    samples = list(read_fc(tmp_path, ImageSize()))
    assert sorted({s.source for s in samples}) == ["fc_scan", "fc_skeleton"]
    assert {s.split for s in samples if Path(s.image).stem == "writer2_1"} == {"test"}
    assert all(s.width == 600 and s.height == 500 and s.edges == ((0, 1),) for s in samples)


def test_voc_annotation_with_derived_heads(tmp_path: Path) -> None:
    filename, width, height, objects, arrows = read_voc_annotation(VOC_XML)
    assert (filename, width, height) == ("fc-001.jpg", 640, 480)
    assert [o.cls for o in objects] == ["terminal", "arrow", "io", "arrow_head"]
    (arrow,) = arrows
    assert arrow.derived and arrow.tail == (60.0, 50.0) and arrow.head == (60.0, 150.0)
    (tmp_path / "train").mkdir()
    (tmp_path / "train" / "fc-001.xml").write_text(VOC_XML)
    _png(tmp_path / "train" / "fc-001.jpg")
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "fc-002.xml").write_text(VOC_XML.replace("fc-001", "fc-002"))
    _png(tmp_path / "validation" / "fc-002.jpg")
    samples = {Path(s.image).stem: s for s in read_voc(tmp_path, ImageSize())}
    assert samples["fc-002"].split == "test"  # Flowchart 3b's validation part is our test
    assert samples["fc-001"].split in ("train", "val") and samples["fc-001"].edges is None


@pytest.mark.parametrize(
    "direction", ["arrow_line_up", "arrow_line_down", "arrow_line_left", "arrow_line_right"]
)
def test_derived_heads_sit_at_the_named_end(direction: str) -> None:
    tail, head, hb = derived_head(
        direction,
        (0.0, 0.0, 20.0, 100.0) if direction[-2:] in ("up", "wn") else (0.0, 0.0, 100.0, 20.0),
    )
    assert hb[0] <= head[0] <= hb[2] and hb[1] <= head[1] <= hb[3]
    assert not (hb[0] <= tail[0] <= hb[2] and hb[1] <= tail[1] <= hb[3])


def test_manifest_round_trip_and_stable_splits(tmp_path: Path) -> None:
    sample = Sample(
        image="images/a.png",
        width=10,
        height=10,
        source="synthetic",
        split="train",
        objects=(LabelledObject(cls="process", box=(1, 1, 5, 5)),),
        arrows=(ArrowTruth(box=(1, 1, 3, 9), tail=(2, 1), head=(2, 9)),),
        edges=((0, 0),),
        extra={"kind": "flowchart"},
    )
    assert from_json(json.loads(json.dumps(to_json(sample)))) == sample
    write_manifest(tmp_path / "m.jsonl", [sample, sample])
    assert list(read_manifest(tmp_path / "m.jsonl")) == [sample, sample]
    assert split_for("abc") == split_for("abc")
    shares = [split_for(f"name{k}") for k in range(2000)]
    assert 0.1 < shares.count("test") / 2000 < 0.2
    with pytest.raises(ValueError, match="class"):
        LabelledObject(cls="hexagon", box=(0, 0, 1, 1))


# --- synthetic drawings ----------------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_synthetic_drawings_are_labelled_consistently(kind: str) -> None:
    image, sample = render(random.Random(5), kind)  # noqa: S311  (test data)
    assert image.shape[:2] == (sample.height, sample.width)
    shapes = sample.shapes
    assert shapes and sample.edges is not None
    assert len(sample.arrows) == len(sample.edges) == sum(o.cls == "arrow" for o in sample.objects)
    for i, j in sample.edges:
        assert 0 <= i < len(shapes) and 0 <= j < len(shapes)
    for a in sample.arrows:  # the arrow's ends lie on (or by) its two shapes' boxes
        assert a.box[0] - 1 <= a.tail[0] <= a.box[2] + 1
    heads = sum(o.cls == "arrow_head" for o in sample.objects)
    assert heads in (0, len(sample.arrows))
    assert all(a.has_head == bool(heads) for a in sample.arrows)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    for s in shapes:  # ink inside every shape box
        x0, y0, x1, y1 = (int(v) for v in s.box)
        assert gray[y0 : y1 + 1, x0 : x1 + 1].min() < 150


def test_synthetic_set_is_reproducible(tmp_path: Path) -> None:
    first = write_synthetic(tmp_path / "a", 6, seed=3)
    second = write_synthetic(tmp_path / "b", 6, seed=3)
    assert list(read_manifest(first)) == list(read_manifest(second))
    assert {s.split for s in read_manifest(first)} <= {"train", "val", "test"}


# --- arrow ends ------------------------------------------------------------------------------


def _drawn_arrow(points: list[tuple[int, int]], head: bool) -> np.ndarray:
    image = np.full((300, 400), 255, np.uint8)
    cv2.polylines(image, [np.array(points, np.int32)], False, 0, 2)
    if head:
        x, y = points[-1]
        cv2.fillPoly(image, [np.array([(x, y), (x - 12, y - 6), (x - 12, y + 6)], np.int32)], 0)
    return image


def test_arrow_ends_of_a_straight_and_a_bent_arrow() -> None:
    straight = _drawn_arrow([(50, 150), (350, 150)], head=True)
    tail, tip = arrow_ends(straight, (45, 140, 355, 160), (335, 140, 355, 160))
    assert abs(tail[0] - 50) <= 5 and abs(tip[0] - 350) <= 5
    bent = _drawn_arrow([(50, 50), (300, 50), (300, 250)], head=False)
    first, second = arrow_ends(bent, (45, 45, 305, 255), None)
    ends = sorted([first, second])
    assert abs(ends[0][0] - 50) <= 4 and abs(ends[1][1] - 250) <= 4


def test_arrow_ends_without_ink_use_the_box() -> None:
    blank = np.full((100, 100), 255, np.uint8)
    tail, tip = arrow_ends(blank, (10, 10, 90, 20), (80, 10, 90, 20))
    assert tail[0] <= 15 and tip == (85.0, 15.0)


def test_head_choice_and_nms() -> None:
    heads = [
        ((80.0, 10.0, 90.0, 20.0), 0.4),
        ((85.0, 12.0, 95.0, 22.0), 0.9),
        ((500.0, 0.0, 510.0, 5.0), 1.0),
    ]
    assert head_for((10.0, 10.0, 90.0, 20.0), heads) == 1
    assert head_for((200.0, 200.0, 210.0, 210.0), heads) is None
    assert nms([(0, 0, 10, 10), (1, 1, 10, 10), (50, 50, 60, 60)], [0.5, 0.9, 0.2], 0.5) == [1, 2]


# --- detector post-processing ----------------------------------------------------------------


def test_letterbox_and_decoding_map_back_to_the_image() -> None:
    image = np.full((200, 400, 3), 255, np.uint8)
    pixels, scale = letterbox(image, 100)
    assert pixels.shape == (3, 100, 100) and scale == 0.25
    assert pixels[:, 75:, :].min() == 1.0  # the pad is white
    logits = np.full((3, len(CLASSES)), -9.0)
    logits[0, CLASSES.index("process")] = 3.0
    logits[1, CLASSES.index("arrow")] = 0.0  # 0.5
    boxes = np.array([[0.5, 0.25, 0.2, 0.1], [0.1, 0.1, 0.1, 0.1], [0.5, 0.5, 0.1, 0.1]])
    found = decode_outputs(logits, boxes, scale=scale, size=100, offset=(10, 20), threshold=0.45)
    assert [d.cls for d in found] == ["process", "arrow"]
    x0, y0, x1, y1 = found[0].box
    assert (round(x0), round(y0), round(x1), round(y1)) == (170, 100, 250, 140)


def test_suppression_keeps_the_best_shape_on_one_spot() -> None:
    found = suppress(
        [
            Detection(cls="process", box=(0, 0, 100, 50), score=0.6),
            Detection(cls="io", box=(2, 1, 100, 50), score=0.8),
            Detection(cls="process", box=(1, 0, 100, 50), score=0.5),
            Detection(cls="arrow", box=(0, 0, 100, 50), score=0.5),
        ]
    )
    assert sorted((d.cls, d.score) for d in found) == [("arrow", 0.5), ("io", 0.8)]


def test_to_detection_builds_arrows_with_ends() -> None:
    image = _drawn_arrow([(50, 150), (350, 150)], head=True)
    detection = to_detection(
        [
            Detection(cls="process", box=(0, 120, 48, 180), score=0.9),
            Detection(cls="arrow", box=(45, 140, 355, 160), score=0.8),
            Detection(cls="arrow_head", box=(335, 140, 355, 160), score=0.7),
        ],
        image,
        400,
        300,
    )
    (shape,) = detection.shapes
    assert shape.shape is NodeShape.PROCESS and shape.confidence == 0.9
    (arrow,) = detection.arrows
    assert arrow.has_head and abs(arrow.tail.x - 50) <= 5 and abs(arrow.head.x - 350) <= 5


# --- benchmark -------------------------------------------------------------------------------


class TruthDetector:
    """Returns the truth of the image it is given (looked up by its size)."""

    def __init__(self, samples: list[Sample], *, empty: bool = False) -> None:
        self._by_size = {(s.width, s.height): s for s in samples}
        self._empty = empty

    def detect(
        self, image: Any, *, offset: tuple[int, int] = (0, 0), threshold: float | None = None
    ) -> list[Detection]:
        if self._empty:
            return []
        s = self._by_size[(image.shape[1], image.shape[0])]
        return [Detection(cls=o.cls, box=o.box, score=0.9) for o in s.objects]


def test_bench_on_a_perfect_and_an_empty_detector(tmp_path: Path) -> None:
    manifest = write_synthetic(tmp_path, 8, seed=9)
    items = [(manifest, s) for s in read_manifest(manifest)]
    samples = [s for _, s in items]
    perfect = evaluate(TruthDetector(samples), items, threshold=0.35)["synthetic"]
    for cls in ("process", "arrow"):
        c = perfect.classes[cls]
        if c.truths:
            assert c.precision == 1.0 and c.recall == 1.0 and c.ap == pytest.approx(1.0)
    assert perfect.arrows_matched == sum(len(s.arrows) for s in samples)
    assert perfect.tails_ok / perfect.arrows_matched > 0.8
    assert perfect.edges_true and perfect.edges_right_any_direction / perfect.edges_true > 0.7
    empty = evaluate(TruthDetector(samples, empty=True), items, threshold=0.35)["synthetic"]
    assert empty.classes["process"].recall in (0.0, None)
    report = render_report(
        {"synthetic": perfect},
        model="shape-detector-v1",
        manifest={
            "training_images": {"synthetic": 8},
            "history": [{"epoch": 1, "peak_gpu_gb": 2.5}],
        },
        threshold=0.35,
        device="cpu",
        peak_gpu_gb=None,
        today=date(2026, 10, 5),
    )
    assert "## synthetic (8 images)" in report and "| process |" in report
    assert "start" not in report  # class names and numbers only, never labels


def test_wiring_without_a_trained_model(tmp_path: Path) -> None:
    setup = build_recognizers(Settings(_env_file=None, model_dir=tmp_path))
    assert setup.recognizers == {} and "tarn diagram train" in (setup.skipped or "")


def test_pack_makes_a_self_contained_bundle(tmp_path: Path) -> None:
    import zipfile

    from tarn_adapters.diagram.pack import pack

    manifest = write_synthetic(tmp_path / "s", 4, seed=2)
    code = tmp_path / "pkg"
    (code / "sub").mkdir(parents=True)
    (code / "sub" / "m.py").write_text("x = 1\n")
    bundle = pack([manifest], tmp_path / "bundle.zip", [code], max_side=640, log=lambda _: None)
    with zipfile.ZipFile(bundle) as z:
        names = set(z.namelist())
        assert "data/manifest.jsonl" in names and "code/pkg/sub/m.py" in names
        z.extractall(tmp_path / "x")
    packed = list(read_manifest(tmp_path / "x" / "data" / "manifest.jsonl"))
    original = list(read_manifest(manifest))
    assert len(packed) == 4 and all(s.extra.get("prepared") for s in packed)
    for p, o in zip(packed, original, strict=True):
        assert max(p.width, p.height) <= 640
        f = p.width / o.width
        assert p.objects[0].box[0] == pytest.approx(o.objects[0].box[0] * f, abs=1.0)
        image = cv2.imread(str(tmp_path / "x" / "data" / p.image))
        assert image is not None and image.shape[1] == p.width


def test_a_bent_arrows_tail_is_the_far_end_of_its_line() -> None:
    # a feedback loop: out of a box to the right, up, and back left into the box above
    image = _drawn_arrow([(192, 180), (240, 180), (240, 60), (196, 60)], head=False)
    x, y = 192, 60
    cv2.fillPoly(image, [np.array([(x, y), (x + 12, y - 6), (x + 12, y + 6)], np.int32)], 0)
    tail, tip = arrow_ends(image, (188, 50, 245, 186), (190, 52, 206, 68))
    assert abs(tail[0] - 192) <= 6 and abs(tail[1] - 180) <= 6
    assert abs(tip[0] - 192) <= 4
