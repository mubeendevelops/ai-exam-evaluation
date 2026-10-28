"""A unit of work (P26, D162): one exam + one student year + one subject, for example
"Half-yearly, 2024 batch, Compiler Theory". One id, one question paper, one SME at a time.
College data: the admin creates it, uploads its booklets, and its owner SME marks them."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from tarn_core.domain.common import JsonValue
from tarn_core.errors import IllegalTransitionError, InvariantError
from tarn_core.ids import (
    AnswerId,
    BlueprintId,
    BookletId,
    CollegeId,
    DelegationRequestId,
    QuestionId,
    StudentId,
    SubjectId,
    UnitId,
    UnitSubmissionId,
    UnitTransferId,
    UserId,
)

UNIT_CODE = re.compile(r"U-\d{4,}")
YEAR_MAX = 20
REASON_MAX = 500
"""Reasons and notes of transfers and delegation requests (P27)."""


class UnitState(StrEnum):
    """D164 (P28): the SME marks an ``open`` unit and submits it whole to the admin; the admin
    may return a ``submitted`` unit (it is ``open`` again: "returned" is that event, kept in
    the unit's submissions); the Governor freezes a submitted unit, after which nothing in it
    changes."""

    OPEN = "open"
    SUBMITTED = "submitted"
    FROZEN = "frozen"


UNIT_TRANSITIONS: Mapping[UnitState, frozenset[UnitState]] = {
    UnitState.OPEN: frozenset({UnitState.SUBMITTED}),
    UnitState.SUBMITTED: frozenset({UnitState.OPEN, UnitState.FROZEN}),
    UnitState.FROZEN: frozenset(),
}
"""Every move a unit may make: submit, return (back to open), freeze."""


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

    @property
    def open(self) -> bool:
        return self.state is UnitState.OPEN

    def moved_to(self, state: UnitState) -> "Unit":
        """The unit in its next state, one version on (``IllegalTransitionError`` for a move
        the state machine does not allow)."""
        if state not in UNIT_TRANSITIONS[self.state]:
            raise IllegalTransitionError(f"a unit cannot move from {self.state} to {state}")
        return replace(self, state=state, version=self.version + 1)


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


# --- submission, return and freeze (P28, D164) ---------------------------------------------


class UnmarkedReason(StrEnum):
    """Why a booklet of a submitted unit was not marked."""

    ABSENT = "absent"
    UNREADABLE = "unreadable"
    OTHER = "other"
    """Needs a note."""


@dataclass(frozen=True, slots=True, kw_only=True)
class UnmarkedBooklet:
    booklet_id: BookletId
    reason: UnmarkedReason
    note: str = ""

    def __post_init__(self) -> None:
        if self.reason is UnmarkedReason.OTHER or self.note:
            _check_reason("note", self.note)


@dataclass(frozen=True, slots=True, kw_only=True)
class UnitSubmission:
    """The SME handed the unit to the admin. The admin may return it with a note (the unit is
    open again; this row keeps the return); the Governor's freeze keeps the last one."""

    id: UnitSubmissionId
    college_id: CollegeId
    unit_id: UnitId
    submitted_by: UserId
    submitted_at: datetime
    unmarked: tuple[UnmarkedBooklet, ...] = ()
    returned_by: UserId | None = None
    returned_at: datetime | None = None
    return_note: str = ""

    def __post_init__(self) -> None:
        if self.submitted_at.tzinfo is None:
            raise InvariantError("submitted_at must be timezone-aware")
        ids = [u.booklet_id for u in self.unmarked]
        if len(set(ids)) != len(ids):
            raise InvariantError("a booklet has one reason")
        returned = (self.returned_by, self.returned_at)
        if any(v is None for v in returned):
            if any(v is not None for v in returned) or self.return_note:
                raise InvariantError("a returned submission names who returned it and when")
        else:
            _check_reason("note", self.return_note)
            if self.returned_at is not None and self.returned_at.tzinfo is None:
                raise InvariantError("returned_at must be timezone-aware")

    @property
    def returned(self) -> bool:
        return self.returned_at is not None


class FrozenOutcome(StrEnum):
    APPROVED = "approved"
    UNMARKED = "unmarked"


@dataclass(frozen=True, slots=True, kw_only=True)
class FrozenSheet:
    version: int
    total: Decimal
    max_marks: Decimal
    issued_by: UserId
    issued_at: datetime
    note: str
    pdf_key: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class FrozenAnswer:
    """One answer slot of a booklet as it stood at the freeze."""

    booklet_id: BookletId
    answer_id: AnswerId
    slot_label: str
    question_id: QuestionId | None
    question_version: int | None
    question_code: str
    max_marks: Decimal
    ai_mark: Decimal | None
    sme_mark: Decimal | None
    """None when the answer was not approved (an unmarked booklet)."""
    reviewer: UserId | None
    reviewed_at: datetime | None
    counted: bool
    """Counted on the last result sheet (best N, OR); False without a sheet."""


@dataclass(frozen=True, slots=True, kw_only=True)
class FrozenBooklet:
    booklet_id: BookletId
    student_id: StudentId
    paper_id: BlueprintId
    paper_version: int
    outcome: FrozenOutcome
    reason: UnmarkedReason | None
    note: str
    total: Decimal | None
    max_marks: Decimal
    sheets: tuple[FrozenSheet, ...]
    answers: tuple[FrozenAnswer, ...]

    def __post_init__(self) -> None:
        if (self.outcome is FrozenOutcome.UNMARKED) != (self.reason is not None):
            raise InvariantError("an unmarked booklet, and only one, has a reason")
        if (self.outcome is FrozenOutcome.APPROVED) != bool(self.sheets):
            raise InvariantError("an approved booklet, and only one, has result sheets")


@dataclass(frozen=True, slots=True, kw_only=True)
class FrozenUnit:
    """What the Governor froze and signed off (D164): the record reports read (D168), kept
    when the paper and its questions are deleted later (D166). College data, insert only."""

    college_id: CollegeId
    unit_id: UnitId
    unit_code: str
    year: str
    subject_id: SubjectId
    subject_code: str
    subject_name: str
    submission_id: UnitSubmissionId
    frozen_by: UserId
    frozen_at: datetime
    papers: tuple[Mapping[str, JsonValue], ...]
    """Each paper version the booklets pin: ``paper_id``, ``version``, ``title``,
    ``exam_code``, ``course_code``, ``total_marks``, ``mark_step``, ``document`` (the
    blueprint document with its sections, slots and choice rules) and ``questions``
    (id, version, code of each linked question)."""
    booklets: tuple[FrozenBooklet, ...]

    def __post_init__(self) -> None:
        if self.frozen_at.tzinfo is None:
            raise InvariantError("frozen_at must be timezone-aware")

    def paper(self, paper_id: BlueprintId, version: int | None = None) -> Mapping[str, JsonValue]:
        """The snapshot of one paper version (the newest frozen one when ``version`` is
        None)."""
        found = [
            p
            for p in self.papers
            if p.get("paper_id") == str(paper_id)
            and (version is None or p.get("version") == version)
        ]
        if not found:
            raise InvariantError(f"paper {paper_id} is not in the frozen record")
        return max(found, key=lambda p: int(str(p.get("version", 0))))
