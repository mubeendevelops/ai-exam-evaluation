"""Global content (R7, design decision 3): visible to every college, versioned, and editable
only by teachers of the owning college. Others copy it to their own college.

Items that belong to a question point at it by id, not by version, so fixing a question's
wording does not force new versions of its key and rubric. The exact versions used are
recorded on every score instead (``AnswerScore.content_versions``)."""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from tarn_core.domain.common import (
    BlobKey,
    ContentKind,
    ContentRef,
    check_marks,
    check_text,
)
from tarn_core.domain.diagram import DiagramGraph
from tarn_core.errors import InvariantError
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ContentMeta:
    """Fields every global content version carries."""

    version: int = 1
    owning_college_id: CollegeId
    created_by: UserId
    copied_from: ContentRef | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise InvariantError(f"content version starts at 1, got {self.version}")


@dataclass(frozen=True, slots=True, kw_only=True)
class Subject:
    id: SubjectId
    meta: ContentMeta
    code: str
    name: str

    def __post_init__(self) -> None:
        check_text("subject code", self.code)
        check_text("subject name", self.name)

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.SUBJECT, id=self.id, version=self.meta.version)


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


MAX_CODE_LENGTH = 40


def check_code(code: str) -> None:
    """A question code such as ``PHY-Q101``: printable, no surrounding space, at most 40."""
    if not code.strip():
        raise InvariantError("question code must not be blank")
    if code != code.strip() or len(code) > MAX_CODE_LENGTH or not code.isprintable():
        raise InvariantError(
            f"question code {code!r} must be printable text of at most {MAX_CODE_LENGTH} "
            "characters without spaces at either end"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Question:
    """``category`` is the topic (the prototype's Category). ``code`` is unique within the
    owning college (checked by the question bank service)."""

    id: QuestionId
    meta: ContentMeta
    subject_id: SubjectId
    code: str
    text: str
    max_marks: Decimal
    difficulty: Difficulty = Difficulty.MEDIUM
    category: str = ""

    def __post_init__(self) -> None:
        check_code(self.code)
        check_text("question text", self.text)
        check_marks("question max marks", self.max_marks, allow_zero=False)

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.QUESTION, id=self.id, version=self.meta.version)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceAnswer:
    """Model answer for a question. ``synthetic`` marks generated dev keys (D18);
    ``guidance_only`` marks keys with no usable model answer ("mark manually", C8)."""

    id: ReferenceAnswerId
    meta: ContentMeta
    question_id: QuestionId
    text: str
    synthetic: bool = False
    guidance_only: bool = False
    retired: bool = False
    """A retired version means the key was removed: repositories stop listing it for the
    question, and scores that used earlier versions keep them."""

    def __post_init__(self) -> None:
        check_text("reference answer text", self.text)

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.REFERENCE_ANSWER, id=self.id, version=self.meta.version)


class CriterionType(StrEnum):
    LIST = "list"
    NUMERIC = "numeric"
    SEMANTIC = "semantic"
    DIAGRAM = "diagram"
    LLM = "llm"


@dataclass(frozen=True, slots=True, kw_only=True)
class ListItem:
    term: str
    synonyms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_text("list item", self.term)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListParams:
    """Match against an item list; credit = matched / required_count, capped at 1."""

    items: tuple[ListItem, ...]
    required_count: int

    def __post_init__(self) -> None:
        if not 1 <= self.required_count <= len(self.items):
            raise InvariantError("required_count must be between 1 and the number of items")


@dataclass(frozen=True, slots=True, kw_only=True)
class NumericParams:
    """One numeric step: credit 1 if within tolerance, else 0."""

    expected: Decimal
    tolerance: Decimal = Decimal(0)
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.expected.is_finite():
            raise InvariantError("expected value must be finite")
        check_marks("tolerance", self.tolerance)


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticParams:
    """Best embedding match between this statement and any student sentence."""

    reference_statement: str

    def __post_init__(self) -> None:
        check_text("reference statement", self.reference_statement)


class DiagramComponent(StrEnum):
    WHOLE = "whole"
    NODES = "nodes"
    EDGES = "edges"
    LABELS = "labels"


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagramParams:
    reference_diagram_id: ReferenceDiagramId
    component: DiagramComponent = DiagramComponent.WHOLE


@dataclass(frozen=True, slots=True, kw_only=True)
class LlmParams:
    instructions: str

    def __post_init__(self) -> None:
        check_text("LLM instructions", self.instructions)


type CriterionParams = ListParams | NumericParams | SemanticParams | DiagramParams | LlmParams

_PARAMS_FOR_TYPE: dict[CriterionType, type[CriterionParams]] = {
    CriterionType.LIST: ListParams,
    CriterionType.NUMERIC: NumericParams,
    CriterionType.SEMANTIC: SemanticParams,
    CriterionType.DIAGRAM: DiagramParams,
    CriterionType.LLM: LlmParams,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class RubricCriterion:
    """One weighted criterion. ``weight`` is in marks: answer mark = sum(weight x credit)."""

    id: CriterionId
    meta: ContentMeta
    question_id: QuestionId
    label: str
    type: CriterionType
    weight: Decimal
    params: CriterionParams
    retired: bool = False

    def __post_init__(self) -> None:
        check_text("criterion label", self.label)
        check_marks("criterion weight", self.weight, allow_zero=False)
        expected = _PARAMS_FOR_TYPE[self.type]
        if not isinstance(self.params, expected):
            raise InvariantError(
                f"{self.type} criterion needs {expected.__name__}, got {type(self.params).__name__}"
            )

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.RUBRIC_CRITERION, id=self.id, version=self.meta.version)


@dataclass(frozen=True, slots=True, kw_only=True)
class Rubric:
    """The criteria of one question version. Weights must sum to the question's max marks,
    unless the key is guidance only, in which case there are no criteria (mark manually)."""

    question: Question
    criteria: tuple[RubricCriterion, ...]
    guidance_only: bool = False

    def __post_init__(self) -> None:
        if self.guidance_only:
            if self.criteria:
                raise InvariantError("a guidance-only key has no AI-scored criteria")
            return
        if not self.criteria:
            raise InvariantError("a rubric needs at least one criterion")
        ids = [c.id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise InvariantError("rubric criteria must be unique")
        for c in self.criteria:
            if c.question_id != self.question.id:
                raise InvariantError(f"criterion {c.label!r} belongs to another question")
        total = sum((c.weight for c in self.criteria), Decimal(0))
        if total != self.question.max_marks:
            raise InvariantError(
                f"rubric weights sum to {total}, question max marks is {self.question.max_marks}"
            )

    @property
    def content_refs(self) -> frozenset[ContentRef]:
        return frozenset({self.question.ref, *(c.ref for c in self.criteria)})


@dataclass(frozen=True, slots=True, kw_only=True)
class Glossary:
    """Per-question vocabulary: teacher terms plus reference diagram labels (D8)."""

    id: GlossaryId
    meta: ContentMeta
    question_id: QuestionId
    teacher_terms: tuple[str, ...] = ()
    reference_labels: tuple[str, ...] = ()
    off_target_terms: tuple[str, ...] = ()
    """Names that contradict the key (the off-target guard, C13): e.g. "Congress" in an answer
    on the Indian President. Teacher-written; not part of ``terms``."""

    @property
    def terms(self) -> tuple[str, ...]:
        """Union of both lists, first occurrence kept, compared case-insensitively."""
        seen: dict[str, str] = {}
        for term in (*self.teacher_terms, *self.reference_labels):
            seen.setdefault(term.strip().casefold(), term.strip())
        return tuple(v for v in seen.values() if v)

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.GLOSSARY, id=self.id, version=self.meta.version)


@dataclass(frozen=True, slots=True, kw_only=True)
class ReferenceDiagram:
    """Uploaded PNG plus its graph (recognised, then optionally edited by the teacher)."""

    id: ReferenceDiagramId
    meta: ContentMeta
    question_id: QuestionId
    png: BlobKey
    graph: DiagramGraph = field(default_factory=lambda: DiagramGraph(nodes=()))

    def __post_init__(self) -> None:
        if self.png.college_id is not None:
            raise InvariantError("reference diagrams are global content: key must be under global/")

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.REFERENCE_DIAGRAM, id=self.id, version=self.meta.version)


KEY_FILE_TYPES = frozenset({"application/pdf", "image/png", "image/jpeg"})


@dataclass(frozen=True, slots=True, kw_only=True)
class KeyFile:
    """An uploaded answer key or sample (PDF or image) attached to a question. Keys must hold
    no student data (C9): the uploader confirms it, and the record cannot exist without it."""

    id: KeyFileId
    meta: ContentMeta
    question_id: QuestionId
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    blob: BlobKey
    keywords: tuple[str, ...] = ()
    no_student_data_confirmed: bool = False

    def __post_init__(self) -> None:
        check_text("file name", self.name)
        if self.media_type not in KEY_FILE_TYPES:
            raise InvariantError(f"key files are PDF or image files, not {self.media_type}")
        if self.size_bytes < 1:
            raise InvariantError("a key file cannot be empty")
        if len(self.sha256) != 64:
            raise InvariantError("sha256 must be 64 hex characters")
        if self.blob.college_id is not None:
            raise InvariantError("key files are global content: key must be under global/")
        if not self.no_student_data_confirmed:
            raise InvariantError(
                "confirm that the key holds no student data (names, USNs, handwriting)"
            )

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.KEY_FILE, id=self.id, version=self.meta.version)
