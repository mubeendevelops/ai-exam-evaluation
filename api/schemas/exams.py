"""api/schemas/exams.py — Pydantic v2 models for GET /api/v1/exams."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

#: exam_status enum (migration 001 line 32).
ExamStatus = Literal["draft", "live", "closed"]


class ExamSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_id: uuid.UUID
    name: str
    conducted_at: dt.datetime | None = None
    status: ExamStatus


def exam_to_summary(row: dict[str, Any]) -> ExamSummary:
    return ExamSummary(
        exam_id=row["exam_id"], name=row["name"],
        conducted_at=row["conducted_at"], status=row["status"],
    )
