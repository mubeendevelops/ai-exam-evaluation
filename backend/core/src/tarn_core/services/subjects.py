"""Subjects: the global list a blueprint (and later a question) belongs to. P6 needs only
listing and creating; the question bank (P7) builds on it."""

from collections.abc import Sequence

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import ContentMeta, Subject
from tarn_core.ids import CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import ContentRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json


class SubjectService:
    def __init__(self, *, content: ContentRepository, users: UserRepository, runtime: Runtime):
        self._content = content
        self._users = users
        self._rt = runtime

    def list(self) -> Sequence[Subject]:
        return sorted(self._content.latest(Subject), key=lambda s: (s.name.casefold(), s.code))

    def create(self, college_id: CollegeId, actor_id: UserId, *, code: str, name: str) -> Subject:
        self._users.get(college_id, actor_id)
        subject = Subject(
            id=self._rt.new_id(SubjectId),
            meta=ContentMeta(owning_college_id=college_id, created_by=actor_id),
            code=code.strip(),
            name=name.strip(),
        )
        self._content.save(subject)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(subject.ref)
        )
        return subject
