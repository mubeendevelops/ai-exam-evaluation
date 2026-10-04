"""The public-dataset downloader: it refuses what it must, extracts safely and records where
every file came from. Served from a local HTTP server; nothing leaves the machine."""

import functools
import http.server
import importlib.util
import io
import json
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "datasets" / "fetch.py"


@pytest.fixture(scope="module")
def fetch():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("dataset_fetch", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["dataset_fetch"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "www"
    root.mkdir()
    good = io.BytesIO()
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("DA/images/a.png", b"png")
    (root / "ok.zip").write_bytes(good.getvalue())
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escape.txt", b"x")
    (root / "bad.zip").write_bytes(bad.getvalue())
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_every_source_states_its_terms_and_how_to_get_it(fetch) -> None:  # type: ignore[no-untyped-def]
    assert {
        "fc-offline",
        "flowchart-3b",
        "madeeasy-ese",
        "cbse-model-answers",
        "unlockias-upsc",
    } <= set(fetch.SOURCES)
    for source in fetch.SOURCES.values():
        assert source.terms and source.landing.startswith("https://")
        assert source.mode in ("url", "kaggle", "manual")


def test_nothing_is_fetched_without_accepting_the_terms(fetch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(fetch.RefusedError, match="--accept-terms"):
        fetch.fetch(fetch.SOURCES["fc-offline"], [], tmp_path, accept_terms=False, extract=True)
    assert not (tmp_path / "fc-offline" / "PROVENANCE.jsonl").exists()


def test_plain_http_to_other_hosts_is_refused(fetch) -> None:  # type: ignore[no-untyped-def]
    for url in (
        "http://example.com/a.zip",
        "ftp://example.com/a",
        "file:///etc/passwd",
        "https:///x",
    ):
        with pytest.raises(fetch.RefusedError):
            fetch.check_url(url)
    fetch.check_url("https://example.com/a.zip")
    fetch.check_url("http://127.0.0.1:8000/a.zip")


def test_a_manual_source_without_a_url_only_points_at_the_page(
    fetch: Any,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = fetch.fetch(fetch.SOURCES["madeeasy-ese"], [], tmp_path, accept_terms=True, extract=True)
    assert code == 2
    assert "madeeasy.in" in capsys.readouterr().out


def test_a_download_is_extracted_and_recorded(fetch, server: str, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    source = fetch.SOURCES["fc-offline"]
    assert fetch.fetch(source, [f"{server}/ok.zip"], tmp_path, accept_terms=True, extract=True) == 0
    assert (tmp_path / "fc-offline" / "ok" / "DA" / "images" / "a.png").read_bytes() == b"png"
    [entry] = [
        json.loads(x)
        for x in (tmp_path / "fc-offline" / "PROVENANCE.jsonl").read_text().splitlines()
    ]
    assert entry["url"].endswith("/ok.zip") and len(entry["sha256"]) == 64
    assert entry["extracted_files"] == 1 and entry["terms_accepted_by_operator"] is True


def test_an_archive_that_leaves_its_folder_is_refused(fetch, server: str, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(fetch.RefusedError, match="unsafe path"):
        fetch.fetch(
            fetch.SOURCES["fc-offline"],
            [f"{server}/bad.zip"],
            tmp_path,
            accept_terms=True,
            extract=True,
        )
    assert not (tmp_path / "escape.txt").exists()


def test_file_names_are_made_safe(fetch) -> None:  # type: ignore[no-untyped-def]
    assert fetch.file_name("https://x.org/a/b/My%20File(1).pdf?x=1") == "My_File_1_.pdf"
    assert fetch.file_name("https://x.org/") == "download"
    assert fetch.file_name("https://x.org/..%2F..%2Fpasswd") == "passwd"
