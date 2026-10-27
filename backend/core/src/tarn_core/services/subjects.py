"""Subjects: the college's list that papers and questions belong to (private to the college
since P23, D165). Any active user of the college adds one for now; P24 gives the list to the
admin (D160)."""

from collections.abc import Sequence

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import ContentMeta, Subject
from tarn_core.errors import PermissionDeniedError
from tarn_core.ids import CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import ContentRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json


class SubjectService:
    def __init__(self, *, content: ContentRepository, users: UserRepository, runtime: Runtime):
        self._content = content
        self._users = users
        self._rt = runtime

    def list(self, college_id: CollegeId) -> Sequence[Subject]:
        return sorted(
            self._content.latest(college_id, Subject), key=lambda s: (s.name.casefold(), s.code)
        )

    def create(self, college_id: CollegeId, actor_id: UserId, *, code: str, name: str) -> Subject:
        if not self._users.get(college_id, actor_id).active:
            raise PermissionDeniedError("This account is disabled.")
        subject = Subject(
            id=self._rt.new_id(SubjectId),
            meta=ContentMeta(college_id=college_id, created_by=actor_id),
            code=code.strip(),
            name=name.strip(),
        )
        self._content.save(college_id, subject)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(subject.ref)
        )
        return subject
