"""A unit of work (P26, D162): one exam + one student year + one subject, for example
"Half-yearly, 2024 batch, Compiler Theory". One id, one question paper, one SME at a time.
College data: the admin creates it, uploads its booklets, and its owner SME marks them."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from tarn_core.errors import InvariantError
from tarn_core.ids import (
    BlueprintId,
    CollegeId,
    DelegationRequestId,
    SubjectId,
    UnitId,
    UnitTransferId,
    UserId,
)

UNIT_CODE = re.compile(r"U-\d{4,}")
YEAR_MAX = 20
REASON_MAX = 500
"""Reasons and notes of transfers and delegation requests (P27)."""


class UnitState(StrEnum):
    """Only ``open`` for now; P28 adds submitted, returned and frozen (D164)."""

    OPEN = "open"


def unit_code(number: int) -> str:
    """``U-0001`` for 1: the short readable code of a college's ``number``-th unit."""
    if number < 1:
        raise InvariantError("unit numbers start at 1")
    return f"U-{number:04d}"


def normalise_year(raw: str) -> str:
    """The student year (batch) as typed, trimmed and with inner runs of spaces made one."""
    return " ".join(raw.split())


@dataclass(frozen=True, slots=True, kw_only=True)
class Unit:
    id: UnitId
    college_id: CollegeId
    code: str
    paper_id: BlueprintId
    """The paper (an exam blueprint with every slot linked); booklets pin the version current
    when they are uploaded."""
    subject_id: SubjectId
    """The paper's subject (a paper used by a unit cannot change subject)."""
    year: str
    """The student year or batch, as text (for example ``2024``)."""
    owner_id: UserId
    """The SME who marks the unit: active and entitled to the subject when assigned."""
    due_date: date
    created_by: UserId | None
    """None for units made by migration 0014 from the booklets uploaded before units."""
    created_at: datetime
    state: UnitState = UnitState.OPEN
    version: int = 1

    def __post_init__(self) -> None:
        if not UNIT_CODE.fullmatch(self.code):
            raise InvariantError(f"unit code must look like U-0001, got {self.code!r}")
        if self.year != normalise_year(self.year) or not self.year:
            raise InvariantError("the student year must not be blank or padded")
        if len(self.year) > YEAR_MAX:
            raise InvariantError(f"the student year has at most {YEAR_MAX} characters")
        if self.created_at.tzinfo is None:
            raise InvariantError("created_at must be timezone-aware")
        if self.version < 1:
            raise InvariantError("unit version starts at 1")


def normalise_reason(raw: str) -> str:
    """A reason or note as typed, trimmed (inner line breaks kept)."""
    return raw.strip()


def _check_reason(what: str, value: str) -> None:
    if not value or value != value.strip():
        raise InvariantError(f"the {what} must not be blank or padded")
    if len(value) > REASON_MAX:
        raise InvariantError(f"the {what} has at most {REASON_MAX} characters")


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitTransfer:
    """The admin moved a unit to another SME (P27, D163): history, insert only. Booklets,
    answers and reviews stay as they were; the reviews keep the SME who marked them."""

    id: UnitTransferId
    college_id: CollegeId
    unit_id: UnitId
    from_owner: UserId
    to_owner: UserId
    reason: str
    by: UserId
    at: datetime
    request_id: DelegationRequestId | None = None
    """The SME's delegation request the transfer answered, if one was pending."""

    def __post_init__(self) -> None:
        _check_reason("reason", self.reason)
        if self.from_owner == self.to_owner:
            raise InvariantError("a transfer moves the unit to another SME")
        if self.at.tzinfo is None:
            raise InvariantError("at must be timezone-aware")


class RequestState(StrEnum):
    PENDING = "pending"
    DONE = "done"
    """The admin transferred the unit."""
    DECLINED = "declined"
    """The admin kept the unit with the SME, with a note."""
    WITHDRAWN = "withdrawn"
    """The SME took the request back."""


@dataclass(frozen=True, slots=True, kw_only=True)
class DelegationRequest:
    """An SME asks the admin to move one of their units to someone else (the delegation inbox,
    D163). One pending request per unit; the admin picks the new SME."""

    id: DelegationRequestId
    college_id: CollegeId
    unit_id: UnitId
    requested_by: UserId
    reason: str
    requested_at: datetime
    state: RequestState = RequestState.PENDING
    closed_by: UserId | None = None
    closed_at: datetime | None = None
    note: str = ""
    """The admin's note when declining."""

    def __post_init__(self) -> None:
        _check_reason("reason", self.reason)
        if self.requested_at.tzinfo is None:
            raise InvariantError("requested_at must be timezone-aware")
        closed = (self.closed_by, self.closed_at)
        if self.state is RequestState.PENDING:
            if any(v is not None for v in closed) or self.note:
                raise InvariantError("a pending request is not closed")
        elif any(v is None for v in closed):
            raise InvariantError("a closed request names who closed it and when")
        if self.state is RequestState.DECLINED:
            _check_reason("note", self.note)
        elif self.note:
            raise InvariantError("only a declined request carries a note")
        if self.closed_at is not None and self.closed_at.tzinfo is None:
            raise InvariantError("closed_at must be timezone-aware")

    @property
    def pending(self) -> bool:
        return self.state is RequestState.PENDING
