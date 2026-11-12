"""Exam blueprints (papers and schemas) as college content (D165): the college's SMEs create
and edit them in their own subjects (each edit is a new version; D178). Blueprints are never
deleted here: booklets pin a version; a paper is deleted for good only through
``services.content_deletion`` once every unit using it is frozen (P28, D166).

Since P33 (D219, D220, D231) a blueprint is a schema or a paper; its kind never changes. A
schema can be cloned (a new blueprint, not a version) and generates paper drafts that name it
in ``from_schema``.

Since P34 (D222) a paper's structure is locked once a booklet is pinned to any of its
versions (``blueprint_lock``); Manage Question Papers lists papers a page at a time and
creates them from a starter or a schema; every link must name a question of the paper's
subject with the leaf's marks."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import (
    BlueprintKind,
    BlueprintQuery,
    BlueprintSummary,
    ExamBlueprint,
    QuestionSlot,
    Section,
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
    BookletRepository,
    ContentRepository,
    EntitlementRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime, ref_json
from tarn_core.services.access import Reach
from tarn_core.services.blueprint_document import DocumentReport, check_document, require_valid
from tarn_core.services.blueprint_lock import LOCKED_MESSAGE, structure_changes
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


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperListing:
    """One page of Manage Question Papers (P34): the matching papers, how many match in all,
    the descriptors in use (every paper in reach, for the filter and the suggestions) and which
    of the page's papers a unit uses."""

    items: Sequence[BlueprintSummary]
    total: int
    descriptors: Sequence[str]
    in_use: frozenset[BlueprintId]


STARTER_QUESTIONS = 5
STARTER_MARKS = Decimal(2)


def starter_paper(
    blueprint_id: BlueprintId, meta: ContentMeta, subject: Subject, *, title: str, descriptor: str
) -> ExamBlueprint:
    """A new paper to start from (P34): section A of five unlinked 2-mark questions, the
    subject's code as the course code."""
    slots = tuple(
        QuestionSlot(label=str(n), marks=STARTER_MARKS) for n in range(1, STARTER_QUESTIONS + 1)
    )
    return ExamBlueprint(
        id=blueprint_id,
        meta=meta,
        subject_id=subject.id,
        title=title,
        total_marks=STARTER_MARKS * STARTER_QUESTIONS,
        sections=(Section(label="A", title="Section A", items=slots),),
        course_code=subject.code[:50],
        kind=BlueprintKind.PAPER,
        descriptor=descriptor,
    )


class BlueprintService:
    def __init__(
        self,
        *,
        content: ContentRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        runtime: Runtime,
        units: UnitRepository,
        booklets: BookletRepository,
    ):
        self._units = units
        self._booklets = booklets
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

    def papers(
        self,
        college_id: CollegeId,
        *,
        reach: Reach,
        search: str = "",
        descriptor: str = "",
        created_on: date | None = None,
        limit: int = 12,
        offset: int = 0,
    ) -> PaperListing:
        """Manage Question Papers (P34): papers in reach whose title or descriptor contains
        ``search``, with exactly ``descriptor``, first saved on ``created_on`` (UTC), most
        recently saved first, one page of them."""
        page = self._content.blueprint_page(
            college_id,
            BlueprintQuery(
                kind=BlueprintKind.PAPER,
                subject_ids=reach.subjects,
                search=search.strip(),
                descriptor=descriptor.strip(),
                created_on=created_on,
            ),
            limit=limit,
            offset=offset,
        )
        used = {u.paper_id for u in self._units.list(college_id)}
        return PaperListing(
            items=page.items,
            total=page.total,
            descriptors=self._content.descriptors(
                college_id, BlueprintKind.PAPER, subject_ids=reach.subjects
            ),
            in_use=frozenset(p.id for p in page.items if p.id in used),
        )

    def structure_locked(self, college_id: CollegeId, blueprint: ExamBlueprint) -> bool:
        """Whether a booklet pins a version of this paper (D222); a schema never is."""
        return blueprint.kind is BlueprintKind.PAPER and self._booklets.pins_blueprint(
            college_id, blueprint.id
        )

    def create_paper(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        title: str,
        subject_id: SubjectId,
        descriptor: str = "",
        schema_id: BlueprintId | None = None,
    ) -> ExamBlueprint:
        """Create Question Paper (P34, modal 6): from a schema of the same subject (as
        ``generate_paper``), or a starter paper."""
        if not title.strip():
            raise InvariantError("title: give the paper a name")
        if schema_id is not None:
            schema = self._content.get(college_id, ExamBlueprint, schema_id)
            if schema.subject_id != subject_id:
                ensure_author(
                    self._users, self._entitlements, college_id, actor_id, schema.subject_id
                )
                raise InvariantError("schema_id: the schema is of another subject")
            return self.generate_paper(
                college_id, actor_id, schema_id, title=title, descriptor=descriptor
            )
        self._require_subject(college_id, subject_id)
        ensure_author(self._users, self._entitlements, college_id, actor_id, subject_id)
        subject = self._content.get(college_id, Subject, subject_id)
        paper = starter_paper(
            self._rt.new_id(BlueprintId),
            ContentMeta(college_id=college_id, created_by=actor_id),
            subject,
            title=_text_or(title, title),
            descriptor=_descriptor(descriptor),
        )
        return self._add(college_id, actor_id, paper)

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
        descriptor: str | None = None,
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
            descriptor=(
                source.descriptor
                if descriptor is None or source.kind is BlueprintKind.SCHEMA
                else _descriptor(descriptor)
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
        descriptor: str | None = None,
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
            descriptor=schema.descriptor if descriptor is None else _descriptor(descriptor),
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
        # Before reading the newest version: an upload pinning it waits, or is seen (F20).
        self._content.hold_blueprint(college_id, blueprint_id, exclusive=True)
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
        if structure_changes(current, edited) and self.structure_locked(college_id, current):
            raise IllegalTransitionError(LOCKED_MESSAGE)
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
        """Every linked question is one of the college's own (content is private, D165), of
        the paper's subject, and carries the leaf's marks (P34)."""
        problems = []
        for slot in blueprint.slots():
            for label, question_id, marks in leaves(slot):
                if question_id is None:
                    continue
                try:
                    question = self._content.get(college_id, Question, question_id)
                except NotFoundError:
                    problems.append(
                        f"question {label}: the linked question is not in your college's "
                        "question bank"
                    )
                    continue
                if question.subject_id != blueprint.subject_id:
                    problems.append(
                        f"question {label}: {question.code} is of another subject; link a "
                        "question of the paper's subject"
                    )
                elif question.max_marks != marks:
                    problems.append(
                        f"question {label}: {question.code} carries {_marks(question.max_marks)}"
                        f" marks, the slot {_marks(marks)}; they must agree"
                    )
        if problems:
            raise InvariantError("; ".join(problems))


def _marks(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _descriptor(value: str) -> str:
    """A descriptor as stored: trimmed (the blueprint checks its length)."""
    return value.strip()


def _text_or(value: str | None, default: str) -> str:
    """A caller's title, trimmed; the default when absent or blank."""
    if value is None or not value.strip():
        return default
    if len(value.strip()) > _MAX_TITLE:
        raise InvariantError(f"title: at most {_MAX_TITLE} characters")
    return value.strip()
