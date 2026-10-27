"""Who edits a college's content (D165, D178): an active SME, and only in the subjects the
admin granted them. Content is private to the college, so there is no owner rule and nothing to
copy between colleges; each edit is a new version, so earlier scores keep the versions they used
(D30)."""

from dataclasses import replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import Question
from tarn_core.domain.personas import CAPABILITIES, Capability
from tarn_core.domain.tenancy import User
from tarn_core.errors import NotFoundError, PermissionDeniedError
from tarn_core.ids import CollegeId, QuestionId, SubjectId, UserId
from tarn_core.ports.repositories import ContentRepository, EntitlementRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json

NOT_AN_AUTHOR = "Only the college's SMEs create and edit questions and papers."


def ensure_author(
    users: UserRepository,
    entitlements: EntitlementRepository,
    college_id: CollegeId,
    actor_id: UserId,
    subject_id: SubjectId,
) -> User:
    """The actor may write this subject's content: an active SME granted the subject. Another
    subject is "not found", like another college's content (D178)."""
    user = users.get(college_id, actor_id)
    if not user.active or Capability.AUTHOR_CONTENT not in CAPABILITIES[user.role]:
        raise PermissionDeniedError(NOT_AN_AUTHOR)
    if subject_id not in entitlements.subjects_of(college_id, actor_id):
        raise NotFoundError(f"subject {subject_id}")
    return user


class ContentService:
    def __init__(
        self,
        *,
        content: ContentRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        runtime: Runtime,
    ):
        self._content = content
        self._users = users
        self._entitlements = entitlements
        self._rt = runtime

    def get_question(
        self, college_id: CollegeId, question_id: QuestionId, version: int | None = None
    ) -> Question:
        return self._content.get(college_id, Question, question_id, version)

    def edit_question(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        *,
        text: str | None = None,
        max_marks: Decimal | None = None,
        category: str | None = None,
    ) -> Question:
        """Save a new version. Earlier versions stay, so earlier scores keep their meaning."""
        current = self._content.get(college_id, Question, question_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, current.subject_id)
        edited = replace(
            current,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
            text=current.text if text is None else text,
            max_marks=current.max_marks if max_marks is None else max_marks,
            category=current.category if category is None else category,
        )
        self._content.save(college_id, edited)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_EDITED,
            before=ref_json(current.ref),
            after=ref_json(edited.ref),
        )
        return edited
