"""The question bank (P7): questions with their reference answers, rubric criteria, glossary,
uploaded answer-key files and reference diagrams. All of it is global content (design decision
3): any teacher creates for their college, only the owning college edits (each edit is a new
version), everyone else copies (``ContentService.copy_question``).

Nothing is deleted. Removing a reference answer or a criterion saves a *retired* version, so
scores that used it keep its exact version (rule 11)."""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.common import ContentRef, JsonValue, global_blob_key
from tarn_core.domain.content import (
    ContentMeta,
    CriterionParams,
    CriterionType,
    DiagramParams,
    Difficulty,
    Glossary,
    KeyFile,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
    Subject,
    check_code,
)
from tarn_core.domain.diagram import DiagramKind
from tarn_core.errors import AlreadyExistsError, InvariantError, NotFoundError
from tarn_core.ids import (
    CollegeId,
    CriterionId,
    GlossaryId,
    KeyFileId,
    QuestionId,
    ReferenceAnswerId,
    ReferenceDiagramId,
    SubjectId,
    UserId,
)
from tarn_core.ports.repositories import (
    CollegeRepository,
    ContentRepository,
    QuestionQuery,
    UserRepository,
)
from tarn_core.ports.storage import BlobStore
from tarn_core.services._support import Runtime, ref_json
from tarn_core.services.content import ensure_can_edit
from tarn_core.services.uploads import check_upload_image

KEY_FILE_MAX_BYTES = 10 * 1024 * 1024
DIAGRAM_MAX_BYTES = 5 * 1024 * 1024
MAX_KEYWORDS = 30
MAX_KEYWORD_LENGTH = 100
MAX_TERMS = 500
MAX_TERM_LENGTH = 200

NO_STUDENT_DATA = (
    "Confirm that this file holds no student data (names, USNs, handwriting): keys are "
    "written by faculty, never taken from a student's submission (C9)."
)

_PDF = b"%PDF-"
_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"


def sniff_media_type(data: bytes) -> str | None:
    """The type of an uploaded file from its first bytes, never from its name."""
    if data.startswith(_PDF):
        return "application/pdf"
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    return None


@dataclass(frozen=True, slots=True, kw_only=True)
class CriterionSpec:
    """A criterion as the teacher edits it. ``id`` None = new; an id = a new version of an
    existing criterion of the question."""

    label: str
    type: CriterionType
    weight: Decimal
    params: CriterionParams
    id: CriterionId | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionSummary:
    question: Question
    key_count: int
    owner_name: str | None
    subject_name: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionList:
    items: tuple[QuestionSummary, ...]
    total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionDetail:
    question: Question
    owner_name: str | None
    subject: Subject | None
    answers: tuple[ReferenceAnswer, ...]
    criteria: tuple[RubricCriterion, ...]
    glossary: Glossary | None
    key_files: tuple[KeyFile, ...]
    diagrams: tuple[ReferenceDiagram, ...]

    @property
    def rubric_total(self) -> Decimal:
        return sum((c.weight for c in self.criteria), Decimal(0))


def _clean_list(values: Sequence[str], *, what: str, limit: int, longest: int) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    for raw in values:
        value = " ".join(raw.split())
        if not value:
            continue
        if len(value) > longest:
            raise InvariantError(f"a {what} is longer than {longest} characters")
        seen.setdefault(value.casefold(), value)
    if len(seen) > limit:
        raise InvariantError(f"at most {limit} {what}s")
    return tuple(seen.values())


def safe_file_name(name: str) -> str:
    """The last path part with anything but letters, digits, dot, dash and underscore replaced."""
    base = re.split(r"[\\/]", name.strip())[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return (cleaned or "file")[:100]


class QuestionBankService:
    def __init__(
        self,
        *,
        content: ContentRepository,
        users: UserRepository,
        colleges: CollegeRepository,
        blobs: BlobStore,
        runtime: Runtime,
    ):
        self._content = content
        self._users = users
        self._colleges = colleges
        self._blobs = blobs
        self._rt = runtime

    # ---- reading --------------------------------------------------------------------------

    def topics(self, subject_id: SubjectId | None = None) -> Sequence[str]:
        return self._content.topics(subject_id)

    def search(self, query: QuestionQuery, *, limit: int, offset: int = 0) -> QuestionList:
        page = self._content.search_questions(query, limit=limit, offset=offset)
        counts = self._content.key_counts([q.id for q in page.items])
        owners = self._colleges.names(
            sorted({q.meta.owning_college_id for q in page.items}, key=str)
        )
        subjects = {s.id: s for s in self._content.latest(Subject)}
        return QuestionList(
            items=tuple(
                QuestionSummary(
                    question=q,
                    key_count=counts.get(q.id, 0),
                    owner_name=owners.get(q.meta.owning_college_id),
                    subject_name=subject.name if (subject := subjects.get(q.subject_id)) else None,
                )
                for q in page.items
            ),
            total=page.total,
        )

    def detail(self, question_id: QuestionId) -> QuestionDetail:
        question = self._content.get(Question, question_id)
        try:
            subject: Subject | None = self._content.get(Subject, question.subject_id)
        except NotFoundError:
            subject = None
        glossaries = self._content.for_question(Glossary, question_id)
        owner = self._colleges.names([question.meta.owning_college_id])
        return QuestionDetail(
            question=question,
            owner_name=owner.get(question.meta.owning_college_id),
            subject=subject,
            answers=tuple(self._content.for_question(ReferenceAnswer, question_id)),
            criteria=tuple(self._content.for_question(RubricCriterion, question_id)),
            glossary=glossaries[0] if glossaries else None,
            key_files=tuple(self._content.for_question(KeyFile, question_id)),
            diagrams=tuple(self._content.for_question(ReferenceDiagram, question_id)),
        )

    # ---- questions ------------------------------------------------------------------------

    def create_question(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        subject_id: SubjectId,
        code: str,
        text: str,
        max_marks: Decimal,
        difficulty: Difficulty = Difficulty.MEDIUM,
        category: str = "",
        reference_answer: str | None = None,
        criteria: Sequence[CriterionSpec] = (),
    ) -> Question:
        """The question with, optionally, its first reference answer and rubric, in one step:
        everything is checked before anything is written."""
        self._users.get(college_id, actor_id)
        self._require_subject(subject_id)
        code = code.strip()
        check_code(code)
        self._require_free_code(college_id, code)
        question = Question(
            id=self._rt.new_id(QuestionId),
            meta=ContentMeta(owning_college_id=college_id, created_by=actor_id),
            subject_id=subject_id,
            code=code,
            text=text.strip(),
            max_marks=max_marks,
            difficulty=difficulty,
            category=" ".join(category.split()),
        )
        answer = (
            None
            if reference_answer is None or not reference_answer.strip()
            else ReferenceAnswer(
                id=self._rt.new_id(ReferenceAnswerId),
                meta=self._meta(college_id, actor_id),
                question_id=question.id,
                text=reference_answer.strip(),
            )
        )
        planned = self._plan_rubric(question, criteria, {}, college_id, actor_id)

        self._content.save(question)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(question.ref)
        )
        if answer is not None:
            self._content.save(answer)
        self._save_rubric(college_id, actor_id, planned)
        return question

    def update_question(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        *,
        code: str,
        text: str,
        max_marks: Decimal,
        difficulty: Difficulty,
        category: str,
        criteria: Sequence[CriterionSpec] | None = None,
    ) -> Question:
        """The next version of the question (the subject never changes: copy to move it).
        Changing the marks needs the rubric along with it, because its weights must add up to
        them; pass ``criteria`` to replace it."""
        self._users.get(college_id, actor_id)
        current = self._content.get(Question, question_id)
        ensure_can_edit(college_id, current)
        code = code.strip()
        check_code(code)
        if code.casefold() != current.code.casefold():
            self._require_free_code(college_id, code)
        edited = replace(
            current,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
            code=code,
            text=text.strip(),
            max_marks=max_marks,
            difficulty=difficulty,
            category=" ".join(category.split()),
        )
        live = self._live_criteria(question_id)
        if criteria is None and max_marks != current.max_marks and live:
            total = sum((c.weight for c in live.values()), Decimal(0))
            raise InvariantError(
                f"The rubric weights add up to {total} marks. Change the rubric together with "
                f"the marks ({max_marks}) so that they still add up."
            )
        planned = (
            None
            if criteria is None
            else self._plan_rubric(edited, criteria, live, college_id, actor_id)
        )

        changed = replace(edited, meta=current.meta) != current
        if changed:
            self._content.save(edited)
            self._rt.record(
                college_id,
                actor_id,
                AuditAction.CONTENT_EDITED,
                before=ref_json(current.ref),
                after=ref_json(edited.ref),
            )
        if planned is not None:
            self._save_rubric(college_id, actor_id, planned)
        return edited if changed else current

    # ---- rubric ---------------------------------------------------------------------------

    def set_rubric(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        criteria: Sequence[CriterionSpec],
    ) -> tuple[RubricCriterion, ...]:
        """Replace the question's criteria. Their weights must add up to the question's max
        marks (no criteria at all is allowed: a guidance-only key is marked by hand)."""
        self._users.get(college_id, actor_id)
        question = self._content.get(Question, question_id)
        ensure_can_edit(college_id, question)
        planned = self._plan_rubric(
            question, criteria, self._live_criteria(question_id), college_id, actor_id
        )
        self._save_rubric(college_id, actor_id, planned)
        return tuple(self._content.for_question(RubricCriterion, question_id))

    def _live_criteria(self, question_id: QuestionId) -> dict[CriterionId, RubricCriterion]:
        return {c.id: c for c in self._content.for_question(RubricCriterion, question_id)}

    def _plan_rubric(
        self,
        question: Question,
        specs: Sequence[CriterionSpec],
        live: dict[CriterionId, RubricCriterion],
        college_id: CollegeId,
        actor_id: UserId,
    ) -> "_RubricPlan":
        """Check the specs against the question and work out what to save; saves nothing."""
        total = sum((s.weight for s in specs), Decimal(0))
        if specs and total != question.max_marks:
            raise InvariantError(
                f"The rubric weights add up to {total} marks, but the question carries "
                f"{question.max_marks}. Change the weights so that they add up."
            )
        diagrams = {d.id for d in self._content.for_question(ReferenceDiagram, question.id)}
        seen: set[CriterionId] = set()
        save: list[RubricCriterion] = []
        for spec in specs:
            if isinstance(spec.params, DiagramParams) and (
                spec.params.reference_diagram_id not in diagrams
            ):
                raise InvariantError(
                    f"Criterion {spec.label!r} points at a reference diagram that does not "
                    "belong to this question; upload the diagram first."
                )
            if spec.id is None:
                save.append(
                    RubricCriterion(
                        id=self._rt.new_id(CriterionId),
                        meta=self._meta(college_id, actor_id),
                        question_id=question.id,
                        label=spec.label.strip(),
                        type=spec.type,
                        weight=spec.weight,
                        params=spec.params,
                    )
                )
                continue
            before = live.get(spec.id)
            if before is None:
                raise InvariantError(f"Criterion {spec.id} is not part of this question's rubric.")
            if spec.id in seen:
                raise InvariantError(f"Criterion {spec.label!r} appears twice.")
            seen.add(spec.id)
            after = replace(
                before,
                label=spec.label.strip(),
                type=spec.type,
                weight=spec.weight,
                params=spec.params,
            )
            if after != before:
                save.append(
                    replace(
                        after,
                        meta=replace(
                            before.meta, version=before.meta.version + 1, created_by=actor_id
                        ),
                    )
                )
        retire = [
            replace(
                c,
                retired=True,
                meta=replace(c.meta, version=c.meta.version + 1, created_by=actor_id),
            )
            for cid, c in live.items()
            if cid not in seen
        ]
        return _RubricPlan(save=tuple(save), retire=tuple(retire))

    def _save_rubric(self, college_id: CollegeId, actor_id: UserId, plan: "_RubricPlan") -> None:
        for criterion in (*plan.save, *plan.retire):
            self._content.save(criterion)
        if plan.save or plan.retire:
            self._rt.record(
                college_id,
                actor_id,
                AuditAction.CONTENT_EDITED,
                after={
                    "rubric": [ref_json(c.ref) for c in plan.save],
                    "retired": [ref_json(c.ref) for c in plan.retire],
                },
            )

    # ---- reference answers ----------------------------------------------------------------

    def add_reference_answer(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        *,
        text: str,
        guidance_only: bool = False,
        synthetic: bool = False,
    ) -> ReferenceAnswer:
        """``synthetic`` marks a key written for development (D18): it is shown as
        "SYNTHETIC - dev only, needs teacher validation". The web app does not set it; the
        development seed does."""
        self._users.get(college_id, actor_id)
        ensure_can_edit(college_id, self._content.get(Question, question_id))
        answer = ReferenceAnswer(
            id=self._rt.new_id(ReferenceAnswerId),
            meta=self._meta(college_id, actor_id),
            question_id=question_id,
            text=text.strip(),
            guidance_only=guidance_only,
            synthetic=synthetic,
        )
        self._content.save(answer)
        self._rt.record(
            college_id, actor_id, AuditAction.CONTENT_CREATED, after=ref_json(answer.ref)
        )
        return answer

    def edit_reference_answer(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        answer_id: ReferenceAnswerId,
        *,
        text: str,
        guidance_only: bool,
    ) -> ReferenceAnswer:
        current = self._own_answer(college_id, actor_id, question_id, answer_id)
        edited = replace(
            current,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
            text=text.strip(),
            guidance_only=guidance_only,
        )
        if replace(edited, meta=current.meta) == current:
            return current
        self._content.save(edited)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_EDITED,
            before=ref_json(current.ref),
            after=ref_json(edited.ref),
        )
        return edited

    def retire_reference_answer(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        answer_id: ReferenceAnswerId,
    ) -> None:
        current = self._own_answer(college_id, actor_id, question_id, answer_id)
        retired = replace(
            current,
            retired=True,
            meta=replace(current.meta, version=current.meta.version + 1, created_by=actor_id),
        )
        self._content.save(retired)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_RETIRED,
            before=ref_json(current.ref),
            after=ref_json(retired.ref),
        )

    def _own_answer(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        answer_id: ReferenceAnswerId,
    ) -> ReferenceAnswer:
        self._users.get(college_id, actor_id)
        ensure_can_edit(college_id, self._content.get(Question, question_id))
        live = {a.id: a for a in self._content.for_question(ReferenceAnswer, question_id)}
        if answer_id not in live:
            raise NotFoundError(f"reference answer {answer_id}")
        return live[answer_id]

    # ---- glossary -------------------------------------------------------------------------

    def set_glossary_terms(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        terms: Sequence[str],
    ) -> Glossary | None:
        """The teacher's terms; the reference diagram labels are kept up to date beside them."""
        self._users.get(college_id, actor_id)
        ensure_can_edit(college_id, self._content.get(Question, question_id))
        cleaned = _clean_list(terms, what="term", limit=MAX_TERMS, longest=MAX_TERM_LENGTH)
        return self._sync_glossary(college_id, actor_id, question_id, terms=cleaned)

    def refresh_reference_labels(
        self, college_id: CollegeId, actor_id: UserId, question_id: QuestionId
    ) -> Glossary | None:
        """Bring the glossary's reference labels up to date after a reference diagram's graph
        changed (recognised or edited)."""
        return self._sync_glossary(college_id, actor_id, question_id)

    def set_off_target_terms(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        terms: Sequence[str],
    ) -> Glossary | None:
        """Names that contradict the key (the off-target guard, C13), e.g. "Congress" for a
        question on the Indian President."""
        self._users.get(college_id, actor_id)
        ensure_can_edit(college_id, self._content.get(Question, question_id))
        cleaned = _clean_list(terms, what="term", limit=MAX_TERMS, longest=MAX_TERM_LENGTH)
        return self._sync_glossary(college_id, actor_id, question_id, off_target=cleaned)

    def _sync_glossary(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        *,
        terms: tuple[str, ...] | None = None,
        off_target: tuple[str, ...] | None = None,
    ) -> Glossary | None:
        existing = next(iter(self._content.for_question(Glossary, question_id)), None)
        labels = _clean_list(
            [
                node.label
                for d in self._content.for_question(ReferenceDiagram, question_id)
                for node in d.graph.nodes
                if len(" ".join(node.label.split())) <= MAX_TERM_LENGTH
            ][:MAX_TERMS],
            what="label",
            limit=MAX_TERMS,
            longest=MAX_TERM_LENGTH,
        )
        wanted_terms = (existing.teacher_terms if existing else ()) if terms is None else terms
        wanted_off = (
            (existing.off_target_terms if existing else ()) if off_target is None else off_target
        )
        if existing is None and not wanted_terms and not labels and not wanted_off:
            return None
        if existing is None:
            glossary = Glossary(
                id=self._rt.new_id(GlossaryId),
                meta=self._meta(college_id, actor_id),
                question_id=question_id,
                teacher_terms=wanted_terms,
                reference_labels=labels,
                off_target_terms=wanted_off,
            )
        elif (existing.teacher_terms, existing.reference_labels, existing.off_target_terms) == (
            wanted_terms,
            labels,
            wanted_off,
        ):
            return existing
        else:
            glossary = replace(
                existing,
                meta=replace(existing.meta, version=existing.meta.version + 1, created_by=actor_id),
                teacher_terms=wanted_terms,
                reference_labels=labels,
                off_target_terms=wanted_off,
            )
        self._content.save(glossary)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_EDITED if existing else AuditAction.CONTENT_CREATED,
            before=None if existing is None else ref_json(existing.ref),
            after=ref_json(glossary.ref),
        )
        return glossary

    # ---- files ----------------------------------------------------------------------------

    def upload_key_file(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        *,
        file_name: str,
        data: bytes,
        declared_type: str | None,
        keywords: Sequence[str] = (),
        no_student_data: bool,
    ) -> KeyFile:
        """An answer key or sample as PDF or image. The uploader must confirm it holds no
        student data (C9); the type is read from the bytes, not trusted from the name."""
        self._users.get(college_id, actor_id)
        ensure_can_edit(college_id, self._content.get(Question, question_id))
        media_type = self._checked_upload(
            data,
            declared_type,
            no_student_data,
            allowed=("application/pdf", "image/png", "image/jpeg"),
            limit=KEY_FILE_MAX_BYTES,
            what="PDF, PNG or JPEG",
        )
        file_id = self._rt.new_id(KeyFileId)
        name = safe_file_name(file_name)
        key_file = KeyFile(
            id=file_id,
            meta=self._meta(college_id, actor_id),
            question_id=question_id,
            name=name,
            media_type=media_type,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            blob=global_blob_key("keys", str(question_id), str(file_id), name),
            keywords=_clean_list(
                keywords, what="keyword", limit=MAX_KEYWORDS, longest=MAX_KEYWORD_LENGTH
            ),
            no_student_data_confirmed=True,
        )
        self._store(data, media_type, key_file)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_CREATED,
            after={**_ref_dict(key_file.ref), "no_student_data_confirmed": True},
        )
        return key_file

    def upload_reference_diagram(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        question_id: QuestionId,
        *,
        file_name: str,
        data: bytes,
        declared_type: str | None,
        no_student_data: bool,
        kind: DiagramKind = DiagramKind.FLOWCHART,
    ) -> ReferenceDiagram:
        """A reference diagram as PNG (U5 Q14). Its nodes and edges are read from the picture
        by the recognizer (the ``diagram.reference`` job, P14) and the teacher edits them;
        until then the graph is empty (``pending``)."""
        self._users.get(college_id, actor_id)
        ensure_can_edit(college_id, self._content.get(Question, question_id))
        self._checked_upload(
            data,
            declared_type,
            no_student_data,
            allowed=("image/png",),
            limit=DIAGRAM_MAX_BYTES,
            what="PNG",
        )
        diagram_id = self._rt.new_id(ReferenceDiagramId)
        diagram = ReferenceDiagram(
            id=diagram_id,
            meta=self._meta(college_id, actor_id),
            question_id=question_id,
            png=global_blob_key(
                "diagrams", str(question_id), str(diagram_id), safe_file_name(file_name)
            ),
            kind=kind,
        )
        self._store(data, "image/png", diagram)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.CONTENT_CREATED,
            after={**_ref_dict(diagram.ref), "no_student_data_confirmed": True},
        )
        self._sync_glossary(college_id, actor_id, question_id)
        return diagram

    def read_key_file(self, question_id: QuestionId, file_id: KeyFileId) -> tuple[KeyFile, bytes]:
        found = next(
            (f for f in self._content.for_question(KeyFile, question_id) if f.id == file_id), None
        )
        if found is None:
            raise NotFoundError(f"key file {file_id}")
        return found, self._blobs.get(found.blob)

    def read_reference_diagram(
        self, question_id: QuestionId, diagram_id: ReferenceDiagramId
    ) -> tuple[ReferenceDiagram, bytes]:
        found = next(
            (
                d
                for d in self._content.for_question(ReferenceDiagram, question_id)
                if d.id == diagram_id
            ),
            None,
        )
        if found is None:
            raise NotFoundError(f"reference diagram {diagram_id}")
        return found, self._blobs.get(found.png)

    @staticmethod
    def _checked_upload(
        data: bytes,
        declared_type: str | None,
        no_student_data: bool,
        *,
        allowed: tuple[str, ...],
        limit: int,
        what: str,
    ) -> str:
        if not no_student_data:
            raise InvariantError(NO_STUDENT_DATA)
        if not data:
            raise InvariantError("The file is empty.")
        if len(data) > limit:
            raise InvariantError(f"The file is larger than {limit // (1024 * 1024)} MB.")
        found = sniff_media_type(data)
        if found not in allowed:
            raise InvariantError(f"Only {what} files are accepted; this file is not one.")
        check_upload_image(data, found or "")
        declared = (declared_type or "").split(";")[0].strip().lower()
        if declared not in ("", "application/octet-stream", found):
            raise InvariantError(
                f"The file says it is {declared} but its content is {found}; upload the real "
                f"{what} file."
            )
        return found

    def _store(self, data: bytes, media_type: str, record: KeyFile | ReferenceDiagram) -> None:
        """Blob first, then the record; a failed record leaves no orphan behind."""
        blob = record.blob if isinstance(record, KeyFile) else record.png
        self._blobs.put(blob, data, media_type)
        try:
            self._content.save(record)
        except Exception:
            self._blobs.delete(blob)
            raise

    # ---- helpers --------------------------------------------------------------------------

    def _meta(self, college_id: CollegeId, actor_id: UserId) -> ContentMeta:
        return ContentMeta(owning_college_id=college_id, created_by=actor_id)

    def _require_subject(self, subject_id: SubjectId) -> None:
        try:
            self._content.get(Subject, subject_id)
        except NotFoundError:
            raise InvariantError(
                "subject: no such subject; pick one from the list or create it first"
            ) from None

    def _require_free_code(self, college_id: CollegeId, code: str) -> None:
        query = QuestionQuery(code=code, exact_code=True, owner_id=college_id)
        if self._content.search_questions(query, limit=1).total:
            raise AlreadyExistsError(f"Your college already has a question with code {code!r}.")


@dataclass(frozen=True, slots=True)
class _RubricPlan:
    save: tuple[RubricCriterion, ...]
    retire: tuple[RubricCriterion, ...]


def _ref_dict(ref: ContentRef) -> dict[str, JsonValue]:
    return {"kind": ref.kind.value, "id": str(ref.id), "version": ref.version}


__all__ = [
    "DIAGRAM_MAX_BYTES",
    "KEY_FILE_MAX_BYTES",
    "NO_STUDENT_DATA",
    "CriterionSpec",
    "QuestionBankService",
    "QuestionDetail",
    "QuestionList",
    "QuestionSummary",
    "sniff_media_type",
]
