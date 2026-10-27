"""Subjects: the college's list that papers and questions belong to (private to the college,
D165). The admin keeps the list (D160): creates, renames and retires subjects, each change a
new version, so scores keep naming the versions they used. SMEs see the subjects granted to
them (D178)."""

from collections.abc import Sequence
from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import ContentMeta, Subject
from tarn_core.domain.personas import Capability, can
from tarn_core.errors import AlreadyExistsError, InvariantError
from tarn_core.ids import CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import ContentRepository, EntitlementRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json
from tarn_core.services.access import Reach, reach_of, require


class SubjectService:
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

    def all(self, college_id: CollegeId) -> Sequence[Subject]:
        """Every subject of the college, retired ones too, by name (the admin's list and
        operator tools)."""
        return sorted(
            self._content.latest(college_id, Subject), key=lambda s: (s.name.casefold(), s.code)
        )

    def list(self, college_id: CollegeId, *, reach: Reach) -> Sequence[Subject]:
        """What a user picks from: the subjects in reach, without the retired ones."""
        return [s for s in self.all(college_id) if reach.sees(s.id) and not s.retired]

    def visible_to(self, college_id: CollegeId, actor_id: UserId) -> Sequence[Subject]:
        """The admin sees the whole list, retired subjects included; an SME the live subjects
        granted to them."""
        user = self._users.get(college_id, actor_id)
        if can(user, Capability.MANAGE_SUBJECTS):
            return self.all(college_id)
        return self.list(college_id, reach=reach_of(user, self._entitlements))

    def create(self, college_id: CollegeId, actor_id: UserId, *, code: str, name: str) -> Subject:
        self._admin(college_id, actor_id)
        code = code.strip()
        self._require_free_code(college_id, code, None)
        subject = Subject(
            id=self._rt.new_id(SubjectId),
            meta=ContentMeta(college_id=college_id, created_by=actor_id),
            code=code,
            name=name.strip(),
        )
        self._content.save(college_id, subject)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(subject.ref)
        )
        return subject

    def rename(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        subject_id: SubjectId,
        *,
        code: str,
        name: str,
    ) -> Subject:
        """The next version with the new code and name."""
        current = self._content.get(college_id, Subject, subject_id)
        self._admin(college_id, actor_id)
        code = code.strip()
        self._require_free_code(college_id, code, subject_id)
        if current.retired:
            raise InvariantError("The subject is retired.")
        return self._next(college_id, actor_id, current, code=code, name=name.strip())

    def retire(self, college_id: CollegeId, actor_id: UserId, subject_id: SubjectId) -> Subject:
        """A retired subject cannot be granted or used for a new paper (D181). Its grants,
        questions and papers stay."""
        current = self._content.get(college_id, Subject, subject_id)
        self._admin(college_id, actor_id)
        if current.retired:
            return current
        return self._next(college_id, actor_id, current, retired=True)

    def _next(
        self, college_id: CollegeId, actor_id: UserId, current: Subject, **changes: object
    ) -> Subject:
        edited = replace(
            current,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
            **changes,  # type: ignore[arg-type]  # dataclass fields, checked by __post_init__
        )
        self._content.save(college_id, edited)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_RETIRED if edited.retired else AuditAction.CONTENT_EDITED,
            before=ref_json(current.ref),
            after=ref_json(edited.ref),
        )
        return edited

    def _admin(self, college_id: CollegeId, actor_id: UserId) -> None:
        require(self._users.get(college_id, actor_id), Capability.MANAGE_SUBJECTS)

    def _require_free_code(self, college_id: CollegeId, code: str, keep: SubjectId | None) -> None:
        wanted = code.casefold()
        if any(
            s.code.casefold() == wanted and s.id != keep
            for s in self._content.latest(college_id, Subject)
        ):
            raise AlreadyExistsError(f"Your college already has a subject with code {code!r}.")
