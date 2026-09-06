"""core/booklet_pipeline.py — one booklet PDF in, persisted answer_blocks out.

The ingestion pass end to end: rasterize + preprocess + store
(core/booklet_ingest.py), segment + classify + assign
(core/booklet_segmenter.py), crop + persist (core/booklet_persist.py).
NOTHING IS EVALUATED HERE — §7C and §7D are separate passes so segmentation
can be verified before anything scores on top of it, and so re-scoring a
booklet reads the blocks that already exist instead of rewriting them.

WHY THIS IS IN core/ AND NOT IN scripts/. It used to live in
scripts/ingest_booklet.py as `ingest_booklet_file`, with a docstring saying "a
future API endpoint can call it directly". That endpoint now exists, and
CLAUDE_CONTEXT.md §11 rule 1 is explicit that api/ imports core/ — the one
standing exception (api/services/questions.py importing from scripts/) is
recorded there as a debt, not a pattern to copy. So the function moved down
here and scripts/ingest_booklet.py imports it. Two front doors, one
implementation; the CLI is still the way to debug what the API did.

WHAT THIS MODULE OWNS, and what it deliberately does not:
  * it owns the ORDER of the three passes and the crop-upload step between
    segmentation and persistence,
  * it owns the ALREADY-INGESTED check (below),
  * it owns nothing about how a region is found, classified, assigned or
    written — those are the three modules it calls, unchanged.

THE ALREADY-INGESTED CHECK, and why it is here rather than in the caller.
core/booklet_persist.py::insert_region_block is an unconditional INSERT
(sequence_order just appends), and upsert_answer reuses the answers row — so
running ingestion twice over one booklet does not overwrite its blocks, it
DOUBLES them, and the evaluator then scores every region twice. There is no
unique constraint that would catch it: a booklet legitimately has several
regions on one page of one answer, so "same answer, same page" is not a
duplicate. `booklet_is_ingested()` is therefore the guard, and
`ingest_booklet_file(..., skip_if_ingested=True)` is how both front doors use
it. Re-ingesting a changed scan is still possible — it arrives as a new upload
with a new source_scan_url, which is a different booklet by this schema's own
definition of one.

Deleting-and-reingesting is NOT offered. answer_blocks rows carry region
provenance (migration 013) and there is no version history for them
(PROJECT_CONTEXT.md §7 open decision 2), so a re-ingest that dropped the old
rows would silently destroy the only record of what the segmenter saw the
first time. If that is ever wanted it is a product decision, not a flag.
"""
from __future__ import annotations

import os
import tempfile
import uuid

from core import booklet_ingest, booklet_persist, booklet_segmenter
from core import db as db_mod

#: Storage key prefix for the per-region crops.
STORAGE_KEY_PREFIX_REGIONS = "booklets/regions"


def booklet_is_ingested(cur, *, student_id: str, exam_id: str,
                        source_scan_url: str) -> bool:
    """True when this booklet already has persisted regions.

    A booklet is (student, exam, source_scan_url) — the same three-part
    address core/booklet_evaluator.load_booklet_tasks uses, because there is
    no booklets table and no exams.paper_id column (§7C's open schema gap).
    Matching on source_scan_url is what keeps two different scans of the same
    student's paper from being mistaken for one already-ingested booklet.

    RLS: answers and answer_blocks fail closed and SILENTLY (§6), so a False
    from this function means "no visible regions", which includes "this
    transaction has no tenant context". That is safe in the direction that
    matters — a missing context makes this say "not ingested", and the
    ingestion that follows then fails loudly inside persist_regions'
    _verify_tenant_rows rather than writing anything.
    """
    cur.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM answers       a
              JOIN answer_blocks ab ON ab.answer_id = a.answer_id
             WHERE a.student_id      = %s
               AND a.exam_id         = %s
               AND a.source_scan_url = %s
        )
        """,
        (str(student_id), str(exam_id), source_scan_url),
    )
    return bool(cur.fetchone()[0])


def _upload_region_crop(image, bbox, *, storage_mode: str) -> str:
    """Crop one region out of its page and store it. Returns a bucket/key ref."""
    x, y, w, h = bbox
    crop = image.crop((x, y, x + w, y + h))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        temp_path = handle.name
    try:
        crop.save(temp_path, format="PNG")
        return booklet_ingest._upload(
            temp_path, STORAGE_KEY_PREFIX_REGIONS,
            storage_mode=storage_mode, asset_id=str(uuid.uuid4()),
        )
    finally:
        os.unlink(temp_path)


def ingest_booklet_file(
    pdf_path,
    *,
    student_id=None,
    exam_id=None,
    paper_id=None,
    storage_mode="dummy",
    dpi=booklet_ingest.DEFAULT_DPI,
    min_confidence=booklet_segmenter.DEFAULT_MIN_CONFIDENCE,
    persist=True,
    dry_run=False,
    stub=False,
    skip_denoise=False,
    source_pdf_url=None,
    conn=None,
    skip_if_ingested=False,
    on_progress=None,
) -> dict:
    """Ingest + segment + associate + (optionally) persist one booklet.

    `conn` lets a caller that already has a connection with the right RLS
    context (the job worker, running as platform admin) reuse it. When it is
    None this opens its own and sets the platform-admin context itself, which
    is what the CLI wants. Either way persist_regions owns the commit — that
    is its documented contract, shared with core/plugins/persistence.py.

    `source_pdf_url` names a PDF ALREADY IN STORAGE, so the API path does not
    upload a second copy of the bytes POST /upload already stored, and the
    answers.source_scan_url written here matches the upload the client will
    ask to evaluate.

    `skip_if_ingested` returns early, without touching storage or the DB,
    when this booklet already has regions — see the module docstring on why
    re-ingesting duplicates rather than replaces. The report then carries
    `"skipped": "already_ingested"` and `persisted: None`, which a caller must
    NOT read as "nothing to evaluate".

    `on_progress(stage, percent, message, **counts)` is called between phases.
    Optional, and swallowed nowhere — a caller that passes a callback owns its
    failures.
    """
    report_progress = on_progress or (lambda *a, **k: None)

    owns_conn = False
    if persist and conn is None:
        conn = db_mod.get_connection()
        owns_conn = True
        cur = conn.cursor()
        # Booklet ingestion is a system-level operation, not a tenant request.
        cur.execute("SET LOCAL app.is_platform_admin = 'true'")
        cur.close()

    try:
        if persist and skip_if_ingested:
            if not source_pdf_url:
                # Without it the check would compare against NULL and match
                # nothing, so it would silently do nothing at all — worse than
                # refusing, because it looks like it ran. The CLI uploads a
                # fresh copy of the PDF on every run and therefore has no
                # already-stored ref to check against; it is the API path,
                # where POST /upload stored the file once, that can ask.
                raise ValueError(
                    "skip_if_ingested needs source_pdf_url — a booklet is "
                    "identified by (student, exam, source_scan_url), and "
                    "without the third part there is nothing to match.")
            report_progress("checking", 0.02,
                            "Checking whether this booklet was already ingested.")
            with conn.cursor() as cur:
                if booklet_is_ingested(cur, student_id=student_id, exam_id=exam_id,
                                       source_scan_url=source_pdf_url):
                    report_progress(
                        "done", 1.0,
                        "Already ingested — its answer_blocks were left untouched.")
                    return {
                        "pdf": str(pdf_path),
                        "source_pdf_url": source_pdf_url,
                        "page_count": None,
                        "storage": storage_mode,
                        "pages": [],
                        "markers": [],
                        "regions": [],
                        "persisted": None,
                        "skipped": "already_ingested",
                    }

        report_progress("rasterizing", 0.10,
                        f"Rasterizing and preprocessing the booklet at {dpi} DPI.")
        ingested = booklet_ingest.ingest_booklet(
            pdf_path, storage_mode=storage_mode, dpi=dpi,
            skip_denoise=skip_denoise, source_pdf_url=source_pdf_url)

        report_progress("segmenting", 0.35,
                        f"Segmenting {ingested['page_count']} pages into regions.",
                        pages=ingested["page_count"])
        segmented = booklet_segmenter.segment_booklet(
            ingested["pages"], min_confidence=min_confidence, stub=stub)
        regions = segmented["regions"]

        report = {
            "pdf": str(pdf_path),
            "source_pdf_url": ingested["source_pdf_url"],
            "page_count": ingested["page_count"],
            "storage": storage_mode,
            "pages": [{k: v for k, v in page.items() if k != "image"}
                      for page in ingested["pages"]],
            "markers": segmented["markers"],
            "regions": regions,
            "persisted": None,
            "skipped": None,
        }

        if not persist:
            report_progress("done", 1.0,
                            f"Segmented {len(regions)} regions (not persisted).",
                            pages=ingested["page_count"], regions=len(regions))
            return report

        # Only assigned regions get a crop uploaded — an unassigned region has
        # no answer to hang off, so its crop would be an orphan object in the
        # bucket.
        assigned = [r for r in regions if r.get("assigned_question")]
        report_progress("persisting", 0.70,
                        f"Writing {len(assigned)} of {len(regions)} regions as "
                        f"answer_blocks rows.",
                        pages=ingested["page_count"], regions=len(regions))

        pages_by_number = {p["page_number"]: p["image"] for p in ingested["pages"]}
        for region in assigned:
            region["blob_url"] = _upload_region_crop(
                pages_by_number[region["page_number"]], region["bbox"],
                storage_mode=storage_mode)

        report["persisted"] = booklet_persist.persist_regions(
            conn,
            regions=regions,
            paper_id=paper_id,
            student_id=student_id,
            exam_id=exam_id,
            source_scan_url=ingested["source_pdf_url"],
            dry_run=dry_run,
        )
        report_progress(
            "done", 1.0,
            f"{report['persisted']['persisted']} blocks written, "
            f"{report['persisted']['skipped']} skipped.",
            pages=ingested["page_count"], regions=len(regions),
            blocks=report["persisted"]["persisted"],
            answers=len(report["persisted"]["answers"]),
        )
        return report
    finally:
        if owns_conn:
            conn.close()
