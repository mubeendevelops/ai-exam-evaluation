"""Exam blueprints as global content (design decision 3): any teacher creates one for the
college; only the owning college's teachers edit it (each edit is a new version); everyone else
copies it. Blueprints are never deleted: booklets pin a version, and global rows are
append-only (D35)."""

from collections.abc import Sequence
from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.content import ContentMeta, Subject
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import ContentRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json
from tarn_core.services.blueprint_document import DocumentReport, check_document, require_valid
from tarn_core.services.content import ensure_can_edit


class BlueprintService:
    def __init__(self, *, content: ContentRepository, users: UserRepository, runtime: Runtime):
        self._content = content
        self._users = users
        self._rt = runtime

    @staticmethod
    def check(document: object) -> DocumentReport:
        """Every problem of a document, with totals computed under the choice rules. Writes
        nothing; does not look up the subject."""
        return check_document(document)

    def get(self, blueprint_id: BlueprintId, version: int | None = None) -> ExamBlueprint:
        return self._content.get(ExamBlueprint, blueprint_id, version)

    def versions(self, blueprint_id: BlueprintId) -> Sequence[ExamBlueprint]:
        return self._content.versions(ExamBlueprint, blueprint_id)

    def list(self) -> Sequence[ExamBlueprint]:
        """The newest version of every blueprint, from every college, by title."""
        return sorted(
            self._content.latest(ExamBlueprint), key=lambda b: (b.title.casefold(), str(b.id))
        )

    def create(self, college_id: CollegeId, actor_id: UserId, document: object) -> ExamBlueprint:
        self._users.get(college_id, actor_id)
        parsed = require_valid(check_document(document))
        self._require_subject(parsed.subject_id)
        blueprint = parsed.build(
            id=self._rt.new_id(BlueprintId),
            meta=ContentMeta(owning_college_id=college_id, created_by=actor_id),
        )
        self._content.save(blueprint)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(blueprint.ref)
        )
        return blueprint

    def update(
        self, college_id: CollegeId, actor_id: UserId, blueprint_id: BlueprintId, document: object
    ) -> ExamBlueprint:
        """Save the document as the next version. Earlier versions stay valid for the
        booklets pinned to them."""
        self._users.get(college_id, actor_id)
        current = self._content.get(ExamBlueprint, blueprint_id)
        ensure_can_edit(college_id, current)
        parsed = require_valid(check_document(document))
        self._require_subject(parsed.subject_id)
        edited = parsed.build(
            id=current.id,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
        )
        self._content.save(edited)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_EDITED,
            before=ref_json(current.ref),
            after=ref_json(edited.ref),
        )
        return edited

    def copy(
        self, college_id: CollegeId, actor_id: UserId, blueprint_id: BlueprintId
    ) -> ExamBlueprint:
        """Copy any blueprint, including the caller's own, into a new one owned by the
        caller's college. Its question links stay: questions are global."""
        self._users.get(college_id, actor_id)
        source = self._content.get(ExamBlueprint, blueprint_id)
        copy = replace(
            source,
            id=self._rt.new_id(BlueprintId),
            meta=ContentMeta(
                owning_college_id=college_id, created_by=actor_id, copied_from=source.ref
            ),
        )
        self._content.save(copy)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_COPIED,
            before=ref_json(source.ref),
            after=ref_json(copy.ref),
        )
        return copy

    def _require_subject(self, subject_id: SubjectId) -> None:
        try:
            self._content.get(Subject, subject_id)
        except NotFoundError:
            raise InvariantError(
                "subject_id: no such subject; pick one from the list or create it first"
            ) from None
