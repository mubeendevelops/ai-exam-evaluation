"""The segmentation benchmark over sample booklets (``tarn bench segment``).

A benchmark folder holds one sub-folder per booklet (student data: keep it under the
git-ignored ``var/``):

- ``booklet.json``: ``{"source": "<PDF, relative to the repository>", "paper": "QP-CI"}``;
  ``paper`` is the start of a seeded blueprint's title (``QP-CI``, ``QP-IPR``,
  ``Assignment 1``…), or null for a booklet of no known paper (segmented, not scored);
- ``truth.json`` (optional): a person's labelling (``services.segmentation.benchmark``):
  ``{"pages": [{"page": 1, "continues": null, "starts": ["1", "2"]}, …],
  "written_page_numbers": [1, 2, …] (optional)}``;
- ``ocr.json`` and ``pages/``: the OCR, written on the first run (``ocrcache``).

The papers come from the development seed run in memory, so no stack is needed."""

import json
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from tarn_adapters.ocr.wiring import OcrSetup
from tarn_adapters.segmentation.ocrcache import CachedPage, load, read_into
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Region
from tarn_core.ids import CollegeId, PageId, RegionId
from tarn_core.ports.engines import Embedder
from tarn_core.services.ocr.reader import exam_lexicon
from tarn_core.services.segmentation.benchmark import (
    BookletRun,
    BookletTruth,
    TruthPage,
)
from tarn_core.services.segmentation.lines import PageInput
from tarn_core.services.segmentation.segmenter import SegmentationPolicy, Segmenter
from tarn_core.services.segmentation.service import question_texts
from tarn_core.testing import InMemory
from tarn_core.testing.seed_world import seed_in_memory

_COLLEGE = CollegeId(uuid.UUID(int=1))


@dataclass(frozen=True, slots=True)
class Booklet:
    label: str
    folder: Path
    source: Path
    paper: str | None
    truth: BookletTruth | None
    labelled_by: str | None = None
    """Who made the truth (``labelled_by`` in ``truth.json``)."""


@dataclass(frozen=True, slots=True)
class Papers:
    blueprints: dict[str, ExamBlueprint]
    texts: dict[str, dict[str, str]]
    memory: InMemory

    def find(self, paper: str) -> ExamBlueprint:
        found = [b for title, b in self.blueprints.items() if title.startswith(paper)]
        if len(found) != 1:
            raise LookupError(f"no single seeded paper starts with {paper!r}")
        return found[0]


def seeded_papers() -> Papers:
    memory = InMemory()
    seed_in_memory(memory)
    blueprints = {b.title: b for b in memory.content.latest(ExamBlueprint)}
    texts = {t: question_texts(memory.content, b) for t, b in blueprints.items()}
    return Papers(blueprints, texts, memory)


def _truth(label: str, path: Path) -> BookletTruth | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    numbers = data.get("written_page_numbers")
    return BookletTruth(
        booklet=label,
        paper=data.get("paper") or "",
        pages=tuple(
            TruthPage(page=p["page"], continues=p["continues"], starts=tuple(p["starts"]))
            for p in data["pages"]
        ),
        written_numbers=None if numbers is None else tuple(numbers),
    )


def _labeller(path: Path) -> str | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8")).get("labelled_by")
    return str(value) if value else None


def discover(root: Path, repo: Path) -> list[Booklet]:
    booklets = []
    for folder in sorted(p for p in root.iterdir() if (p / "booklet.json").exists()):
        meta = json.loads((folder / "booklet.json").read_text(encoding="utf-8"))
        booklets.append(
            Booklet(
                label=folder.name,
                folder=folder,
                source=repo / meta["source"],
                paper=meta.get("paper"),
                truth=_truth(folder.name, folder / "truth.json"),
                labelled_by=_labeller(folder / "truth.json"),
            )
        )
    return booklets


def ensure_ocr(
    booklet: Booklet,
    papers: Papers,
    setup: Callable[[], OcrSetup],
    progress: Callable[[str], None],
) -> list[CachedPage]:
    """The cached OCR, reading the booklet first when there is none (or it is stale)."""
    cached = load(booklet.folder, booklet.source if booklet.source.exists() else None)
    if cached is not None:
        return cached
    ocr = setup()
    blueprint = papers.find(booklet.paper) if booklet.paper else None
    lexicon = exam_lexicon(papers.memory.content, blueprint, ocr.word_list) if blueprint else None
    if lexicon is None:
        from tarn_core.services.ocr.selector import Lexicon

        lexicon = Lexicon(frozenset(), ocr.word_list)
    pages = []
    for page in read_into(booklet.source, booklet.folder, ocr, lexicon):
        progress(f"{booklet.label}: page {page.index + 1} read")
        pages.append(page)
    return pages


def page_inputs(label: str, pages: Sequence[CachedPage]) -> list[PageInput]:
    """Cached pages as segmentation input, with ids made up from the booklet label."""
    inputs = []
    for page in pages:
        page_id = PageId(uuid.uuid5(uuid.NAMESPACE_URL, f"tarn-bench:{label}:{page.index}"))
        ids = [
            RegionId(uuid.uuid5(uuid.NAMESPACE_URL, f"tarn-bench:{label}:{page.index}:{k}"))
            for k in range(len(page.regions))
        ]
        regions = tuple(
            Region(
                id=ids[k],
                college_id=_COLLEGE,
                page_id=page_id,
                kind=r.kind,
                box=r.box,
                teacher_text=r.text,
                parent_id=None if r.parent is None else ids[r.parent],
                row=r.row,
                col=r.col,
            )
            for k, r in enumerate(page.regions)
        )
        inputs.append(
            PageInput(
                page_id=page_id,
                index=page.index,
                width=page.width,
                height=page.height,
                regions=regions,
            )
        )
    return inputs


def segment(
    booklet: Booklet,
    pages: Sequence[CachedPage],
    papers: Papers,
    embedder: Embedder,
    policy: SegmentationPolicy,
) -> BookletRun | None:
    if booklet.paper is None:
        return None
    blueprint = papers.find(booklet.paper)
    inputs = page_inputs(booklet.label, pages)
    started = time.perf_counter()
    result = Segmenter(blueprint, papers.texts[blueprint.title], embedder, policy).segment(inputs)
    return BookletRun(
        booklet=booklet.label,
        paper=booklet.paper,
        result=result,
        page_numbers={p.page_id: p.index + 1 for p in inputs},
        seconds=time.perf_counter() - started,
        truth=booklet.truth,
    )
