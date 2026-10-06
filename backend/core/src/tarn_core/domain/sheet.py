"""What a result sheet PDF shows, as plain data (P18). The core builds it from the review;
a ``SheetRenderer`` adapter draws it. It holds student text and names, so it is never logged
or audited: the only thing stored is the rendered PDF, under the booklet's folder."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tarn_core.domain.common import Box
from tarn_core.domain.review import ResultLine


@dataclass(frozen=True, slots=True, kw_only=True)
class SheetCriterion:
    label: str
    weight: Decimal
    credit: Decimal
    marks: Decimal
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class SheetDiagram:
    """A drawing of the student's answer: the cleaned page image and the box to cut from it.
    ``image`` is None when the page image is gone; the sheet then names the drawing only."""

    image: bytes | None
    box: Box
    caption: str


@dataclass(frozen=True, slots=True, kw_only=True)
class SheetAnswer:
    label: str
    """Leaf label of the question: ``"7"`` or ``"12.a"``."""
    section_label: str
    question: str
    """The question text (an excerpt)."""
    max_marks: Decimal
    ai_mark: Decimal | None
    teacher_mark: Decimal | None
    """None for an answer that was not attempted."""
    counted: bool
    outcome: str
    """Why the mark counts or not: the slot's outcome (``counted``, ``not counted: best N``…)."""
    excerpt: str
    """The student's text, struck-out lines left out, cut to a readable length."""
    diagrams: tuple[SheetDiagram, ...] = ()
    criteria: tuple[SheetCriterion, ...] = ()
    """The AI's per-criterion breakdown of the score the teacher approved."""
    tags: tuple[str, ...] = ()
    remarks: str = ""

    @property
    def overridden(self) -> bool:
        return (
            self.ai_mark is not None
            and self.teacher_mark is not None
            and self.ai_mark != self.teacher_mark
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class SheetDocument:
    college_name: str
    exam: str
    course: str
    """Course code and subject name, as far as the blueprint and subject give them."""
    student_name: str
    usn: str
    version: int
    total: Decimal
    max_marks: Decimal
    issued_by: str
    generated_at: datetime
    lines: tuple[ResultLine, ...]
    answers: tuple[SheetAnswer, ...]
    amendment_note: str = ""

    @property
    def not_counted(self) -> tuple[ResultLine, ...]:
        """Slots that were marked but do not enter the total (best N, OR): shown, not hidden."""
        return tuple(ln for ln in self.lines if ln.mark is not None and not ln.counted)
