"""Edit rights on global content (design decision 3): teachers of the owning college edit
it, creating a new version; teachers elsewhere copy it to their own college."""

from dataclasses import replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.content import (
    MAX_CODE_LENGTH,
    ContentMeta,
    DiagramParams,
    Glossary,
    KeyFile,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
)
from tarn_core.errors import NotOwnerError
from tarn_core.ids import (
    CollegeId,
    CriterionId,
    GlossaryId,
    KeyFileId,
    QuestionId,
    ReferenceAnswerId,
    ReferenceDiagramId,
    UserId,
)
from tarn_core.ports.repositories import (
    ContentRepository,
    GlobalItem,
    QuestionQuery,
    UserRepository,
)
from tarn_core.services._support import Runtime, ref_json


def ensure_can_edit(college_id: CollegeId, item: GlobalItem) -> None:
    if item.meta.owning_college_id != college_id:
        raise NotOwnerError(
            f"{type(item).__name__} {item.id} belongs to another college; copy it first"
        )


def free_code(content: ContentRepository, college_id: CollegeId, code: str) -> str:
    """``code`` if no question of this college has it, else ``code-2``, ``code-3``..."""
    query = QuestionQuery(code=code, exact_code=True, owner_id=college_id)
    if content.search_questions(query, limit=1).total == 0:
        return code
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = f"{code[: MAX_CODE_LENGTH - len(suffix)]}{suffix}"
        query = QuestionQuery(code=candidate, exact_code=True, owner_id=college_id)
        if content.search_questions(query, limit=1).total == 0:
            return candidate
        n += 1


class ContentService:
    def __init__(self, *, content: ContentRepository, users: UserRepository, runtime: Runtime):
        self._content = content
        self._users = users
        self._rt = runtime

    def get_question(self, question_id: QuestionId, version: int | None = None) -> Question:
        """Global content: readable from every college."""
        return self._content.get(Question, question_id, version)

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
        self._users.get(college_id, actor_id)
        current = self._content.get(Question, question_id)
        ensure_can_edit(college_id, current)
        edited = replace(
            current,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
            text=current.text if text is None else text,
            max_marks=current.max_marks if max_marks is None else max_marks,
            category=current.category if category is None else category,
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

    def copy_question(
        self, college_id: CollegeId, actor_id: UserId, question_id: QuestionId
    ) -> Question:
        """Copy a question with its keys, rubric, glossary and reference diagrams into a new
        item owned by ``college_id``. Works for any source, including the caller's own."""
        self._users.get(college_id, actor_id)
        source = self._content.get(Question, question_id)
        copy = replace(
            source,
            id=self._rt.new_id(QuestionId),
            meta=self._new_meta(college_id, actor_id, source),
            code=free_code(self._content, college_id, source.code),
        )
        self._content.save(copy)

        diagram_ids: dict[ReferenceDiagramId, ReferenceDiagramId] = {}
        for diagram in self._content.for_question(ReferenceDiagram, question_id):
            new_diagram = replace(
                diagram,
                id=self._rt.new_id(ReferenceDiagramId),
                meta=self._new_meta(college_id, actor_id, diagram),
                question_id=copy.id,
            )
            diagram_ids[diagram.id] = new_diagram.id
            self._content.save(new_diagram)
        for answer in self._content.for_question(ReferenceAnswer, question_id):
            self._content.save(
                replace(
                    answer,
                    id=self._rt.new_id(ReferenceAnswerId),
                    meta=self._new_meta(college_id, actor_id, answer),
                    question_id=copy.id,
                )
            )
        for criterion in self._content.for_question(RubricCriterion, question_id):
            params = criterion.params
            if isinstance(params, DiagramParams):
                mapped = diagram_ids.get(params.reference_diagram_id, params.reference_diagram_id)
                params = replace(params, reference_diagram_id=mapped)
            self._content.save(
                replace(
                    criterion,
                    id=self._rt.new_id(CriterionId),
                    meta=self._new_meta(college_id, actor_id, criterion),
                    question_id=copy.id,
                    params=params,
                )
            )
        for key_file in self._content.for_question(KeyFile, question_id):
            # Blobs are global and never deleted, so the copy shares the stored file.
            self._content.save(
                replace(
                    key_file,
                    id=self._rt.new_id(KeyFileId),
                    meta=self._new_meta(college_id, actor_id, key_file),
                    question_id=copy.id,
                )
            )
        for glossary in self._content.for_question(Glossary, question_id):
            self._content.save(
                replace(
                    glossary,
                    id=self._rt.new_id(GlossaryId),
                    meta=self._new_meta(college_id, actor_id, glossary),
                    question_id=copy.id,
                )
            )

        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_COPIED,
            before=ref_json(source.ref),
            after=ref_json(copy.ref),
        )
        return copy

    @staticmethod
    def _new_meta(
        college_id: CollegeId,
        actor_id: UserId,
        source: Question
        | ReferenceAnswer
        | RubricCriterion
        | Glossary
        | ReferenceDiagram
        | KeyFile,
    ) -> ContentMeta:
        return ContentMeta(
            version=1, owning_college_id=college_id, created_by=actor_id, copied_from=source.ref
        )
