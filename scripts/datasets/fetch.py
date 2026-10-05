#!/usr/bin/env python3
"""Download public datasets for OCR and diagram tests (P11) and for scoring calibration (P13).

Standard library only. Nothing is downloaded by default and nothing is committed: files go to
``var/datasets/<name>/`` (git-ignored). The sources are those of docs/requirements.md "Public
handwritten material for development"; Tarn agreed to use them internally for testing (C20, Q11),
which is not a licence to redistribute them.

Rules this script keeps:
* **No crawling.** It fetches only the URLs you give it (one file or archive each), with a
  descriptive User-Agent, so each site's terms and robots rules stay yours to read.
* **You accept the terms.** ``fetch`` refuses until you pass ``--accept-terms`` after reading the
  note ``info`` prints. Where a page states no licence, the note says so: ask the owner.
* **Provenance.** Every file is recorded in ``var/datasets/<name>/PROVENANCE.jsonl`` (URL, date,
  size, SHA-256, the terms note), so any later publication can be checked against its source.
* **Safe.** HTTPS only (plain HTTP only for 127.0.0.1), a size limit, archives extracted without
  absolute paths or ``..``.

    python scripts/datasets/fetch.py list
    python scripts/datasets/fetch.py info fc-offline
    python scripts/datasets/fetch.py fetch fc-offline --accept-terms --url https://…/DA.zip
    python scripts/datasets/fetch.py fetch flowchart-3b --accept-terms      # needs the kaggle CLI
"""

# ruff: noqa: T201
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

USER_AGENT = "tarn-dataset-fetch/1.0 (internal OCR testing; one URL at a time)"
MAX_BYTES = 2 * 1024**3
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "var" / "datasets"


@dataclass(frozen=True)
class Source:
    name: str
    title: str
    landing: str
    holds: str
    use: str
    terms: str
    mode: str
    """``url`` (you give the direct link), ``kaggle`` (the kaggle CLI), ``manual`` (pick single
    files on the site by hand; ``--url`` then takes one file you chose)."""
    kaggle: str = ""


SOURCES: dict[str, Source] = {
    s.name: s
    for s in (
        Source(
            "fc-offline",
            "FC database, offline extension (CTU Prague): FC_A skeleton images, FC_B scans",
            "https://cmp.felk.cvut.cz/~breslmar/flowcharts_offline/",
            "Hand-drawn flowcharts as PNG, with XML annotation and InkML strokes (folders DA, DB).",
            "Benchmark for flowchart recognition (P14).",
            "Described as freely available for research; the page's licence text was not "
            "checked by Tarn (the server was unreachable when this script was written). Read "
            "the page, cite the authors (Bresler et al.), and use it for testing only.",
            "url",
        ),
        Source(
            "fc",
            "FC database, online version (CTU Prague): FC_A strokes",
            "https://cmp.felk.cvut.cz/~breslmar/flowcharts/",
            "Online (stroke) flowcharts by 35 writers.",
            "Reference for FC_A; the offline extension above is the image form.",
            "As for fc-offline: read the page, cite the authors, test use only.",
            "url",
        ),
        Source(
            "flowchart-3b",
            "Kaggle 'Flowchart 3b' (davbetm)",
            "https://www.kaggle.com/datasets/davbetm/flowchart-3b",
            "Photos of hand-drawn flowchart shapes and full flowcharts by engineering students.",
            "Phone-photo flowcharts, close to our capture (P14).",
            "The licence is set on the Kaggle page and was not readable by Tarn (requirements: "
            "'check the licence'). Open the page, read the licence and the competition/dataset "
            "rules, and accept only if internal testing is allowed.",
            "kaggle",
            kaggle="davbetm/flowchart-3b",
        ),
        Source(
            "madeeasy-ese",
            "MADE EASY ESE mains test-series topper sheets",
            "https://www.madeeasy.in/ese-2020-mains-test-series-question-answer",
            "Scans of real candidates' handwritten answers across engineering branches.",
            "Closest to engineering booklets: text, derivations, diagrams (OCR, segmentation).",
            "Copyrighted material of MADE EASY and its candidates. Tarn may use it internally "
            "for testing (requirements Q11); do not redistribute, publish or commit it.",
            "manual",
        ),
        Source(
            "cbse-model-answers",
            "CBSE model answers by candidates (Class 10/12 scanned answer books)",
            "https://www.cbse.gov.in/",
            "Official scanned answer books incl. Physics, Chemistry, Maths.",
            "Diagrams, formulas and tables inside a real exam booklet layout.",
            "Published by CBSE; the pages' terms were not checked by Tarn. Internal testing "
            "only; do not redistribute; school level, not engineering.",
            "manual",
        ),
        Source(
            "unlockias-upsc",
            "UnlockIAS index of UPSC mains answer copies",
            "https://www.unlockias.in/upsc-toppers-answer-copy",
            "640+ scanned booklets with flowcharts, tables and maps.",
            "Flowcharts, tables, long descriptive answers (OCR, segmentation).",
            "Copyrighted scripts of UPSC candidates, indexed by a third party. Internal "
            "testing only (requirements Q11); do not redistribute or commit.",
            "manual",
        ),
        Source(
            "mohler",
            "Texas short-answer grading data v2.0 (Mohler, Bunescu and Mihalcea, ACL 2011)",
            "https://web.eecs.umich.edu/~mihalcea/downloads.html",
            "80 data-structures questions with a reference answer each and about 2,270 student "
            "answers, every one marked 0-5 by two human graders (typed text, not handwriting).",
            "Choosing the scoring embedding model and fitting the credit bands (P13): human "
            "marks against a reference answer, the shape of Tarn's semantic criteria.",
            "Distributed by the authors for research; the page's licence text was not checked "
            "by Tarn. Read the page, cite the paper, use it internally for testing only, do not "
            "redistribute or commit it. Give the archive's URL with --url.",
            "url",
        ),
        Source(
            "scientsbank",
            "SemEval-2013 Task 7 (Student Response Analysis): SciEntsBank and Beetle",
            "https://aclanthology.org/S13-2045/",
            "Science and electronics questions with reference answers and student answers "
            "labelled correct, partially correct, contradictory, irrelevant or non-domain.",
            "A second check of the scoring model and of the off-target guard (P13): labels map "
            "to credit 1, 1/2, 0 and 'irrelevant'/'non-domain' to off-target.",
            "Released for the shared task; the licence was not checked by Tarn. Find the data "
            "release from the task paper, read its terms, internal testing only, do not "
            "redistribute or commit it. Give the archive's URL with --url.",
            "url",
        ),
    )
}


class RefusedError(Exception):
    """A rule of this script, not a failure of the network."""


def check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    local = parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise RefusedError(f"only https URLs are fetched: {url}")
    if not parts.hostname:
        raise RefusedError(f"not a URL: {url}")


def file_name(url: str) -> str:
    name = Path(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name).lstrip(".")
    return name or "download"


def safe_extract(archive: Path, target: Path) -> list[Path]:
    """Unpack a zip or tar archive; refuses absolute paths, '..' and links that leave ``target``."""
    target.mkdir(parents=True, exist_ok=True)
    base = target.resolve()
    written: list[Path] = []

    def destination(member: str) -> Path:
        path = (base / member).resolve()
        if not path.is_relative_to(base):
            raise RefusedError(f"{archive.name}: unsafe path in archive")
        return path

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                path = destination(info.filename)
                if info.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, path.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                written.append(path)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                path = destination(member.name)
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    source = tf.extractfile(member)
                    if source is not None:
                        with source, path.open("wb") as dst:
                            shutil.copyfileobj(source, dst)
                        written.append(path)
                else:
                    raise RefusedError(f"{archive.name}: links and devices are not extracted")
    return written


def download(url: str, destination: Path) -> tuple[int, str]:
    """Stream ``url`` to ``destination``; returns its size and SHA-256."""
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    digest, size = hashlib.sha256(), 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as out:  # noqa: S310
        while chunk := response.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                raise RefusedError(f"{url} is larger than {MAX_BYTES >> 20} MB")
            digest.update(chunk)
            out.write(chunk)
    return size, digest.hexdigest()


def record(folder: Path, source: Source, entry: dict[str, object]) -> None:
    entry = {
        "source": source.name,
        "landing": source.landing,
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "terms_note": source.terms,
        "terms_accepted_by_operator": True,
        **entry,
    }
    with (folder / "PROVENANCE.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")


def fetch(source: Source, urls: list[str], out: Path, *, accept_terms: bool, extract: bool) -> int:
    if not accept_terms:
        raise RefusedError(
            f"read the terms first (`info {source.name}`), then repeat with --accept-terms"
        )
    folder = out / source.name
    folder.mkdir(parents=True, exist_ok=True)
    if source.mode == "kaggle":
        exe = shutil.which("kaggle")
        if exe is None:
            raise RefusedError("the kaggle CLI is not installed (pip install kaggle; API token)")
        subprocess.run(  # noqa: S603
            [exe, "datasets", "download", "-d", source.kaggle, "-p", str(folder), "--unzip"],
            check=True,
        )
        record(folder, source, {"kaggle": source.kaggle})
        print(f"{source.name}: downloaded into {folder}")
        return 0
    if not urls:
        print(
            f"{source.name}: no URL given. Open {source.landing} , read its terms, and "
            + (
                "pass the direct link of the archive with --url."
                if source.mode == "url"
                else "download single files by hand into the folder, or pass the direct link "
                "of one file you chose with --url."
            )
        )
        print(f"folder: {folder}")
        return 2
    for url in urls:
        name = file_name(url)
        size, sha = download(url, folder / name)
        entry: dict[str, object] = {"url": url, "file": name, "bytes": size, "sha256": sha}
        if extract and (zipfile.is_zipfile(folder / name) or tarfile.is_tarfile(folder / name)):
            files = safe_extract(folder / name, folder / Path(name).stem)
            entry["extracted_files"] = len(files)
        record(folder, source, entry)
        print(f"{name}: {size} bytes, sha256 {sha[:16]}…")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="default: var/datasets")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    info = sub.add_parser("info")
    info.add_argument("name", choices=SOURCES)
    get = sub.add_parser("fetch")
    get.add_argument("name", choices=SOURCES)
    get.add_argument("--url", action="append", default=[], help="direct link (repeatable)")
    get.add_argument("--accept-terms", action="store_true")
    get.add_argument("--no-extract", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            for s in SOURCES.values():
                print(f"{s.name:<20} [{s.mode:<6}] {s.title}")
            return 0
        source = SOURCES[args.name]
        if args.command == "info":
            for label, value in (
                ("title", source.title),
                ("page", source.landing),
                ("holds", source.holds),
                ("use", source.use),
                ("terms", source.terms),
                ("how", source.mode),
            ):
                print(f"{label:<6}: {value}")
            return 0
        return fetch(
            source, args.url, args.out, accept_terms=args.accept_terms, extract=not args.no_extract
        )
    except RefusedError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
