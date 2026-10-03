"""What the development seed is made of: plain descriptions of colleges, questions, rubrics and
papers, with a short vocabulary to write them (``lst``, ``sem``, ``num``, ``dia``).

Everything here is invented or written from the faculty keys and the question papers; no name,
USN or handwriting from ``samples/`` appears in it (CLAUDE.md, P8)."""

from dataclasses import dataclass
from decimal import Decimal

from tarn_core.domain.content import (
    CriterionType,
    DiagramComponent,
    Difficulty,
)

SYNTHETIC_SOURCE = "SYNTHETIC - dev only, needs teacher validation"
"""Label of every key written for development (D18). It is what ``ReferenceAnswer.synthetic``
means; the web app shows it as a badge on the key."""


@dataclass(frozen=True, slots=True)
class Term:
    """One item of a list criterion, with the words that also count."""

    term: str
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CriterionSeed:
    label: str
    type: CriterionType
    weight: Decimal
    # list
    items: tuple[Term, ...] = ()
    need: int = 0
    # numeric
    expected: Decimal = Decimal(0)
    tolerance: Decimal = Decimal(0)
    unit: str = ""
    # semantic
    statement: str = ""
    # diagram (the key of a DiagramSeed of the same question)
    diagram: str = ""
    component: DiagramComponent = DiagramComponent.WHOLE


@dataclass(frozen=True, slots=True)
class AnswerSeed:
    text: str
    synthetic: bool = False
    guidance_only: bool = False


@dataclass(frozen=True, slots=True)
class DiagramSeed:
    key: str
    """Name used by criteria."""
    file: str
    """Name of the PNG in the adapters' seed data directory."""


@dataclass(frozen=True, slots=True)
class QuestionSeed:
    code: str
    text: str
    marks: Decimal
    topic: str
    difficulty: Difficulty
    answers: tuple[AnswerSeed, ...]
    criteria: tuple[CriterionSeed, ...] = ()
    terms: tuple[str, ...] = ()
    """The teacher's glossary terms."""
    diagrams: tuple[DiagramSeed, ...] = ()

    def __post_init__(self) -> None:
        # The seed must be loadable: weights add up unless the key is guidance only.
        total = sum((c.weight for c in self.criteria), Decimal(0))
        if self.criteria and total != self.marks:
            raise ValueError(f"{self.code}: criteria add up to {total}, question has {self.marks}")
        if not self.criteria and not all(a.guidance_only for a in self.answers):
            raise ValueError(f"{self.code}: no criteria, so every key must be guidance only")
        keys = {d.key for d in self.diagrams}
        for c in self.criteria:
            if c.type is CriterionType.DIAGRAM and c.diagram not in keys:
                raise ValueError(f"{self.code}: criterion {c.label!r} names an unknown diagram")


@dataclass(frozen=True, slots=True)
class SubjectSeed:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class PersonSeed:
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class StudentSeed:
    name: str
    usn: str
    class_section: str


@dataclass(frozen=True, slots=True)
class PaperSeed:
    """An exam blueprint as the public document, with question codes in place of ids."""

    title: str
    subject_code: str
    document: dict[str, object]
    """``schema_version`` 1.0 document; ``question_id`` entries hold ``@CODE`` references."""


@dataclass(frozen=True, slots=True)
class CollegeSeed:
    institution_id: str
    name: str
    admin: PersonSeed
    teachers: tuple[PersonSeed, ...]
    students: tuple[StudentSeed, ...]
    subjects: tuple[SubjectSeed, ...]
    questions: tuple[tuple[str, QuestionSeed], ...]
    """(subject code, question): this college owns them."""
    papers: tuple[PaperSeed, ...]


# ---- vocabulary -----------------------------------------------------------------------------


def _d(value: str | int | float) -> Decimal:
    return Decimal(str(value))


def term(text: str, *synonyms: str) -> Term:
    return Term(text, tuple(synonyms))


def lst(
    label: str, weight: str | float, items: list[Term], need: int | None = None
) -> CriterionSeed:
    """Credit = matched items / ``need`` (default: all of them), at most 1."""
    return CriterionSeed(
        label=label,
        type=CriterionType.LIST,
        weight=_d(weight),
        items=tuple(items),
        need=len(items) if need is None else need,
    )


def sem(label: str, weight: str | float, statement: str) -> CriterionSeed:
    return CriterionSeed(
        label=label, type=CriterionType.SEMANTIC, weight=_d(weight), statement=statement
    )


def num(
    label: str,
    weight: str | float,
    expected: str | float,
    tolerance: str | float = 0,
    unit: str = "",
) -> CriterionSeed:
    return CriterionSeed(
        label=label,
        type=CriterionType.NUMERIC,
        weight=_d(weight),
        expected=_d(expected),
        tolerance=_d(tolerance),
        unit=unit,
    )


def dia(
    label: str,
    weight: str | float,
    diagram: str,
    component: DiagramComponent = DiagramComponent.WHOLE,
) -> CriterionSeed:
    return CriterionSeed(
        label=label,
        type=CriterionType.DIAGRAM,
        weight=_d(weight),
        diagram=diagram,
        component=component,
    )


def dia_parts(
    label: str,
    diagram: str,
    nodes: str | float,
    edges: str | float,
    labels: str | float,
) -> list[CriterionSeed]:
    """A diagram's marks split into nodes, edges and labels (R6, C23)."""
    return [
        dia(f"{label}: nodes", nodes, diagram, DiagramComponent.NODES),
        dia(f"{label}: edges", edges, diagram, DiagramComponent.EDGES),
        dia(f"{label}: labels", labels, diagram, DiagramComponent.LABELS),
    ]


def key(text: str) -> AnswerSeed:
    """A faculty key."""
    return AnswerSeed(text=text)


def synthetic(text: str) -> AnswerSeed:
    """A key written for development: shown as SYNTHETIC - dev only (D18)."""
    return AnswerSeed(text=text, synthetic=True)


def guidance(text: str) -> AnswerSeed:
    """A key that only says what the student has to do: marked by hand (C8)."""
    return AnswerSeed(text=text, guidance_only=True)


def question(
    code: str,
    text: str,
    marks: str | float,
    topic: str,
    answers: list[AnswerSeed],
    criteria: list[CriterionSeed] | None = None,
    *,
    difficulty: Difficulty = Difficulty.MEDIUM,
    terms: list[str] | None = None,
    diagrams: list[DiagramSeed] | None = None,
) -> QuestionSeed:
    return QuestionSeed(
        code=code,
        text=text,
        marks=_d(marks),
        topic=topic,
        difficulty=difficulty,
        answers=tuple(answers),
        criteria=tuple(criteria or ()),
        terms=tuple(terms or ()),
        diagrams=tuple(diagrams or ()),
    )
