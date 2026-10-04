"""Segmentation adapters (P12): the embedder chosen from the settings, the OCR cache of a
booklet, and the benchmark folder (discovery, segmenting a cached booklet against a seeded
paper). Synthetic data only."""

import json
from pathlib import Path

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.embed import wiring
from tarn_adapters.embed.sentence import SentenceTransformerEmbedder
from tarn_adapters.segmentation import ocrcache
from tarn_adapters.segmentation.bench import discover, page_inputs, seeded_papers, segment
from tarn_adapters.segmentation.ocrcache import CachedPage, CachedRegion
from tarn_core.domain.booklet import RegionKind
from tarn_core.domain.common import Box
from tarn_core.services.segmentation.segmenter import SegmentationPolicy
from tarn_core.services.segmentation.similarity import TrigramEmbedder


def test_trigram_when_asked_or_when_the_library_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = wiring.build_embedder(Settings(embedding_model="trigram"))
    assert isinstance(chosen.embedder, TrigramEmbedder) and chosen.fallback_reason is None
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    missing = wiring.build_embedder(Settings())
    assert isinstance(missing.embedder, TrigramEmbedder)
    assert missing.fallback_reason == "sentence-transformers is not installed"


def test_the_configured_model_loads_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda name: object())
    chosen = wiring.build_embedder(Settings(embedding_model="org/some-model"))
    assert isinstance(chosen.embedder, SentenceTransformerEmbedder)
    assert chosen.fallback_reason is None  # nothing loaded yet


def _cache(folder: Path, source: Path) -> None:
    lines = [
        ("1. freedom of speech and expression", 70),
        ("freedom of assembly and to move anywhere", 260),
        ("2. national, state and financial emergency", 70),
        ("three types of emergency in the constitution", 260),
    ]
    document = {
        "schema": ocrcache.SCHEMA,
        "sha256": ocrcache.sha256_of(source),
        "pages": [
            {
                "index": 0,
                "width": 1400,
                "height": 2200,
                "rotation_degrees": 0,
                "regions": [
                    {
                        "kind": "text_line",
                        "box": [x, 150 + 70 * k, 1100, 200 + 70 * k],
                        "text": text,
                        "flagged": False,
                        "parent": None,
                        "row": None,
                        "col": None,
                    }
                    for k, (text, x) in enumerate(lines)
                ],
            }
        ],
    }
    (folder / "ocr.json").write_text(json.dumps(document))


def test_cache_round_trip_and_staleness(tmp_path: Path) -> None:
    source = tmp_path / "booklet.pdf"
    source.write_bytes(b"%PDF synthetic")
    _cache(tmp_path, source)
    pages = ocrcache.load(tmp_path, source)
    assert pages is not None and len(pages[0].regions) == 4
    assert pages[0].regions[0] == CachedRegion(
        kind=RegionKind.TEXT_LINE,
        box=Box(x0=70, y0=150, x1=1100, y1=200),
        text="1. freedom of speech and expression",
        flagged=False,
    )
    source.write_bytes(b"%PDF another file")
    assert ocrcache.load(tmp_path, source) is None  # the cache is of another file
    assert ocrcache.load(tmp_path / "nothing") is None


def test_benchmark_folder_segments_a_cached_booklet(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "samples").mkdir(parents=True)
    (repo / "samples" / "b.pdf").write_bytes(b"%PDF synthetic")
    root = tmp_path / "bench"
    folder = root / "b-one"
    folder.mkdir(parents=True)
    (folder / "booklet.json").write_text(json.dumps({"source": "samples/b.pdf", "paper": "QP-CI"}))
    (folder / "truth.json").write_text(
        json.dumps({"pages": [{"page": 1, "continues": None, "starts": ["1", "2"]}]})
    )
    (root / "b-two").mkdir()
    (root / "b-two" / "booklet.json").write_text(
        json.dumps({"source": "samples/none.pdf", "paper": None})
    )
    _cache(folder, repo / "samples" / "b.pdf")
    booklets = discover(root, repo)
    assert [b.label for b in booklets] == ["b-one", "b-two"]
    assert booklets[0].truth is not None and booklets[1].paper is None
    pages = ocrcache.load(folder)
    assert pages is not None
    papers = seeded_papers()
    run = segment(booklets[0], pages, papers, TrigramEmbedder(), SegmentationPolicy())
    assert run is not None
    score = run.score()
    assert score is not None and score.order_exact and score.recall == 1.0
    assert segment(booklets[1], pages, papers, TrigramEmbedder(), SegmentationPolicy()) is None


def test_page_inputs_keep_table_cells_under_their_table() -> None:
    page = CachedPage(
        0,
        1400,
        2200,
        0,
        (
            CachedRegion(RegionKind.TABLE, Box(x0=0, y0=0, x1=1400, y1=2200), None, False),
            CachedRegion(
                RegionKind.TEXT_LINE, Box(x0=70, y0=100, x1=900, y1=150), "1. x", False, 0, 0, 0
            ),
        ),
    )
    (made,) = page_inputs("b", [page])
    table, cell = made.regions
    assert cell.parent_id == table.id and (cell.row, cell.col) == (0, 0)
