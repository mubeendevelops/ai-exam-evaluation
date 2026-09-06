"""api/schemas/students.py — Pydantic v2 models for GET /api/v1/students."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict


class StudentSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: uuid.UUID
    name: str
    roll_number: str
    email: str
    enrolled_at: dt.datetime


def student_to_summary(row: dict[str, Any]) -> StudentSummary:
    return StudentSummary(
        student_id=row["student_id"], name=row["name"],
        roll_number=row["roll_number"], email=row["email"],
        enrolled_at=row["enrolled_at"],
    )
