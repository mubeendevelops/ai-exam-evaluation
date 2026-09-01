#!/usr/bin/env python3
"""
scripts/benchmark_booklet_segmentation.py — scores core/booklet_segmenter.py
against media/booklets/ground_truth.json.

Mirrors scripts/benchmark_ocr_engines.py and
scripts/benchmark_diagram_extraction.py: offline measurement tooling, NOT part
of the production pipeline. It is the source of truth for any accuracy number
quoted in core/booklet_segmenter.py's docstring — a module docstring cannot be
evidence for its own claims.

METRICS
  region detection   fraction of ground-truth regions matched by a predicted
                     region at IoU >= --iou (default 0.5)
  classification     of the matched regions, fraction whose block_type is right
  assignment         of the matched regions, fraction assigned to the right
                     question marker
  spurious           predicted regions that matched no ground-truth region

COORDINATE SPACES DIFFER, and reconciling them is the whole reason this is a
script and not an assert. Ground truth is recorded in the render space that
scripts/generate_booklet_benchmark.py drew in (1240x1754); ingestion
rasterizes the PDF at --dpi, giving a larger page. Ground-truth boxes are
scaled by the measured page-size ratio before matching. Getting this wrong
silently reports 0.000 across the board, which is what happened first.

Usage:
    python3 scripts/benchmark_booklet_segmentation.py
    python3 scripts/benchmark_booklet_segmentation.py --iou 0.6 --dpi 300
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import booklet_ingest, booklet_segmenter  # noqa: E402

DEFAULT_PDF = REPO_ROOT / "media" / "booklets" / "sample_booklet.pdf"
DEFAULT_TRUTH = REPO_ROOT / "media" / "booklets" / "ground_truth.json"


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    return intersection / (aw * ah + bw * bh - intersection)


def _scale(bbox, factor):
    return [int(round(v * factor)) for v in bbox]


def score(pdf_path, truth_path, *, iou_threshold=0.5, dpi=booklet_ingest.DEFAULT_DPI,
          min_confidence=booklet_segmenter.DEFAULT_MIN_CONFIDENCE) -> dict:
    truth_doc = json.loads(Path(truth_path).read_text())

    ingested = booklet_ingest.ingest_booklet(
        pdf_path, storage_mode="dummy", dpi=dpi, skip_denoise=True)
    segmented = booklet_segmenter.segment_booklet(
        ingested["pages"], min_confidence=min_confidence)
    predicted = segmented["regions"]

    truth_w = truth_doc["page_size"][0]
    actual_w = ingested["pages"][0]["width"]
    factor = actual_w / truth_w

    expected = []
    for page in truth_doc["pages"]:
        for region in page["regions"]:
            if region["type"] == "marker":
                continue     # markers are dropped by design, not answers
            expected.append({
                "page_number": page["page_number"],
                "bbox": _scale(region["bbox"], factor),
                "block_type": region["type"],
                "question": region["question"],
            })

    matched_predictions, rows = set(), []
    detected = classified = assigned = 0
    for item in expected:
        candidates = [
            (index, region) for index, region in enumerate(predicted)
            if region["page_number"] == item["page_number"]
        ]
        best_index, best_score = None, 0.0
        for index, region in candidates:
            overlap = _iou(item["bbox"], region["bbox"])
            if overlap > best_score:
                best_index, best_score = index, overlap

        row = {**item, "iou": round(best_score, 3),
               "predicted_type": None, "predicted_question": None}
        if best_index is not None and best_score >= iou_threshold:
            region = predicted[best_index]
            matched_predictions.add(best_index)
            detected += 1
            classified += int(region["block_type"] == item["block_type"])
            assigned += int(region.get("assigned_question") == item["question"])
            row["predicted_type"] = region["block_type"]
            row["predicted_question"] = region.get("assigned_question")
        rows.append(row)

    total = len(expected)
    return {
        "scale_factor": round(factor, 4),
        "total": total,
        "detected": detected,
        "classified": classified,
        "assigned": assigned,
        "spurious": len(predicted) - len(matched_predictions),
        "markers": segmented["markers"],
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--pdf", default=str(DEFAULT_PDF))
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH))
    ap.add_argument("--iou", type=float, default=0.5,
                    help="IoU threshold for calling a region detected (default: 0.5)")
    ap.add_argument("--dpi", type=int, default=booklet_ingest.DEFAULT_DPI)
    ap.add_argument("--min-confidence", type=float,
                    default=booklet_segmenter.DEFAULT_MIN_CONFIDENCE)
    args = ap.parse_args()

    if not Path(args.pdf).exists():
        print(f"ERROR: {args.pdf} not found — run "
              "scripts/generate_booklet_benchmark.py first.", file=sys.stderr)
        return 1

    result = score(args.pdf, args.truth, iou_threshold=args.iou, dpi=args.dpi,
                   min_confidence=args.min_confidence)

    header = f"Booklet segmentation benchmark (IoU >= {args.iou}, {args.dpi} DPI)"
    print(header)
    print("=" * len(header))
    print(f"ground-truth scale factor: {result['scale_factor']}")
    print()
    print(f"    {'pg':>2}  {'expected bbox':<26} {'type':<8} -> "
          f"{'predicted':<8} {'iou':>5}  {'question':<9} -> predicted")
    for row in result["rows"]:
        print(f"    {row['page_number']:>2}  {str(row['bbox']):<26} "
              f"{row['block_type']:<8} -> {str(row['predicted_type']):<8} "
              f"{row['iou']:>5.2f}  {row['question']:<9} -> "
              f"{row['predicted_question']}")
    print()

    total = result["total"]
    print(f"region detection    : {result['detected']}/{total} = {result['detected']/total:.3f}")
    print(f"classification      : {result['classified']}/{total} = {result['classified']/total:.3f}")
    print(f"question assignment : {result['assigned']}/{total} = {result['assigned']/total:.3f}")
    print(f"spurious regions    : {result['spurious']}")
    print(f"markers detected    : {len(result['markers'])} "
          f"({', '.join(m['raw_text'].strip() for m in result['markers'])})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
