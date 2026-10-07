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
    BOOKLET_PROCESSED = "booklet.processed"  # by the page pipeline: no acting user
    BOOKLET_FAILED = "booklet.failed"  # by the page pipeline: no acting user
    BOOKLET_TEXT_READ = "booklet.text_read"  # by the OCR reader: no acting user
    BOOKLET_SEGMENTED = "booklet.segmented"  # by segmentation: no acting user
    SEGMENT_EDITED = "segment.edited"  # a teacher's merge, split, reassign or boundary move
    DIAGRAM_RECOGNISED = "diagram.recognised"  # a student drawing read by the pipeline
    DIAGRAM_EDITED = "diagram.edited"  # a teacher's correction of a student drawing's graph
    PAGE_USED_ANYWAY = "page.used_anyway"
    CONTENT_CREATED = "content.created"
    CONTENT_EDITED = "content.edited"
    CONTENT_COPIED = "content.copied"
    CONTENT_RETIRED = "content.retired"
    ANSWER_SCORED = "answer.scored"
    BOOKLET_SCORED = "booklet.scored"  # by scoring: no acting user
    # The teacher's review (P15)
    BOOKLET_OPENED = "booklet.opened"  # lock taken (SCORED → IN_REVIEW on the first open)
    BOOKLET_CLOSED = "booklet.closed"  # lock released
    REGION_EDITED = "region.edited"  # an OCR line corrected or struck out (no text here)
    ANSWER_RESCORE_REQUESTED = "answer.rescore_requested"
    ANSWER_SKIPPED = "answer.skipped"
    ANSWER_APPROVED = "answer.approved"
    ANSWER_REOPENED = "answer.reopened"
    AMENDMENT_OPENED = "amendment.opened"
    AMENDMENT_WITHDRAWN = "amendment.withdrawn"
    BOOKLET_APPROVED = "booklet.approved"
    RESULT_SHEET_ISSUED = "result_sheet.issued"
    # Authentication (P4)
    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOCKED_OUT = "auth.locked_out"
    LOGOUT = "auth.logout"
    SESSION_REUSED = "auth.session_reused"
    RESET_REQUESTED = "auth.reset_requested"
    PASSWORD_RESET = "auth.password_reset"  # noqa: S105  (an action name)
    PASSWORD_CHANGED = "auth.password_changed"  # noqa: S105
    RECOVERY_CODES_ISSUED = "auth.recovery_codes_issued"
    RECOVERY_CODE_USED = "auth.recovery_code_used"
    RECOVERY_FAILED = "auth.recovery_failed"
    # Tenants and accounts (P4)
    TENANT_REGISTERED = "tenant.registered"
    TENANT_EMAIL_VERIFIED = "tenant.email_verified"
    TENANT_APPROVED = "tenant.approved"
    ACCOUNT_CREATED = "account.created"
    ACCOUNT_ACTIVATED = "account.activated"
    ACCOUNT_DISABLED = "account.disabled"
    ACCOUNT_ENABLED = "account.enabled"
    ACCOUNT_FORCE_RESET = "account.force_reset"
    ACCOUNT_UNLOCKED = "account.unlocked"
    ROSTER_IMPORTED = "roster.imported"
    # College settings (P19)
    COLLEGE_LLM_SCORING_SET = "college.llm_scoring_set"  # a Tarn operator switches the flag


# Events that may have no acting user: a failed sign-in for an unknown email, the approval of
# a tenant by a Tarn operator (who is not a user of any college), and what the page pipeline
# does on its own (cleaning, reading, segmenting, scoring).
ANONYMOUS_ACTIONS = frozenset(
    {
        AuditAction.LOGIN_FAILED,
        AuditAction.TENANT_APPROVED,
        AuditAction.COLLEGE_LLM_SCORING_SET,
        AuditAction.BOOKLET_PROCESSED,
        AuditAction.BOOKLET_FAILED,
        AuditAction.BOOKLET_TEXT_READ,
        AuditAction.BOOKLET_SEGMENTED,
        AuditAction.BOOKLET_SCORED,
        AuditAction.DIAGRAM_RECOGNISED,
        AuditAction.ANSWER_SCORED,  # scored by the pipeline; a teacher's re-score names them
    }
)

# A before/after key containing one of these words is refused: events never hold secrets.
_SECRET_WORDS = ("password", "secret", "token", "pepper", "hash", "recovery_code")


def _check_no_secrets(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if any(word in key.lower() for word in _SECRET_WORDS):
                raise InvariantError(f"audit events hold no secrets (key {key!r})")
            _check_no_secrets(inner)
    elif isinstance(value, list):
        for inner in value:
            _check_no_secrets(inner)


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEvent:
    id: AuditEventId
    college_id: CollegeId
    actor_id: UserId | None
    at: datetime
    action: AuditAction
    booklet_id: BookletId | None = None
    answer_id: AnswerId | None = None
    before: JsonValue = None
    after: JsonValue = None

    def __post_init__(self) -> None:
        check_aware("audit time", self.at)
        if self.actor_id is None and self.action not in ANONYMOUS_ACTIONS:
            raise InvariantError(f"{self.action} needs an acting user")
        _check_no_secrets(self.before)
        _check_no_secrets(self.after)
        if self.action is AuditAction.BOOKLET_DELETED:
            if self.booklet_id is None:
                raise InvariantError("a deletion record names the booklet")
            if self.before is not None or self.after is not None:
                raise InvariantError("a deletion record holds no content")
