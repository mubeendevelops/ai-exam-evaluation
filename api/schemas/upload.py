"""api/schemas/upload.py — Pydantic v2 models for POST /api/v1/upload.

There is no REQUEST model here on purpose: the endpoint takes multipart form
data (UploadFile), which FastAPI binds from the form directly. A Pydantic
model would have to be a JSON body, and a booklet PDF is not JSON.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field



class UploadResponse(BaseModel):
    """What POST /api/v1/upload returns.

    UPLOADING AND EVALUATING ARE SEPARATE CALLS. An upload is a stored PDF and
    nothing more; POST /api/v1/evaluate is what queues work against it. That
    split exists because one uploaded booklet can be evaluated more than once —
    a different exam/student binding, a re-run after a reference answer is
    fixed, a re-run after ingestion is corrected.

    This response previously also carried a `job_id`, because an upload used to
    enqueue exactly one job and the two ids were equal. That field is gone as of
    migration 015: there is no job at upload time any more, and returning a
    job id that named a job no worker would ever run was worse than returning
    nothing. The upload now has its own row (`booklet_uploads`), which is what
    `upload_id` identifies.
    """

    model_config = ConfigDict(extra="forbid")

    upload_id: uuid.UUID = Field(
        description=(
            "Identifies the stored booklet. Pass it to POST /api/v1/evaluate "
            "to queue an evaluation."
        )
    )
    filename: str = Field(description="The client's original filename, as sent.")
    size_bytes: int = Field(ge=0, description="Bytes actually received and stored.")
    blob_url: str = Field(
        description=(
            "Stable 'bucket/key' storage reference — NEVER a presigned URL "
            "(CLAUDE_CONTEXT.md §10). Under storage_mode='dummy' this is a "
            "deterministic 'dummy-storage/...' placeholder with no object "
            "behind it."
        )
    )
    storage_mode: str = Field(
        description="'dummy' or 'minio' — which core/storage.py path stored it."
    )
    uploaded_at: dt.datetime


class UploadSummary(BaseModel):
    """One row of GET /api/v1/uploads."""

    model_config = ConfigDict(extra="forbid")

    upload_id: uuid.UUID
    filename: str
    size_bytes: int = Field(ge=0)
    blob_url: str
    storage_mode: str
    uploaded_at: dt.datetime
    bound: bool = Field(
        description="Exam-binding state: true when at least one `answers` "
                    "row was ingested from this upload, for any exam/student. "
                    "See core/uploads.py::_BOUND_EXISTS.",
    )


def upload_to_summary(row: dict[str, Any]) -> UploadSummary:
    return UploadSummary(
        upload_id=row["upload_id"], filename=row["filename"],
        size_bytes=row["size_bytes"], blob_url=row["blob_url"],
        storage_mode=row["storage_mode"], uploaded_at=row["uploaded_at"],
        bound=row["bound"],
    )
