#!/usr/bin/env python3
"""
scripts/ingest_booklet.py — one answer-booklet PDF in, a page-by-page region
report out. The ingestion + segmentation half of the full-booklet pipeline
(Task 5); NOTHING IS EVALUATED HERE.

BEHAVIOUR:
  1. Rasterize every PDF page, deskew and denoise it, and upload the prepared
     page image to object storage (core/booklet_ingest.py). Stable
     "bucket/key" refs only, never presigned URLs (PROJECT_CONTEXT.md rule 1).
  2. Segment each page into regions and classify each as text / table /
     diagram / formula with a confidence, using PaddleOCR's layout model
     (core/booklet_segmenter.py). Low-confidence regions are flagged for human
     review rather than routed to a plugin.
  3. Detect question-number markers ("Q1", "2.", "a)") with the existing OCR
     ensemble and assign each region to the most recent preceding marker —
     across page breaks, since an answer continues onto the next page.
  4. Crop each assigned region, upload the crop, and persist the region as an
     answer_blocks row with its page number, bbox and confidence
     (core/booklet_persist.py). Answers rows are created per question as
     needed.
  5. Print the region report: every region either carries a question or an
     explicit flag. Nothing is silently dropped and nothing is guessed.

The next stage (evaluation, orchestration, rate limiting, aggregation) is
deliberately NOT in this script — regions have to be verified correct before
anything scores them.

THE PIPELINE ITSELF LIVES IN core/booklet_pipeline.py, not here. This file is
argument parsing and the region report; the API's booklet_ingest job calls the
same core function (CLAUDE_CONTEXT.md §11 rule 1). Anything you change about
the ORDER of the passes, or about re-ingestion, belongs down there.

Usage:
    # Segmentation report only, no DB and no network:
    python3 scripts/ingest_booklet.py media/booklets/sample_booklet.pdf --no-persist

    # Full run against real storage, rolled back:
    python3 scripts/ingest_booklet.py booklet.pdf \\
        --student-id <uuid> --exam-id <uuid> --paper-id <uuid> \\
        --storage minio --dry-run

    # Offline smoke test, no model load at all:
    python3 scripts/ingest_booklet.py booklet.pdf --no-persist --stub
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import booklet_ingest, booklet_segmenter  # noqa: E402
from core.booklet_pipeline import ingest_booklet_file  # noqa: E402
from core.plugins.persistence import RLSVisibilityError  # noqa: E402


def _print_report(report: dict) -> None:
    header = f"Booklet ingestion — {report['pdf']}"
    print(header)
    print("=" * len(header))
    print(f"source PDF : {report['source_pdf_url']}")
    print(f"pages      : {report['page_count']}   storage: {report['storage']}")
    print()

    outcomes = {}
    if report["persisted"]:
        for item in report["persisted"]["results"]:
            outcomes[(item["page_number"], tuple(item["bbox"]))] = item

    by_page = {}
    for region in report["regions"]:
        by_page.setdefault(region["page_number"], []).append(region)

    for page in report["pages"]:
        number = page["page_number"]
        print(f"Page {number}/{report['page_count']}   "
              f"deskew {page['deskew_degrees']:+.2f}°   {page['page_image_url']}")
        rows = by_page.get(number, [])
        if not rows:
            print("    (no regions detected)")
            print()
            continue
        print(f"    {'#':>2}  {'bbox':<26} {'type':<8} {'conf':>5}  "
              f"{'question':<9} flags")
        for index, region in enumerate(rows, start=1):
            bbox = region["bbox"]
            flags = list(region.get("review_reasons") or [])
            outcome = outcomes.get((number, tuple(bbox)))
            if outcome and outcome.get("skip_reason"):
                flags.append(f"NOT PERSISTED: {outcome['skip_reason']}")
            elif outcome and outcome.get("block_id"):
                flags.append(f"block={outcome['block_id'][:8]}")
            question = region.get("assigned_question") or "-"
            if not region.get("assigned_question"):
                question = "UNASSIGNED"
            print(f"    {index:>2}  {str(bbox):<26} {region['block_type']:<8} "
                  f"{region['confidence']:>5.2f}  {question:<9} "
                  f"{', '.join(flags) if flags else '-'}")
        print()

    total = len(report["regions"])
    assigned = sum(1 for r in report["regions"] if r.get("assigned_question"))
    flagged = sum(1 for r in report["regions"] if r.get("needs_review"))
    print(f"Summary: {total} regions — {assigned} assigned, "
          f"{total - assigned} unassigned, {flagged} flagged for review")
    print(f"         {len(report['markers'])} question markers detected: "
          f"{', '.join(sorted({m['raw_text'].strip() for m in report['markers']})) or '(none)'}")

    if report["persisted"]:
        persisted = report["persisted"]
        print(f"         {persisted['persisted']} blocks written, "
              f"{persisted['skipped']} skipped, "
              f"{len(persisted['answers'])} answers touched")
    else:
        print("         persistence skipped (--no-persist)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("pdf_path", type=Path, help="the booklet PDF to ingest")
    ap.add_argument("--student-id", help="students.student_id the booklet belongs to")
    ap.add_argument("--exam-id", help="exams.exam_id the booklet was written for")
    ap.add_argument("--paper-id",
                    help="generated_papers.paper_id whose slot_labels ('Q1', "
                         "'Q2a') the detected markers resolve against. Required "
                         "because there is no FK from exams to generated_papers "
                         "— see core/booklet_persist.py's module docstring.")
    ap.add_argument("--storage", default="dummy", choices=["dummy", "minio"],
                    help="object storage backend for page images and region crops")
    ap.add_argument("--dpi", type=int, default=booklet_ingest.DEFAULT_DPI,
                    help=f"PDF rasterization DPI (default: {booklet_ingest.DEFAULT_DPI})")
    ap.add_argument("--min-confidence", type=float,
                    default=booklet_segmenter.DEFAULT_MIN_CONFIDENCE,
                    help="below this a region is flagged for human review "
                         f"(default: {booklet_segmenter.DEFAULT_MIN_CONFIDENCE})")
    ap.add_argument("--no-persist", action="store_true",
                    help="segment and report only — no DB connection at all. "
                         "The fastest way to inspect segmentation quality.")
    ap.add_argument("--dry-run", action="store_true",
                    help="do every DB write, then roll back")
    ap.add_argument("--stub", action="store_true",
                    help="skip the layout model and OCR entirely, using fixed "
                         "deterministic regions — offline smoke test")
    ap.add_argument("--skip-denoise", action="store_true",
                    help="skip denoising (pure waste on a rendered PDF that "
                         "has no scan noise; ~1s/page)")
    ap.add_argument("--output", type=Path,
                    help="also write the full report as JSON to this path")
    ap.add_argument("--verbose", action="store_true",
                    help="print the full report JSON to stdout")
    args = ap.parse_args()

    if not args.pdf_path.exists():
        print(f"ERROR: PDF not found: {args.pdf_path}", file=sys.stderr)
        return 1

    persist = not args.no_persist
    if persist:
        missing = [name for name, value in (
            ("--student-id", args.student_id),
            ("--exam-id", args.exam_id),
            ("--paper-id", args.paper_id),
        ) if not value]
        if missing:
            print(f"ERROR: {', '.join(missing)} required unless --no-persist "
                  "is given.", file=sys.stderr)
            return 1

    try:
        report = ingest_booklet_file(
            args.pdf_path,
            student_id=args.student_id,
            exam_id=args.exam_id,
            paper_id=args.paper_id,
            storage_mode=args.storage,
            dpi=args.dpi,
            min_confidence=args.min_confidence,
            persist=persist,
            dry_run=args.dry_run,
            stub=args.stub,
            skip_denoise=args.skip_denoise,
        )
    except (ValueError, RLSVisibilityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    _print_report(report)

    if args.verbose:
        print()
        print(json.dumps(report, indent=2, default=str))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nReport written to {args.output}")

    if not persist:
        print("[no-persist] segmentation only, no changes persisted.")
    elif args.dry_run:
        print("[dry-run] rolled back, no changes persisted.")
    else:
        print("Committed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
