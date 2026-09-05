"""api/schemas/jobs.py — Pydantic v2 models for the job-status endpoint."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "running", "succeeded", "failed"]

#: Job kinds the API knows how to enqueue. evaluation_jobs.job_type is free
#: TEXT in the DB (migration 014 §2 — new kinds must not need a migration);
#: this is the API's narrower view of which of them a client may ask for.
#: Defined in api/services/evaluation.py, which is what enqueues it; re-exported
#: here so schema consumers do not have to import a service.
from api.services.evaluation import JOB_TYPE_BOOKLET_EVAL  # noqa: E402,F401


class JobProgress(BaseModel):
    """Where a job has got to.

    `stage` and `percent` come from evaluation_jobs.progress, which the worker
    writes between phases (migration 015). They are STAGE MARKERS, not measured
    fractions, and the distinction is deliberate: core/booklet_evaluator runs
    extraction and evaluation as bounded thread pools with no progress
    callback, so completion *within* a phase is genuinely unknown. Reporting
    which phase a job is in is honest. Interpolating a percentage inside one
    would be a fabricated number that a client would render as a smoothly
    moving bar — and it would hide, from us, that real progress reporting does
    not exist. Adding a callback to core/ is what would make these real.

    `counts` carries whatever the stage actually knows (regions, questions), so
    a client has something true to display alongside the coarse marker.
    """

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(
        description="Machine-readable phase: queued, loading, evaluating, "
                    "persisting, done, failed."
    )
    percent: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Stage marker in [0, 1] — NOT a measured fraction of work "
                    "done. Null when the stage is unknown.",
    )
    message: str = Field(description="Human-readable one-liner about the current state.")
    attempts: int = Field(
        ge=0,
        description=(
            "How many times this job has been CLAIMED by a worker. >1 means it "
            "was retried, which usually means it died mid-run."
        ),
    )
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="What the current stage knows, e.g. {'regions': 10, "
                    "'questions': 5}. Empty until a stage reports something.",
    )


class JobResponse(BaseModel):
    """One job as reported by GET /api/v1/jobs/{job_id}.

    college_id is deliberately NOT exposed. The caller already knows their own
    tenant (they authenticated as it), a job from any other tenant returns 404
    rather than a body, so the field could only ever echo the request — and
    every field an API returns is a field a future endpoint might be tempted
    to accept.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    job_type: str
    status: JobStatus
    progress: JobProgress
    error: str | None = Field(
        default=None,
        description="Why the job failed. Null unless status is 'failed'.",
    )
    result: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The worker's run report, once terminal. NOT the scores — those "
            "are appended to the evaluation_results ledger."
        ),
    )
    created_at: dt.datetime
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


_PROGRESS_BY_STATUS: dict[str, tuple[str, float | None, str]] = {
    "queued": ("queued", 0.0, "Waiting for a worker to pick this up."),
    "running": ("running", None, "A worker is running this job."),
    "succeeded": ("done", 1.0, "Finished. See `result` for the run report."),
    "failed": ("failed", None, "Failed. See `error`."),
}


def _progress(job: dict[str, Any]) -> JobProgress:
    """Prefers what the worker actually reported; falls back to the status.

    The stored progress is only trusted while the job is RUNNING. A terminal
    job's last progress row is a stale mid-run snapshot — a job that failed
    during 'persisting' would otherwise report 85% forever, which reads as
    "nearly done" rather than "dead".
    """
    stage, percent, message = _PROGRESS_BY_STATUS[job["status"]]
    reported = job.get("progress") or {}

    if job["status"] == "running" and reported:
        stage = reported.get("stage", stage)
        percent = reported.get("percent", percent)
        message = reported.get("message", message)

    return JobProgress(
        stage=stage,
        percent=percent,
        message=message,
        attempts=job["attempts"],
        counts=reported.get("counts") or {},
    )


def job_to_response(job: dict[str, Any]) -> JobResponse:
    """Maps a core/jobs.py row dict onto the response model.

    One mapping function, here, so the row shape is translated in exactly one
    place — the routers never hand a raw DB dict to FastAPI, which would leak
    college_id and any column added later by accident.
    """
    return JobResponse(
        job_id=job["job_id"],
        job_type=job["job_type"],
        status=job["status"],
        progress=_progress(job),
        error=job["error"],
        result=job["result"],
        created_at=job["created_at"],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
    )
