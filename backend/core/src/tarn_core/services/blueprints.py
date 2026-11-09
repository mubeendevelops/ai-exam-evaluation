"""Exam blueprints (papers and schemas) as college content (D165): the college's SMEs create
and edit them in their own subjects (each edit is a new version; D178). Blueprints are never
deleted here: booklets pin a version; a paper is deleted for good only through
``services.content_deletion`` once every unit using it is frozen (P28, D166).

Since P33 (D219, D220, D231) a blueprint is a schema or a paper; its kind never changes. A
schema can be cloned (a new blueprint, not a version) and generates paper drafts that name it
in ``from_schema``."""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import (
    BlueprintKind,
    BlueprintQuery,
    BlueprintSummary,
    ExamBlueprint,
    leaves,
)
from tarn_core.domain.content import ContentMeta, Question, Subject
from tarn_core.errors import (
    AlreadyExistsError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
)
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UserId
from tarn_core.ports.repositories import (
    ContentRepository,
    EntitlementRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime, ref_json
from tarn_core.services.access import Reach
from tarn_core.services.blueprint_document import DocumentReport, check_document, require_valid
from tarn_core.services.content import ensure_author

COPY_SUFFIX = " (copy)"
PAPER_SUFFIX = " paper"
_MAX_TITLE = 200


def _titled(base: str, suffix: str) -> str:
    """``base`` + ``suffix`` within the title limit (the base is shortened, not the suffix)."""
    return base[: _MAX_TITLE - len(suffix)].rstrip() + suffix


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaListing:
    """Manage Schema's cards and the batches its filter offers (every schema in reach)."""

    items: Sequence[BlueprintSummary]
    batches: Sequence[str]


class BlueprintService:
    def __init__(
        self,
        *,
        content: ContentRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        runtime: Runtime,
        units: UnitRepository,
    ):
        self._units = units
        self._content = content
        self._users = users
        self._entitlements = entitlements
        self._rt = runtime

    @staticmethod
    def check(document: object) -> DocumentReport:
        """Every problem of a document, with totals computed under the choice rules. Writes
        nothing; does not look up the subject."""
        return check_document(document)

    # Reads take the reader's ``reach``: a paper of another subject is "not found" (D178).

    def get(
        self,
        college_id: CollegeId,
        blueprint_id: BlueprintId,
        version: int | None = None,
        *,
        reach: Reach,
    ) -> ExamBlueprint:
        found = self._content.get(college_id, ExamBlueprint, blueprint_id, version)
        reach.check(found.subject_id, f"blueprint {blueprint_id}")
        return found

    def versions(
        self, college_id: CollegeId, blueprint_id: BlueprintId, *, reach: Reach
    ) -> Sequence[ExamBlueprint]:
        found = self._content.versions(college_id, ExamBlueprint, blueprint_id)
        if found:
            reach.check(found[-1].subject_id, f"blueprint {blueprint_id}")
        return found

    def list(
        self, college_id: CollegeId, *, reach: Reach, kind: BlueprintKind | None = None
    ) -> Sequence[ExamBlueprint]:
        """The newest version of every blueprint of the college in reach, by title."""
        return sorted(
            (
                b
                for b in self._content.latest(college_id, ExamBlueprint)
                if reach.sees(b.subject_id) and (kind is None or b.kind is kind)
            ),
            key=lambda b: (b.title.casefold(), str(b.id)),
        )

    def schemas(
        self, college_id: CollegeId, *, reach: Reach, title: str = "", target_batch: str = ""
    ) -> SchemaListing:
        """Manage Schema (P33): the schemas in reach matching the filters, by title, each with
        the number of papers made from it; and the batches in use for the filter."""
        items = self._content.blueprint_summaries(
            college_id,
            BlueprintQuery(
                kind=BlueprintKind.SCHEMA,
                subject_ids=reach.subjects,
                title=title.strip(),
                target_batch=target_batch.strip(),
            ),
        )
        batches = self._content.target_batches(
            college_id, BlueprintKind.SCHEMA, subject_ids=reach.subjects
        )
        return SchemaListing(
            items=sorted(items, key=lambda b: (b.title.casefold(), str(b.id))), batches=batches
        )

    def create(self, college_id: CollegeId, actor_id: UserId, document: object) -> ExamBlueprint:
        parsed = require_valid(check_document(document))
        self._require_subject(college_id, parsed.subject_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, parsed.subject_id)
        blueprint = parsed.build(
            id=self._rt.new_id(BlueprintId),
            meta=ContentMeta(college_id=college_id, created_by=actor_id),
        )
        return self._add(college_id, actor_id, blueprint)

    def clone(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        blueprint_id: BlueprintId,
        *,
        title: str | None = None,
        target_batch: str | None = None,
        weightage_note: str | None = None,
    ) -> ExamBlueprint:
        """A new blueprint (version 1, not a version of the source) with the source's content,
        titled "<title> (copy)" unless ``title`` is given. A paper's copy has no exam code (it
        names one paper) and keeps the schema it was made from."""
        source = self._content.get(college_id, ExamBlueprint, blueprint_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, source.subject_id)
        self._require_subject(college_id, source.subject_id)
        copy = replace(
            source,
            id=self._rt.new_id(BlueprintId),
            meta=ContentMeta(college_id=college_id, created_by=actor_id),
            title=_text_or(title, _titled(source.title, COPY_SUFFIX)),
            exam_code="",
            target_batch=source.target_batch if target_batch is None else target_batch.strip(),
            weightage_note=(
                source.weightage_note if weightage_note is None else weightage_note.strip()
            ),
            from_schema=(
                source.from_schema
                if self._is_schema_of(college_id, source.from_schema, source.subject_id)
                else None
            ),
        )
        return self._add(college_id, actor_id, copy, source=source)

    def generate_paper(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        schema_id: BlueprintId,
        *,
        title: str | None = None,
    ) -> ExamBlueprint:
        """A paper draft from a schema (D220): its sections, rules, marks and header, every
        slot unlinked, no exam code yet, ``from_schema`` naming it."""
        schema = self._content.get(college_id, ExamBlueprint, schema_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, schema.subject_id)
        if schema.kind is not BlueprintKind.SCHEMA:
            raise IllegalTransitionError("Only a schema generates a paper; this is a paper.")
        self._require_subject(college_id, schema.subject_id)
        paper = replace(
            schema,
            id=self._rt.new_id(BlueprintId),
            meta=ContentMeta(college_id=college_id, created_by=actor_id),
            kind=BlueprintKind.PAPER,
            title=_text_or(title, _titled(schema.title, PAPER_SUFFIX)),
            from_schema=schema.id,
        )
        return self._add(college_id, actor_id, paper, source=schema)

    def _add(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        blueprint: ExamBlueprint,
        *,
        source: ExamBlueprint | None = None,
    ) -> ExamBlueprint:
        """Check and save a new blueprint (version 1) and audit it."""
        blueprint = replace(blueprint, saved_at=self._rt.clock.now())
        self._require_questions(college_id, blueprint)
        self._require_free_exam_code(college_id, blueprint)
        self._require_from_schema(college_id, blueprint)
        self._content.save(college_id, blueprint)
        after = ref_json(blueprint.ref)
        if source is not None and isinstance(after, dict):
            after["from"] = ref_json(source.ref)
        self._rt.record(college_id, actor_id, AuditAction.CONTENT_CREATED, after=after)
        return blueprint

    def update(
        self, college_id: CollegeId, actor_id: UserId, blueprint_id: BlueprintId, document: object
    ) -> ExamBlueprint:
        """Save the document as the next version. Earlier versions stay valid for the
        booklets pinned to them."""
        current = self._content.get(college_id, ExamBlueprint, blueprint_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, current.subject_id)
        parsed = require_valid(check_document(document))
        self._require_subject(
            college_id, parsed.subject_id, new_paper=parsed.subject_id != current.subject_id
        )
        if parsed.subject_id != current.subject_id:
            if any(
                u.paper_id == current.id
                for u in self._units.list(college_id, subject_id=current.subject_id)
            ):
                raise IllegalTransitionError(
                    "subject_id: a unit uses this paper, so its subject cannot change"
                )
            ensure_author(self._users, self._entitlements, college_id, actor_id, parsed.subject_id)
        if parsed.kind is not None and parsed.kind is not current.kind:
            raise InvariantError(
                f"kind: this blueprint is a {current.kind.value} and stays one; "
                "generate a paper from a schema, or clone, instead"
            )
        edited = replace(
            parsed.build(
                id=current.id,
                meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
                kind=current.kind,
            ),
            saved_at=self._rt.clock.now(),
        )
        self._require_questions(college_id, edited)
        self._require_free_exam_code(college_id, edited)
        if edited.from_schema != current.from_schema or edited.subject_id != current.subject_id:
            # Unchanged, it may name a schema deleted since (D231): the paper keeps it.
            self._require_from_schema(college_id, edited)
        self._content.save(college_id, edited)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_EDITED,
            before=ref_json(current.ref),
            after=ref_json(edited.ref),
        )
        return edited

    def _require_subject(
        self, college_id: CollegeId, subject_id: SubjectId, *, new_paper: bool = True
    ) -> None:
        """The subject exists; a retired one takes no new paper (D181), though the papers it
        already has may still be edited."""
        try:
            subject = self._content.get(college_id, Subject, subject_id)
        except NotFoundError:
            raise InvariantError(
                "subject_id: no such subject; pick one from the list or ask your admin"
            ) from None
        if subject.retired and new_paper:
            raise InvariantError("subject_id: the subject is retired; papers cannot use it")

    def _require_free_exam_code(self, college_id: CollegeId, blueprint: ExamBlueprint) -> None:
        """An exam code names one paper of the college (P26): unique, ignoring case, among the
        newest versions of the college's blueprints that carry one."""
        code = blueprint.exam_code.casefold()
        if not code:
            return
        for other in self._content.latest(college_id, ExamBlueprint):
            if other.id != blueprint.id and other.exam_code.casefold() == code:
                raise AlreadyExistsError(
                    f"exam_code: {blueprint.exam_code} is taken by another paper of your college"
                )

    def _require_from_schema(self, college_id: CollegeId, blueprint: ExamBlueprint) -> None:
        """A paper's ``from_schema`` names a schema of the same college and subject."""
        if blueprint.from_schema is not None and not self._is_schema_of(
            college_id, blueprint.from_schema, blueprint.subject_id
        ):
            raise InvariantError(
                "from_schema: not a schema of this subject in your college; leave it empty"
            )

    def _is_schema_of(
        self, college_id: CollegeId, schema_id: BlueprintId | None, subject_id: SubjectId
    ) -> bool:
        if schema_id is None:
            return False
        try:
            schema = self._content.get(college_id, ExamBlueprint, schema_id)
        except NotFoundError:
            return False
        return schema.kind is BlueprintKind.SCHEMA and schema.subject_id == subject_id

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


def _text_or(value: str | None, default: str) -> str:
    """A caller's title, trimmed; the default when absent or blank."""
    if value is None or not value.strip():
        return default
    if len(value.strip()) > _MAX_TITLE:
        raise InvariantError(f"title: at most {_MAX_TITLE} characters")
    return value.strip()
