"""api/services/ingestion.py — adapter between the API/worker and the
ingestion pass in core/booklet_pipeline.py.

TRANSLATION ONLY, exactly like api/services/evaluation.py. There is no
rasterization, segmentation, classification, marker detection, assignment or
persistence logic in this file and there must never be. What lives here is the
API-shaped part: turning {upload_id, exam_id, student_id, paper_id} into a job
payload, fetching the stored PDF back to a local path because that is what
core/booklet_ingest.py takes, and chaining the evaluation job that follows.

WHY A SEPARATE JOB TYPE, rather than an ingest phase inside booklet_eval.
Decided 2026-09-05 with the product owner; the argument is §7C's and it did
not change when the API grew:

  * Ingestion writes a student's answer_blocks ONCE. Evaluation reads them.
    Folding ingestion into booklet_eval would re-run segmentation on every
    re-score — a corrected reference answer, a different scoring method, a
    re-run after a model upgrade — and each of those would rewrite the
    student's regions. There is no version history for answer_blocks
    (PROJECT_CONTEXT.md §7 open decision 2), so the previous segmentation's
    provenance would be gone with no way to get it back.
  * Segmentation has to be verifiable BEFORE anything scores on top of it.
    Two job types means a failed ingestion is a failed ingestion — named,
    with its own error and its own stage — instead of a booklet_eval that
    failed somewhere in the middle of a job that did two unrelated things.
  * It is also the cheap option: evaluation_jobs.job_type is free TEXT
    precisely so a new kind of work needs no migration (014 §2).

The cost is that a client polls twice: the ingest job first, then the
evaluation job whose id lands in the ingest job's `result`. That is the honest
shape of "two passes", and POST /evaluate tells the client which one it is
looking at via `job_type`.

WHY paper_id IS A REQUEST FIELD. Marker labels ('Q1', 'Q2a') are resolved
against pattern_slots.slot_label THROUGH a specific generated paper, because
slot labels repeat across papers. There is no FK from exams to
generated_papers (§7C's open schema gap, re-verified 2026-09-05), so the exam
alone cannot name the paper. Adding `exams.paper_id` would close it and is on
§10's list of product decisions not to settle unilaterally — so the id is
asked for instead, and validated here. If that column is ever added, this
field becomes optional and falls back to the exam's paper; nothing built on it
is wasted.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

import api.services.evaluation
import core.booklet_persist
import core.booklet_pipeline
import core.jobs
import core.storage
import core.uploads
from api.services.evaluation import JOB_TYPE_BOOKLET_EVAL, UploadNotFoundError

#: evaluation_jobs.job_type for the ingestion pass.
JOB_TYPE_BOOKLET_INGEST = "booklet_ingest"


class PaperNotFoundError(LookupError):
    """The paper_id names no generated_papers row.

    Its own type so the router answers 404 rather than letting the caller
    discover it minutes later as a failed job. Paper tables are NOT tenanted
    (§11 — the question bank is shared), so this really does mean "no such
    paper", not "not yours".
    """


# ─────────────────────────────── enqueue ────────────────────────────────────

def enqueue_booklet_ingest(
    cur,
    *,
    college_id,
    upload_id,
    exam_id,
    student_id,
    paper_id,
    dpi: int | None = None,
    min_confidence: float | None = None,
    skip_denoise: bool = False,
    stub: bool = False,
    evaluate_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolves the upload and the paper, then queues one booklet_ingest job.

    Both are resolved HERE, in the request, for the reason
    enqueue_booklet_evaluation resolves the upload: migration 015 puts no FK
    from evaluation_jobs.payload into anything, so without this a client gets
    a 404's worth of information as a failed job minutes later.

    `evaluate_after` carries the booklet_eval payload options this ingestion
    should chain into on success. None means "ingest only".
    """
    upload = core.uploads.get_upload(cur, upload_id=upload_id, college_id=college_id)
    if upload is None:
        raise UploadNotFoundError(
            f"No upload {upload_id} for this college. Either the id is wrong, it "
            f"belongs to another college, or this transaction has no RLS context "
            f"(booklet_uploads fails closed and silently — CLAUDE_CONTEXT.md §6)."
        )

    try:
        core.booklet_persist._verify_paper_exists(cur, str(paper_id))
    except ValueError as exc:
        raise PaperNotFoundError(str(exc)) from exc

    payload = {
        "upload_id": str(upload_id),
        "exam_id": str(exam_id),
        "student_id": str(student_id),
        "paper_id": str(paper_id),
        # Resolved now so the worker never redoes a tenant-scoped lookup, and
        # so the job row records exactly which stored scan it was pointed at.
        "source_scan_url": upload["blob_url"],
        # The mode the file was STORED under, not the worker's own setting —
        # a worker configured for minio must not go looking for a dummy ref in
        # a bucket.
        "storage_mode": upload["storage_mode"],
        "options": {
            "dpi": dpi,
            "min_confidence": min_confidence,
            "skip_denoise": skip_denoise,
            "stub": stub,
        },
        "evaluate_after": evaluate_after,
    }

    return core.jobs.enqueue_job(
        cur,
        college_id=college_id,
        job_type=JOB_TYPE_BOOKLET_INGEST,
        payload=payload,
    )


# ────────────────────────────── run (worker) ────────────────────────────────

def run_booklet_ingest(conn, payload: dict, *, on_progress=None,
                       dry_run: bool = False) -> dict:
    """Executes one booklet_ingest job. Called by scripts/run_job_worker.py.

    The whole of the wiring: fetch the stored PDF to a local path, hand it to
    core/booklet_pipeline.ingest_booklet_file, delete the temp file. Every
    decision about regions, classification, markers, assignment and what gets
    written is made inside core/ (§7C); none of them is re-made here.

    THE PDF IS FETCHED BACK FROM STORAGE rather than passed through the job
    payload, and core/booklet_ingest.py takes a PATH rather than bytes because
    pypdfium2 does. A queued job may not run for minutes, so the payload holds
    the stable "bucket/key" ref and never a presigned URL (§10).

    ALREADY-INGESTED BOOKLETS ARE SKIPPED, not re-ingested. The report comes
    back with skipped='already_ingested' and the existing answer_blocks are
    untouched — see core/booklet_pipeline.py's docstring for why re-ingestion
    would duplicate rather than replace them. This is checked here as well as
    at enqueue time because the two are separated by a queue, and two
    evaluations of one booklet requested at once must not both ingest it.
    """
    options = payload.get("options") or {}
    report_progress = on_progress or (lambda *a, **k: None)
    storage_mode = payload.get("storage_mode") or "dummy"

    report_progress("fetching", 0.05,
                    f"Fetching the stored booklet ({payload['source_scan_url']}).")
    handle, tmp_path = tempfile.mkstemp(prefix="booklet-ingest-", suffix=".pdf")
    os.close(handle)
    try:
        core.storage.fetch_to_path(payload["source_scan_url"], tmp_path)

        kwargs = {
            "student_id": payload["student_id"],
            "exam_id": payload["exam_id"],
            "paper_id": payload["paper_id"],
            "storage_mode": storage_mode,
            "source_pdf_url": payload["source_scan_url"],
            "persist": True,
            "dry_run": dry_run,
            "stub": bool(options.get("stub")),
            "skip_denoise": bool(options.get("skip_denoise")),
            "skip_if_ingested": True,
            "conn": conn,
            "on_progress": report_progress,
        }
        # Only override the pipeline's own defaults when the payload actually
        # named a value — a None here would otherwise become dpi=None.
        if options.get("dpi"):
            kwargs["dpi"] = int(options["dpi"])
        if options.get("min_confidence") is not None:
            kwargs["min_confidence"] = float(options["min_confidence"])

        return core.booklet_pipeline.ingest_booklet_file(tmp_path, **kwargs)
    finally:
        os.unlink(tmp_path)


def enqueue_chained_evaluation(cur, *, college_id, payload: dict) -> dict[str, Any] | None:
    """Queues the booklet_eval job a finished booklet_ingest job chains into.

    Returns the job, or None when `evaluate_after` was not set (an
    ingest-only job).

    IDEMPOTENT, and it has to be. The worker's crash window (killed between
    doing the work and marking the row terminal) means a requeued ingest job
    can reach this a second time, and a booklet ingested once must not be
    evaluated twice by accident — that is a duplicate ledger row and duplicate
    LLM spend. The match is on the three ids that identify the booklet, so a
    re-run finds the job the first run queued whatever its options were.

    Failed evaluations are NOT matched: if the first evaluation failed, a
    re-run of the ingest job should queue a fresh one rather than point at a
    corpse.
    """
    evaluate_after = payload.get("evaluate_after")
    if not evaluate_after:
        return None

    identity = {
        "upload_id": payload["upload_id"],
        "student_id": payload["student_id"],
        "exam_id": payload["exam_id"],
    }
    existing = core.jobs.find_job_by_payload(
        cur,
        college_id=college_id,
        job_type=JOB_TYPE_BOOKLET_EVAL,
        match=identity,
        statuses=["queued", "running", "succeeded"],
    )
    if existing is not None:
        return existing

    return core.jobs.enqueue_job(
        cur,
        college_id=college_id,
        job_type=JOB_TYPE_BOOKLET_EVAL,
        payload={
            **identity,
            "source_scan_url": payload["source_scan_url"],
            "options": evaluate_after,
        },
    )


# ──────────────────── the one entry point POST /evaluate uses ───────────────

def enqueue_evaluation_pipeline(
    cur,
    *,
    college_id,
    upload_id,
    exam_id,
    student_id,
    paper_id,
    stub: bool = False,
    stub_extraction: bool = False,
    stub_llm: bool = False,
    method: str | None = None,
    dpi: int | None = None,
    min_confidence: float | None = None,
    skip_denoise: bool = False,
) -> dict[str, Any]:
    """Queues whatever this booklet actually needs, and says which it was.

    Returns {"job": <the job to poll first>, "ingest_job_id": str | None}.

    The branch, and why it is here rather than in the router: a booklet that
    has ALREADY been ingested must not be ingested again — re-segmentation
    would duplicate its answer_blocks (core/booklet_pipeline.py) — so a
    re-score of an ingested booklet goes straight to booklet_eval and reuses
    the regions that exist. A booklet with no regions gets a booklet_ingest
    job that chains into the evaluation on success.

    The check is advisory, not the guarantee. Two clients can ask at the same
    moment and both see "no regions"; run_booklet_ingest re-checks inside the
    worker, where the answer is authoritative because the ingestion that would
    duplicate is in the same transaction as the check. What this branch buys
    is that the common case — re-scoring — never queues an ingest job at all,
    and that GET /jobs/{id} tells a client the truth about what it is waiting
    for.
    """
    upload = core.uploads.get_upload(cur, upload_id=upload_id, college_id=college_id)
    if upload is None:
        raise UploadNotFoundError(
            f"No upload {upload_id} for this college. Either the id is wrong, it "
            f"belongs to another college, or this transaction has no RLS context "
            f"(booklet_uploads fails closed and silently — CLAUDE_CONTEXT.md §6)."
        )

    already_ingested = core.booklet_pipeline.booklet_is_ingested(
        cur,
        student_id=str(student_id),
        exam_id=str(exam_id),
        source_scan_url=upload["blob_url"],
    )

    if already_ingested:
        job = api.services.evaluation.enqueue_booklet_evaluation(
            cur,
            college_id=college_id,
            upload_id=upload_id,
            exam_id=exam_id,
            student_id=student_id,
            stub=stub,
            stub_extraction=stub_extraction,
            stub_llm=stub_llm,
            method=method,
        )
        return {"job": job, "ingest_job_id": None}

    job = enqueue_booklet_ingest(
        cur,
        college_id=college_id,
        upload_id=upload_id,
        exam_id=exam_id,
        student_id=student_id,
        paper_id=paper_id,
        dpi=dpi,
        min_confidence=min_confidence,
        skip_denoise=skip_denoise,
        stub=stub,
        evaluate_after={
            "stub": stub,
            "stub_extraction": stub_extraction,
            "stub_llm": stub_llm,
            "method": method,
        },
    )
    return {"job": job, "ingest_job_id": job["job_id"]}
