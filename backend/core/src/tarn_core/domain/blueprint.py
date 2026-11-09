"""Exam blueprints: the structure of a question paper (C12). College content (D165).

A section holds items; an item is one question slot or an OR group of slots. A section
counts all its items, or the best N of them ("any N of M", C19). A slot either points at
one question or is split into sub-parts that each point at a question (QP-IPR 12a + 12b).
Step marks split a leaf's marks further (K-AI1).

A slot or sub-part may be *unlinked*: the designer can lay out a paper before its questions
exist. An unlinked blueprint is saved and checked like any other, but no booklet can be
registered against it (``unlinked_leaves``).

Since document 1.2 (P33, D219) a blueprint is one of two kinds: a **schema**, a reusable
template whose slots stay unlinked and which has no exam code, or a **paper**, which links its
slots and needs an exam code before a unit can use it. A paper may name the schema it was made
from (``from_schema``)."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from tarn_core.domain.common import ContentKind, ContentRef, check_marks, check_text
from tarn_core.domain.content import ContentMeta
from tarn_core.errors import InvariantError
from tarn_core.ids import BlueprintId, QuestionId, SubjectId


class BlueprintKind(StrEnum):
    SCHEMA = "schema"
    PAPER = "paper"


MAX_TARGET_BATCH = 100
MAX_DESCRIPTOR = 50
MAX_NOTE = 1000
"""Limits of the 1.2 header texts: ``weightage_note`` and ``description`` share ``MAX_NOTE``."""


class EvaluationMethod(StrEnum):
    """How a section is meant to be evaluated (the designer's per-section choice)."""

    EXACT_PATTERN_MATCH = "exact_pattern_match"
    OMR_BUBBLE_SCAN = "omr_bubble_scan"
    KEYWORD_FORMULA = "keyword_formula"
    SEMANTIC_RUBRIC = "semantic_rubric"
    DIAGRAM = "diagram"


# Objective sections: a wrong answer there can carry negative marks.
OBJECTIVE_METHODS = frozenset(
    {EvaluationMethod.EXACT_PATTERN_MATCH, EvaluationMethod.OMR_BUBBLE_SCAN}
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    label: str
    marks: Decimal

    def __post_init__(self) -> None:
        check_text("step label", self.label)
        check_marks("step marks", self.marks, allow_zero=False)


def _check_steps(owner: str, marks: Decimal, steps: tuple[Step, ...]) -> None:
    if steps and sum((s.marks for s in steps), Decimal(0)) != marks:
        raise InvariantError(f"step marks of {owner} do not sum to its {marks} marks")
    _check_unique(f"step labels of {owner}", [s.label for s in steps])


@dataclass(frozen=True, slots=True, kw_only=True)
class SubPart:
    label: str
    marks: Decimal
    question_id: QuestionId | None = None
    steps: tuple[Step, ...] = ()

    def __post_init__(self) -> None:
        check_text("sub-part label", self.label)
        check_marks("sub-part marks", self.marks, allow_zero=False)
        _check_steps(f"sub-part {self.label}", self.marks, self.steps)


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionSlot:
    """One numbered question on the paper. ``negative_marks`` is deducted when an attempted
    answer earns nothing (0 = no negative marking)."""

    label: str
    marks: Decimal
    question_id: QuestionId | None = None
    parts: tuple[SubPart, ...] = ()
    steps: tuple[Step, ...] = ()
    negative_marks: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        check_text("question label", self.label)
        check_marks("question marks", self.marks, allow_zero=False)
        check_marks("negative marks", self.negative_marks)
        if self.question_id is not None and self.parts:
            raise InvariantError(f"slot {self.label} has a question or sub-parts, not both")
        if self.parts:
            if self.steps:
                raise InvariantError(f"slot {self.label}: put step marks on its sub-parts")
            if sum((p.marks for p in self.parts), Decimal(0)) != self.marks:
                raise InvariantError(f"sub-part marks of {self.label} do not sum to {self.marks}")
            _check_unique(f"sub-part labels of {self.label}", [p.label for p in self.parts])
        _check_steps(f"slot {self.label}", self.marks, self.steps)

    @property
    def question_ids(self) -> tuple[QuestionId | None, ...]:
        """One entry per answerable leaf; None where the leaf is not linked yet."""
        if self.parts:
            return tuple(p.question_id for p in self.parts)
        return (self.question_id,)


@dataclass(frozen=True, slots=True, kw_only=True)
class OrGroup:
    """Alternatives of which one counts: the higher-scoring if both are answered (D9)."""

    alternatives: tuple[QuestionSlot, ...]

    def __post_init__(self) -> None:
        if len(self.alternatives) < 2:
            raise InvariantError("an OR group needs at least two alternatives")
        if len({a.marks for a in self.alternatives}) != 1:
            raise InvariantError(f"OR alternatives {self.label} must carry equal marks")

    @property
    def marks(self) -> Decimal:
        return self.alternatives[0].marks

    @property
    def label(self) -> str:
        return " or ".join(a.label for a in self.alternatives)


type BlueprintItem = QuestionSlot | OrGroup


@dataclass(frozen=True, slots=True)
class AllOf:
    """Every item counts."""


@dataclass(frozen=True, slots=True)
class AnyN:
    """The best ``n`` attempted items count; the rest are shown as "not counted"."""

    n: int


type ChoiceRule = AllOf | AnyN


@dataclass(frozen=True, slots=True, kw_only=True)
class Section:
    label: str
    items: tuple[BlueprintItem, ...]
    rule: ChoiceRule = AllOf()
    title: str = ""
    method: EvaluationMethod = EvaluationMethod.SEMANTIC_RUBRIC

    def __post_init__(self) -> None:
        check_text("section label", self.label)
        if not self.items:
            raise InvariantError(f"section {self.label} has no items")
        if isinstance(self.rule, AnyN) and not 1 <= self.rule.n <= len(self.items):
            raise InvariantError(
                f"section {self.label}: any {self.rule.n} of {len(self.items)} is impossible"
            )

    @property
    def counted_items(self) -> int:
        return self.rule.n if isinstance(self.rule, AnyN) else len(self.items)

    @property
    def max_marks(self) -> Decimal:
        """Most a student can earn here: the sum of the N largest items' marks."""
        top = sorted((i.marks for i in self.items), reverse=True)[: self.counted_items]
        return sum(top, Decimal(0))

    def slots(self) -> Iterator[QuestionSlot]:
        for item in self.items:
            if isinstance(item, OrGroup):
                yield from item.alternatives
            else:
                yield item


@dataclass(frozen=True, slots=True, kw_only=True)
class ExamBlueprint:
    id: BlueprintId
    meta: ContentMeta
    subject_id: SubjectId
    title: str
    total_marks: Decimal
    sections: tuple[Section, ...]
    mark_step: Decimal = Decimal("0.5")
    course_code: str = ""
    """The course code printed on the paper (often the subject's code): not unique."""
    exam_code: str = ""
    """Names this exam (P26): unique per college among blueprints that carry one; a paper
    needs it before a unit can use it. Empty on papers made before schema v1.1."""
    duration_minutes: int | None = None
    kind: BlueprintKind = BlueprintKind.PAPER
    target_batch: str = ""
    """Free text ("2024 batch", "Class 12"): a filter only, not tied to a unit's year (D219)."""
    weightage_note: str = ""
    """The rubric weightage description: shown, not enforced."""
    description: str = ""
    descriptor: str = ""
    """One free-text tag per paper (D219)."""
    from_schema: BlueprintId | None = None
    """The schema this paper was made from; it may since have been deleted (D231)."""
    saved_at: datetime | None = field(default=None, compare=False)
    """When this version was saved (the row's ``created_at``); None before it is stored."""

    def __post_init__(self) -> None:
        check_text("blueprint title", self.title)
        if self.exam_code != self.exam_code.strip() or len(self.exam_code) > 50:
            raise InvariantError("exam code: trimmed, at most 50 characters")
        for name, value, limit in (
            ("target batch", self.target_batch, MAX_TARGET_BATCH),
            ("weightage note", self.weightage_note, MAX_NOTE),
            ("description", self.description, MAX_NOTE),
            ("descriptor", self.descriptor, MAX_DESCRIPTOR),
        ):
            if value != value.strip() or len(value) > limit:
                raise InvariantError(f"{name}: trimmed, at most {limit} characters")
        if self.kind is BlueprintKind.SCHEMA:
            if self.exam_code:
                raise InvariantError("a schema has no exam code")
            if self.from_schema is not None:
                raise InvariantError("a schema is not made from another schema")
        if self.duration_minutes is not None and self.duration_minutes < 1:
            raise InvariantError(f"duration must be at least 1 minute, got {self.duration_minutes}")
        check_marks("exam total", self.total_marks, allow_zero=False)
        check_marks("mark step", self.mark_step, allow_zero=False)
        if not self.sections:
            raise InvariantError("a blueprint needs at least one section")
        _check_unique("section labels", [s.label for s in self.sections])
        _check_unique("question labels", [q.label for q in self.slots()])
        computed = sum((s.max_marks for s in self.sections), Decimal(0))
        if computed != self.total_marks:
            raise InvariantError(f"sections add up to {computed}, exam total is {self.total_marks}")
        if self.kind is BlueprintKind.SCHEMA and len(self.unlinked_leaves()) != sum(
            len(leaves(s)) for s in self.slots()
        ):
            raise InvariantError("a schema leaves every question unlinked")

    @property
    def ref(self) -> ContentRef:
        return ContentRef(kind=ContentKind.BLUEPRINT, id=self.id, version=self.meta.version)

    def slots(self) -> Iterator[QuestionSlot]:
        for section in self.sections:
            yield from section.slots()

    def leaf(self, label: str) -> tuple[QuestionSlot, QuestionId, Decimal]:
        """The slot, question id and marks of a leaf label such as ``"7"`` or ``"12.a"``.
        Raises InvariantError while the leaf is not linked to a question."""
        for s in self.slots():
            for name, question_id, marks in leaves(s):
                if name == label:
                    if question_id is None:
                        raise InvariantError(f"question {label} has no question linked yet")
                    return s, question_id, marks
        raise KeyError(label)

    def unlinked_leaves(self) -> tuple[str, ...]:
        """Labels of the leaves that do not point at a question yet, in paper order."""
        return tuple(
            name for s in self.slots() for name, question_id, _ in leaves(s) if question_id is None
        )

    def slot(self, label: str) -> QuestionSlot:
        for s in self.slots():
            if s.label == label:
                return s
        raise KeyError(label)


def _check_unique(what: str, values: list[str]) -> None:
    if len(set(values)) != len(values):
        raise InvariantError(f"{what} must be unique")


def leaf_label(slot: QuestionSlot, part: SubPart | None = None) -> str:
    """Label of an answerable leaf: ``"7"`` for a plain slot, ``"12.a"`` for a sub-part.
    ``Answer.slot_label`` holds one of these."""
    return slot.label if part is None else f"{slot.label}.{part.label}"


def leaves(slot: QuestionSlot) -> tuple[tuple[str, QuestionId | None, Decimal], ...]:
    """(leaf label, question id or None, marks) for each answerable part of the slot."""
    if slot.parts:
        return tuple((leaf_label(slot, p), p.question_id, p.marks) for p in slot.parts)
    return ((leaf_label(slot), slot.question_id, slot.marks),)


@dataclass(frozen=True, slots=True, kw_only=True)
class BlueprintQuery:
    """Which blueprints a list shows. ``subject_ids`` None = every subject of the college;
    ``title`` matches anywhere and ``target_batch`` exactly, both ignoring case."""

    kind: BlueprintKind | None = None
    subject_ids: frozenset[SubjectId] | None = None
    title: str = ""
    target_batch: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class BlueprintSummary:
    """The newest version of a blueprint as the lists show it, read without its sections.
    ``used_in``: the papers (newest versions) whose ``from_schema`` is this blueprint."""

    id: BlueprintId
    version: int
    kind: BlueprintKind
    subject_id: SubjectId
    title: str
    course_code: str
    exam_code: str
    target_batch: str
    weightage_note: str
    description: str
    descriptor: str
    from_schema: BlueprintId | None
    total_marks: Decimal
    section_count: int
    saved_at: datetime | None
    used_in: int = 0

    def __post_init__(self) -> None:
        if self.version < 1 or self.section_count < 0 or self.used_in < 0:
            raise InvariantError("blueprint summary: version, sections and uses out of range")


def blueprint_summary(blueprint: ExamBlueprint, used_in: int = 0) -> BlueprintSummary:
    return BlueprintSummary(
        id=blueprint.id,
        version=blueprint.meta.version,
        kind=blueprint.kind,
        subject_id=blueprint.subject_id,
        title=blueprint.title,
        course_code=blueprint.course_code,
        exam_code=blueprint.exam_code,
        target_batch=blueprint.target_batch,
        weightage_note=blueprint.weightage_note,
        description=blueprint.description,
        descriptor=blueprint.descriptor,
        from_schema=blueprint.from_schema,
        total_marks=blueprint.total_marks,
        section_count=len(blueprint.sections),
        saved_at=blueprint.saved_at,
        used_in=used_in,
    )
