"""Ground-truth adapters: the folder store, the manifest reading of a set, the pre-fill from the
OCR, the local transcription server and the benchmark collector (fake engines, real files)."""

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from tarn_adapters.compute import DeviceInfo
from tarn_adapters.config import Settings
from tarn_adapters.groundtruth.prefill import prefill_file
from tarn_adapters.groundtruth.server import TranscriptionServer
from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore
from tarn_adapters.imaging.testing import jpeg, ruled_page
from tarn_adapters.ocr.batch import read_manifest
from tarn_adapters.ocr.bench import collect
from tarn_adapters.ocr.images import encode_jpeg
from tarn_adapters.ocr.transform import OpenCvPageTransform
from tarn_adapters.ocr.wiring import OcrSetup
from tarn_adapters.ocr.words import FileWordList
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.domain.groundtruth import (
    CaptureType,
    TruthLine,
    TruthPage,
    TruthStatus,
)
from tarn_core.domain.ocr import ContentClass, SelectorSettings
from tarn_core.errors import EngineFailedError, InvariantError, NotFoundError
from tarn_core.ports.engines import DetectedRegion, OcrEngine, TableCell
from tarn_core.services.groundtruth import GroundTruthService
from tarn_core.services.ocr.reader import OrientationPolicy
from tarn_core.testing import ScriptedLayoutDetector, ScriptedOcrEngine

SECRET = "zyxwv unmistakable"
CPU = DeviceInfo("cpu", "cpu", None, "test")
CUDA = DeviceInfo("cuda", "fake gpu", 4096, "test")


def page_image() -> bytes:
    return encode_jpeg(np.full((1400, 1000, 3), 255, np.uint8))


def truth_page(page_id: str = "b-ci2-p04", **changes: object) -> TruthPage:
    fields: dict[str, object] = {
        "id": page_id,
        "capture": CaptureType.CLEAN_SCAN,
        "image": f"{page_id}.jpg",
        "width": 1000,
        "height": 1400,
        "source": "B-CI2",
        "lines": (
            TruthLine(
                box=Box(x0=50, y0=50, x1=500, y1=100),
                text=SECRET,
                content_class=ContentClass.CURSIVE,
                status=TruthStatus.VERIFIED,
                prefill="zyxwv unmistakeable",
            ),
            TruthLine(
                box=Box(x0=50, y0=150, x1=500, y1=200),
                text="to check",
                content_class=ContentClass.PRINT,
                prefill="to check",
            ),
            TruthLine(
                box=Box(x0=50, y0=250, x1=500, y1=300),
                text="",
                content_class=ContentClass.PRINT,
                status=TruthStatus.IGNORED,
            ),
        ),
    }
    fields.update(changes)
    return TruthPage(**fields)  # type: ignore[arg-type]


# --- the folder store ---------------------------------------------------------------------------


def test_the_folder_store_round_trips_a_page_and_its_image(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path / "set")
    assert store.page_ids() == []
    store.save(truth_page(), page_image())
    assert store.page_ids() == ["b-ci2-p04"]
    assert store.get("b-ci2-p04") == truth_page()
    assert store.image("b-ci2-p04") == page_image()
    assert sorted(p.name for p in (tmp_path / "set").iterdir()) == [
        "b-ci2-p04.jpg",
        "b-ci2-p04.jsonl",
    ]


def test_the_folder_store_refuses_what_it_cannot_find_or_trust(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path)
    with pytest.raises(NotFoundError):
        store.get("missing")
    with pytest.raises(NotFoundError):
        store.get("../etc/passwd")
    with pytest.raises(InvariantError, match="needs its image"):
        store.save(truth_page())
    store.save(truth_page(), page_image())
    (tmp_path / "b-ci2-p04.jsonl").rename(tmp_path / "other.jsonl")
    with pytest.raises(InvariantError, match="holds page"):
        store.get("other")


def test_a_second_save_keeps_the_image_and_replaces_the_lines(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path)
    store.save(truth_page(), page_image())
    store.save(truth_page(lines=(truth_page().lines[1],)))
    assert len(store.get("b-ci2-p04").lines) == 1
    assert store.image("b-ci2-p04") == page_image()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


def test_a_set_is_a_calibration_manifest_of_its_verified_lines(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path)
    store.save(truth_page(), page_image())
    store.save(truth_page("b-ci2-p05", image="b-ci2-p05.jpg"), page_image())
    lines = read_manifest(tmp_path)
    assert len(lines) == 2  # one verified line per page: pre-filled and ignored ones are no truth
    assert {ln.text for ln in lines} == {SECRET}
    assert all(ln.content_class is ContentClass.CURSIVE and ln.box is not None for ln in lines)
    assert all(ln.image.parent == tmp_path.resolve() for ln in lines)
    single = read_manifest(tmp_path / "b-ci2-p04.jsonl")
    assert len(single) == 1
    with pytest.raises(InvariantError, match="manifest line"):
        (tmp_path / "bad.jsonl").write_text("[1]\n")
        read_manifest(tmp_path)


# --- pre-fill ---------------------------------------------------------------------------------


def make_setup(
    engines: dict[str, OcrEngine],
    regions: list[DetectedRegion] | None = None,
    device: DeviceInfo = CPU,
) -> OcrSetup:
    box = Box(x0=60, y0=60, x1=500, y1=110)
    return OcrSetup(
        settings=SelectorSettings(),
        layout=ScriptedLayoutDetector(
            regions
            if regions is not None
            else [
                DetectedRegion(kind=RegionKind.TEXT_LINE, box=box),
                DetectedRegion(kind=RegionKind.TEXT_LINE, box=Box(x0=60, y0=160, x1=500, y1=210)),
            ]
        ),
        engines=engines,
        transform=OpenCvPageTransform(),
        word_list=FileWordList(frozenset({"the", "court"})),
        orientation=OrientationPolicy(enabled=False),
        device=device,
    )


def test_prefill_stores_cleaned_pages_with_the_selectors_reading(tmp_path: Path) -> None:
    source = tmp_path / "booklet.jpg"
    source.write_bytes(jpeg(ruled_page(1000, 1400, seed=3)))
    setup = make_setup(
        {
            "trocr": ScriptedOcrEngine("trocr", ["the court"], 0.9),
            "paddle": ScriptedOcrEngine("paddle", ["the cart"], 0.5),
        }
    )
    store = DirectoryGroundTruthStore(tmp_path / "set")
    service = GroundTruthService(store)
    made = list(
        prefill_file(
            source, setup, service, label="b-x", capture=CaptureType.PHONE_PHOTO, pages=[1]
        )
    )
    assert [p.id for p in made] == ["b-x-p01"]
    stored = store.get("b-x-p01")
    assert stored.capture is CaptureType.PHONE_PHOTO
    assert stored.source == "b-x"
    assert [ln.text for ln in stored.lines] == ["the court", "the court"]
    assert all(ln.status is TruthStatus.PREFILLED and ln.prefill == ln.text for ln in stored.lines)
    assert store.image("b-x-p01").startswith(b"\xff\xd8")  # the cleaned page, as read
    assert service.progress().verified == 0


def test_prefill_cells_of_a_table_are_lines_and_a_second_run_keeps_the_work(tmp_path: Path) -> None:
    source = tmp_path / "booklet.jpg"
    source.write_bytes(jpeg(ruled_page(1000, 1400, seed=3)))
    table = DetectedRegion(
        kind=RegionKind.TABLE,
        box=Box(x0=40, y0=40, x1=900, y1=400),
        cells=(
            TableCell(row=0, col=0, box=Box(x0=60, y0=60, x1=400, y1=110)),
            TableCell(row=1, col=0, box=Box(x0=60, y0=160, x1=400, y1=210)),
        ),
    )
    setup = make_setup({"paddle": ScriptedOcrEngine("paddle", ["cell"], 0.8)}, [table])
    store = DirectoryGroundTruthStore(tmp_path / "set")
    service = GroundTruthService(store)
    kwargs = {"label": "b-x", "capture": CaptureType.CLEAN_SCAN, "pages": [1]}
    assert len(next(prefill_file(source, setup, service, **kwargs)).lines) == 2  # type: ignore[arg-type]
    with pytest.raises(InvariantError, match="already"):
        list(prefill_file(source, setup, service, **kwargs))  # type: ignore[arg-type]
    service.record_correction(
        page_id="b-x-p01",
        box=Box(x0=60, y0=60, x1=400, y1=110),
        text="by a person",
        content_class=ContentClass.CURSIVE,
    )
    with pytest.raises(InvariantError, match="not overwritten"):
        list(prefill_file(source, setup, service, replace=True, **kwargs))  # type: ignore[arg-type]


# --- the transcription server -----------------------------------------------------------------


@pytest.fixture
def server(tmp_path: Path) -> Iterator[TranscriptionServer]:
    store = DirectoryGroundTruthStore(tmp_path)
    store.save(truth_page(), page_image())
    app = TranscriptionServer(store)
    app.start()
    yield app
    app.stop()


def call(
    app: TranscriptionServer,
    path: str,
    *,
    method: str = "GET",
    body: object = None,
    token: str | None = None,
    host: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"X-Tarn-Token": app.token if token is None else token}
    if host is not None:
        headers["Host"] = host
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{app.port}{path}", data, headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


def test_the_server_binds_to_loopback_and_every_request_needs_the_token(
    server: TranscriptionServer,
) -> None:
    assert server.url.startswith("http://127.0.0.1:") and "?t=" in server.url
    assert server._httpd.server_address[0] == "127.0.0.1"
    assert call(server, "/api/pages", token="wrong")[0] == 403
    assert call(server, "/api/pages", token="")[0] == 403
    assert call(server, f"/?t={server.token}", token="")[0] == 200  # the address carries it
    assert call(server, "/api/pages")[0] == 200


def test_the_server_refuses_foreign_host_names(server: TranscriptionServer) -> None:
    assert call(server, "/api/pages", host="evil.example")[0] == 403
    assert call(server, "/api/pages", host=f"localhost:{server.port}")[0] == 200


def test_the_page_is_served_with_a_strict_policy_and_the_api_lists_and_reads(
    server: TranscriptionServer,
) -> None:
    status, headers, body = call(server, "/")
    assert status == 200 and b"Ground-truth transcription" in body
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"
    _, _, listing = call(server, "/api/pages")
    [entry] = json.loads(listing)
    assert (entry["id"], entry["verified"], entry["prefilled"], entry["ignored"]) == (
        "b-ci2-p04",
        1,
        1,
        1,
    )
    _, _, detail = call(server, "/api/pages/b-ci2-p04")
    page = json.loads(detail)
    assert (page["width"], page["height"]) == (1000, 1400)
    assert page["lines"][0]["prefill"] == "zyxwv unmistakeable"
    status, headers, image = call(server, "/api/pages/b-ci2-p04/image")
    assert (status, headers["Content-Type"], image) == (200, "image/jpeg", page_image())
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_saving_lines_stores_them_and_keeps_the_prefill(server: TranscriptionServer) -> None:
    _, _, detail = call(server, "/api/pages/b-ci2-p04")
    lines = json.loads(detail)["lines"]
    lines[1]["text"] = "checked text"
    lines[1]["status"] = "verified"
    lines.append(
        {
            "box": [100, 400, 600, 450],
            "text": "added",
            "class": "numeric",
            "status": "verified",
            "origin": "transcription",
            "prefill": None,
            "region_id": None,
        }
    )
    status, _, saved = call(server, "/api/pages/b-ci2-p04", method="PUT", body={"lines": lines})
    assert status == 200 and json.loads(saved)["verified"] == 3
    stored = server._store.get("b-ci2-p04")
    assert [ln.text for ln in stored.lines] == [SECRET, "checked text", "", "added"]
    assert stored.lines[1].prefill == "to check"
    assert stored.lines[3].prefill is None


def test_bad_saves_are_refused_without_echoing_the_text(server: TranscriptionServer) -> None:
    bad = [
        {"box": [0, 0, 5000, 20], "text": SECRET, "class": "print", "status": "verified"},
    ]
    status, _, body = call(server, "/api/pages/b-ci2-p04", method="PUT", body={"lines": bad})
    assert status == 400 and SECRET.encode() not in body
    assert call(server, "/api/pages/b-ci2-p04", method="PUT", body={"lines": []})[0] == 400
    assert call(server, "/api/pages/b-ci2-p04", method="PUT", body=[1])[0] == 400
    unchanged = server._store.get("b-ci2-p04")
    assert len(unchanged.lines) == 3
    assert call(server, "/api/pages/nope")[0] == 404
    assert (
        call(
            server,
            "/api/pages/nope",
            method="PUT",
            body={"lines": [{"box": [0, 0, 5, 5], "text": "x", "class": "print"}]},
        )[0]
        == 404
    )
    assert call(server, "/api/other")[0] == 404
    assert call(server, "/api/pages/b-ci2-p04", method="POST", body={})[0] in (404, 405, 501)


# --- the collector ----------------------------------------------------------------------------


def verified_page(page_id: str, text: str) -> TruthPage:
    return truth_page(
        page_id,
        image=f"{page_id}.jpg",
        lines=(
            TruthLine(
                box=Box(x0=60, y0=60, x1=500, y1=110),
                text=text,
                content_class=ContentClass.CURSIVE,
                status=TruthStatus.VERIFIED,
            ),
            TruthLine(
                box=Box(x0=60, y0=160, x1=500, y1=210),
                text="pending",
                content_class=ContentClass.PRINT,
                status=TruthStatus.PREFILLED,
                prefill="pending",
            ),
            TruthLine(
                box=Box(x0=60, y0=900, x1=500, y1=950),
                text="",
                content_class=ContentClass.PRINT,
                status=TruthStatus.IGNORED,
            ),
        ),
    )


def test_collect_reads_every_engine_on_the_truth_boxes_and_times_it(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path)
    for name in ("p1", "p2"):
        store.save(verified_page(name, "the court"), page_image())
    good = ScriptedOcrEngine("trocr", ["the court", "pending"], 0.9, version="m1")
    bad = ScriptedOcrEngine("paddle", ["the cart", "pending"], 0.6, version="p1")
    broken = ScriptedOcrEngine("tesseract", ["x"], fail=EngineFailedError("no"), version="t1")
    setup = make_setup({"trocr": good, "paddle": bad, "tesseract": broken})
    run = collect(setup, store, settings=Settings(), date="2026-10-04", label="unit")

    assert run.engines == {"trocr": "m1", "paddle": "p1", "tesseract": "t1"}
    assert len(run.lines) == 4  # two lines a page: the ignored box is not read as truth
    first = run.lines[0]
    # configured order (the print set comes first); the failed engine has no reading
    assert [r.engine.name for r in first.readings] == ["paddle", "trocr"]
    assert (first.truth, first.verified) == ("the court", True)
    assert [ln.verified for ln in run.lines] == [True, False, True, False]
    assert all(s.failures == ("tesseract:error",) for s in run.pages)
    stats = run.pages[0]
    assert (stats.lines, stats.detected, stats.matched, stats.tables) == (2, 2, 2, 0)
    [timing] = run.timings
    assert timing.pages == 2 and timing.device == "cpu"
    assert set(timing.engine_seconds) == {"trocr", "paddle", "tesseract"}
    assert run.label == "unit" and run.config["TARN_TROCR_MODEL"]
    assert good.calls >= 3  # a warm-up read, then each page once


def test_collect_counts_detection_coverage_and_lines_inside_tables(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path)
    store.save(verified_page("p1", "the court"), page_image())
    table = DetectedRegion(
        kind=RegionKind.TABLE,
        box=Box(x0=40, y0=40, x1=900, y1=400),
        cells=(TableCell(row=0, col=0, box=Box(x0=60, y0=60, x1=500, y1=110)),),
    )
    setup = make_setup({"trocr": ScriptedOcrEngine("trocr", ["a"], 0.9)}, [table])
    run = collect(setup, store, settings=Settings(), date="d")
    stats = run.pages[0]
    assert (stats.tables, stats.detected, stats.matched, stats.lines_in_tables) == (1, 1, 1, 2)


def test_collect_times_the_cpu_too_only_when_the_run_was_not_on_the_cpu(tmp_path: Path) -> None:
    store = DirectoryGroundTruthStore(tmp_path)
    for name in ("p1", "p2", "p3"):
        store.save(verified_page(name, "the court"), page_image())
    gpu = make_setup({"trocr": ScriptedOcrEngine("trocr", ["a"], 0.9)}, device=CUDA)
    built: list[int] = []

    def cpu() -> OcrSetup:
        built.append(1)
        return make_setup({"trocr": ScriptedOcrEngine("trocr", ["a"], 0.9)})

    run = collect(gpu, store, settings=Settings(), date="d", cpu_setup=cpu, cpu_pages=2)
    assert [t.device for t in run.timings] == ["cuda (fake gpu)", "cpu (forced)"]
    assert run.timings[1].pages == 2
    on_cpu = collect(
        make_setup({"trocr": ScriptedOcrEngine("trocr", ["a"], 0.9)}),
        store,
        settings=Settings(),
        date="d",
        cpu_setup=cpu,
    )
    assert len(on_cpu.timings) == 1 and built == [1]
