"""Who edits a college's content (D165): the college's active teachers. Content is private to
the college, so there is no owner rule and nothing to copy between colleges; each edit is a new
version, so earlier scores keep the versions they used (D30). P24 narrows authoring to the
subjects an SME is entitled to."""

from dataclasses import replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import Question
from tarn_core.domain.tenancy import Role, User
from tarn_core.errors import PermissionDeniedError
from tarn_core.ids import CollegeId, QuestionId, UserId
from tarn_core.ports.repositories import ContentRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json

NOT_AN_AUTHOR = "Only the college's teachers create and edit questions and papers."


def ensure_author(users: UserRepository, college_id: CollegeId, actor_id: UserId) -> User:
    """The actor may write the college's content: an active user who is not the admin."""
    user = users.get(college_id, actor_id)
    if not user.active or user.role is Role.ADMIN:
        raise PermissionDeniedError(NOT_AN_AUTHOR)
    return user


class ContentService:
    def __init__(self, *, content: ContentRepository, users: UserRepository, runtime: Runtime):
        self._content = content
        self._users = users
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
        ensure_author(self._users, college_id, actor_id)
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
