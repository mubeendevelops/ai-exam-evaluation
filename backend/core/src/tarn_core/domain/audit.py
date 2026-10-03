"""Append-only audit events (rule 6). Never put secrets in ``before``/``after``.
A deletion event carries no content: who, when and which booklet only (D14)."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tarn_core.domain.booklet import check_aware
from tarn_core.domain.common import JsonValue
from tarn_core.errors import InvariantError
from tarn_core.ids import AnswerId, AuditEventId, BookletId, CollegeId, UserId


class AuditAction(StrEnum):
    BOOKLET_REGISTERED = "booklet.registered"
    BOOKLET_DELETED = "booklet.deleted"
    CONTENT_EDITED = "content.edited"
    CONTENT_COPIED = "content.copied"
    ANSWER_SCORED = "answer.scored"
    ANSWER_APPROVED = "answer.approved"
    RESULT_SHEET_ISSUED = "result_sheet.issued"


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEvent:
    id: AuditEventId
    college_id: CollegeId
    actor_id: UserId
    at: datetime
    action: AuditAction
    booklet_id: BookletId | None = None
    answer_id: AnswerId | None = None
    before: JsonValue = None
    after: JsonValue = None

    def __post_init__(self) -> None:
        check_aware("audit time", self.at)
        if self.action is AuditAction.BOOKLET_DELETED:
            if self.booklet_id is None:
                raise InvariantError("a deletion record names the booklet")
            if self.before is not None or self.after is not None:
                raise InvariantError("a deletion record holds no content")
