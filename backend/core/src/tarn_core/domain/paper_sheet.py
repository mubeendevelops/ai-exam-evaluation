"""What a question paper PDF shows, as plain data (P34, D224). The core builds it from one
paper version and the latest version of each linked question; a ``PaperRenderer`` adapter
draws it. It has no field for keys, rubrics or reference answers, and holds no student data,
so it may be cached."""

from dataclasses import dataclass
from decimal import Decimal

from tarn_core.domain.common import check_text


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperPart:
    label: str
    text: str | None
    """None while the sub-part is not linked to a question."""
    marks: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperQuestion:
    number: str
    text: str | None
    """None while not linked, or for a question split into sub-parts."""
    marks: Decimal
    parts: tuple[PaperPart, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperChoice:
    """An OR pair (or more): the student answers one of the alternatives."""

    alternatives: tuple[PaperQuestion, ...]


type PaperItem = PaperQuestion | PaperChoice


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperSection:
    label: str
    title: str
    rule: str
    """"Answer all questions." or "Answer any 2 of 5."."""
    negative_note: str
    """The negative marking of the section in words; empty when none."""
    items: tuple[PaperItem, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperDocument:
    college_name: str
    title: str
    exam_code: str
    course_code: str
    subject_name: str
    duration_minutes: int | None
    total_marks: Decimal
    version: int
    sections: tuple[PaperSection, ...]
    unlinked: int = 0
    """Questions not linked yet: the PDF says it is a draft."""

    def __post_init__(self) -> None:
        check_text("paper title", self.title)
