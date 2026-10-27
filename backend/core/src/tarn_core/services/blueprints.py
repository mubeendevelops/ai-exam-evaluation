"""Exam blueprints (papers and schemas) as college content (D165): the college's teachers
create and edit them (each edit is a new version). Blueprints are never deleted yet: booklets
pin a version, and content rows stay append-only until P28 (D166)."""

from collections.abc import Sequence
from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.content import ContentMeta, Question, Subject
from tarn_core.errors import InvariantError, NotFoundError
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import ContentRepository, UserRepository
from tarn_core.services._support import Runtime, ref_json
from tarn_core.services.blueprint_document import DocumentReport, check_document, require_valid
from tarn_core.services.content import ensure_author


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

    def get(
        self, college_id: CollegeId, blueprint_id: BlueprintId, version: int | None = None
    ) -> ExamBlueprint:
        return self._content.get(college_id, ExamBlueprint, blueprint_id, version)

    def versions(self, college_id: CollegeId, blueprint_id: BlueprintId) -> Sequence[ExamBlueprint]:
        return self._content.versions(college_id, ExamBlueprint, blueprint_id)

    def list(self, college_id: CollegeId) -> Sequence[ExamBlueprint]:
        """The newest version of every blueprint of the college, by title."""
        return sorted(
            self._content.latest(college_id, ExamBlueprint),
            key=lambda b: (b.title.casefold(), str(b.id)),
        )

    def create(self, college_id: CollegeId, actor_id: UserId, document: object) -> ExamBlueprint:
        ensure_author(self._users, college_id, actor_id)
        parsed = require_valid(check_document(document))
        self._require_subject(college_id, parsed.subject_id)
        blueprint = parsed.build(
            id=self._rt.new_id(BlueprintId),
            meta=ContentMeta(college_id=college_id, created_by=actor_id),
        )
        self._require_questions(college_id, blueprint)
        self._content.save(college_id, blueprint)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(blueprint.ref)
        )
        return blueprint

    def update(
        self, college_id: CollegeId, actor_id: UserId, blueprint_id: BlueprintId, document: object
    ) -> ExamBlueprint:
        """Save the document as the next version. Earlier versions stay valid for the
        booklets pinned to them."""
        current = self._content.get(college_id, ExamBlueprint, blueprint_id)
        ensure_author(self._users, college_id, actor_id)
        parsed = require_valid(check_document(document))
        self._require_subject(college_id, parsed.subject_id)
        edited = parsed.build(
            id=current.id,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
        )
        self._require_questions(college_id, edited)
        self._content.save(college_id, edited)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_EDITED,
            before=ref_json(current.ref),
            after=ref_json(edited.ref),
        )
        return edited

    def _require_subject(self, college_id: CollegeId, subject_id: SubjectId) -> None:
        try:
            self._content.get(college_id, Subject, subject_id)
        except NotFoundError:
            raise InvariantError(
                "subject_id: no such subject; pick one from the list or create it first"
            ) from None

    def _require_questions(self, college_id: CollegeId, blueprint: ExamBlueprint) -> None:
        """Every linked question is one of the college's own (content is private, D165)."""
        for slot in blueprint.slots():
            for label, question_id, _ in leaves(slot):
                if question_id is None:
                    continue
                try:
                    self._content.get(college_id, Question, question_id)
                except NotFoundError:
                    raise InvariantError(
                        f"question {label}: the linked question is not in your college's "
                        "question bank"
                    ) from None
