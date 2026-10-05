"""The sample-booklet calibration set (``tarn score samples``): every answer of the benchmark
booklets of ``var/segmentation/`` (P12), cut by the segmenter from the cached OCR, written to
``var/scoring/samples/answers.jsonl`` with its question code. Marks go in ``marks.json``
beside it (provisional marks by Claude until teachers mark them, D95).

Only booklets whose OCR is cached are used: nothing is read again here."""

from collections.abc import Callable
from pathlib import Path

from tarn_adapters.scoring.sets import write_answers
from tarn_adapters.segmentation.bench import Papers, discover, page_inputs, segment
from tarn_adapters.segmentation.ocrcache import load
from tarn_core.domain.booklet import Region
from tarn_core.domain.content import Question
from tarn_core.ids import RegionId
from tarn_core.ports.engines import Embedder
from tarn_core.services.scoring.assemble import segment_text
from tarn_core.services.segmentation.segmenter import ProposedSegment, SegmentationPolicy


def build_samples(
    root: Path,
    repo: Path,
    out: Path,
    papers: Papers,
    embedder: Embedder,
    progress: Callable[[str], None],
) -> tuple[Path, int]:
    rows: list[dict[str, object]] = []
    for booklet in discover(root, repo):
        if booklet.paper is None:
            continue
        pages = load(booklet.folder, booklet.source if booklet.source.exists() else None)
        if pages is None:
            progress(f"{booklet.label}: no cached OCR, skipped (run `tarn bench segment` first)")
            continue
        run = segment(booklet, pages, papers, embedder, SegmentationPolicy())
        if run is None:
            continue
        blueprint = papers.find(booklet.paper)
        regions: dict[RegionId, Region] = {
            r.id: r for p in page_inputs(booklet.label, pages) for r in p.regions
        }
        by_slot: dict[str, list[ProposedSegment]] = {}
        for proposed in run.result.segments:
            if proposed.slot_label is not None:
                by_slot.setdefault(proposed.slot_label, []).append(proposed)
        for slot, proposals in by_slot.items():
            _, question_id, _ = blueprint.leaf(slot)
            code = papers.memory.content.get(Question, question_id).code
            text = segment_text(proposals, regions)
            rows.append(
                {
                    "id": f"{booklet.label}:{slot}",
                    "booklet": booklet.label,
                    "paper": booklet.paper,
                    "slot": slot,
                    "question": code,
                    "text": text.text,
                }
            )
        progress(f"{booklet.label}: {len(by_slot)} answers")
    return write_answers(out, rows), len(rows)
