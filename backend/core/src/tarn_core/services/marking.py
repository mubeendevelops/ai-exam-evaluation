"""Pure marking rules: negative marking, OR pairs and "any N of M" (C12, C19, D9)."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from tarn_core.domain.blueprint import ExamBlueprint, OrGroup, QuestionSlot, leaves
from tarn_core.domain.content import Question
from tarn_core.errors import InvariantError
from tarn_core.ids import QuestionId


class Outcome(StrEnum):
    COUNTED = "counted"
    NOT_ATTEMPTED = "not attempted"
    NOT_COUNTED_BEST_N = "not counted: best N"
    NOT_COUNTED_OR = "not counted: other OR alternative scored higher"


@dataclass(frozen=True, slots=True, kw_only=True)
class SlotResult:
    section_label: str
    slot_label: str
    mark: Decimal | None
    outcome: Outcome

    @property
    def counted(self) -> bool:
        return self.outcome is Outcome.COUNTED


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionResult:
    label: str
    slots: tuple[SlotResult, ...]
    total: Decimal
    max_marks: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class ExamResult:
    sections: tuple[SectionResult, ...]
    total: Decimal
    max_marks: Decimal

    @property
    def slots(self) -> tuple[SlotResult, ...]:
        return tuple(s for sec in self.sections for s in sec.slots)


def slot_mark(slot: QuestionSlot, earned: Decimal) -> Decimal:
    """Final mark of an attempted slot: ``earned`` in [0, slot.marks], or minus the
    slot's negative marks when an attempted answer earns nothing."""
    if not 0 <= earned <= slot.marks:
        raise InvariantError(f"slot {slot.label}: mark {earned} outside 0..{slot.marks}")
    if earned == 0 and slot.negative_marks > 0:
        return -slot.negative_marks
    return earned


def apply_choice_rules(blueprint: ExamBlueprint, marks: Mapping[str, Decimal]) -> ExamResult:
    """Decide which attempted slots count.

    ``marks`` maps attempted slot labels to final slot marks (see ``slot_mark``); a slot
    missing from it was not attempted. In each section, every OR group keeps its highest
    alternative (ties: the earlier one), then the section keeps its best N items (ties:
    paper order). Fewer than N attempted means all count."""
    known = {s.label: s for s in blueprint.slots()}
    for label, mark in marks.items():
        slot = known.get(label)
        if slot is None:
            raise InvariantError(f"no slot {label!r} in the blueprint")
        if not -slot.negative_marks <= mark <= slot.marks:
            raise InvariantError(f"slot {label}: mark {mark} outside its range")

    sections: list[SectionResult] = []
    for section in blueprint.sections:
        outcome: dict[str, Outcome] = {}
        candidates: list[tuple[Decimal, int, str]] = []  # (mark, paper position, slot label)
        for position, item in enumerate(section.items):
            alternatives = item.alternatives if isinstance(item, OrGroup) else (item,)
            attempted = [a for a in alternatives if a.label in marks]
            for a in alternatives:
                outcome[a.label] = Outcome.NOT_ATTEMPTED
            if not attempted:
                continue
            best = max(attempted, key=lambda a: (marks[a.label], -alternatives.index(a)))
            for a in attempted:
                outcome[a.label] = Outcome.NOT_COUNTED_OR
            candidates.append((marks[best.label], position, best.label))

        ranked = sorted(candidates, key=lambda c: (-c[0], c[1]))
        for rank, (_, _, label) in enumerate(ranked):
            outcome[label] = (
                Outcome.COUNTED if rank < section.counted_items else Outcome.NOT_COUNTED_BEST_N
            )

        results = tuple(
            SlotResult(
                section_label=section.label,
                slot_label=s.label,
                mark=marks.get(s.label),
                outcome=outcome[s.label],
            )
            for s in section.slots()
        )
        total = sum((r.mark for r in results if r.counted and r.mark is not None), Decimal(0))
        sections.append(
            SectionResult(
                label=section.label, slots=results, total=total, max_marks=section.max_marks
            )
        )

    return ExamResult(
        sections=tuple(sections),
        total=sum((s.total for s in sections), Decimal(0)),
        max_marks=blueprint.total_marks,
    )


def check_blueprint_questions(
    blueprint: ExamBlueprint, questions: Mapping[QuestionId, Question]
) -> None:
    """Every leaf of the blueprint must point at a known question with the same max marks."""
    for slot in blueprint.slots():
        for label, question_id, marks in leaves(slot):
            if question_id is None:
                raise InvariantError(f"leaf {label}: no question linked yet")
            question = questions.get(question_id)
            if question is None:
                raise InvariantError(f"leaf {label}: unknown question {question_id}")
            if question.max_marks != marks:
                raise InvariantError(
                    f"leaf {label} carries {marks} marks, question has {question.max_marks}"
                )
