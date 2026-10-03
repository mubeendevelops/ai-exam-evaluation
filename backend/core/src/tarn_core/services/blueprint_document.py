"""The exam blueprint as a JSON document (public schema ``docs/api/blueprint.schema.json``, R3).

``check_document`` reads a document the way a teacher would want it explained: it collects
*every* problem with a path and a plain sentence instead of stopping at the first, and it checks
the totals with the choice rules ("any N of M", OR pairs) applied. ``blueprint_to_document`` is
the way back. Plain data in, plain data out; the structure rules here and the JSON Schema file
are kept in step by a contract test in ``tarn_adapters``."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from uuid import UUID

from tarn_core.domain.blueprint import (
    OBJECTIVE_METHODS,
    AllOf,
    AnyN,
    BlueprintItem,
    ChoiceRule,
    EvaluationMethod,
    ExamBlueprint,
    OrGroup,
    QuestionSlot,
    Section,
    Step,
    SubPart,
)
from tarn_core.domain.common import JsonValue
from tarn_core.domain.content import ContentMeta
from tarn_core.errors import InvariantError
from tarn_core.ids import BlueprintId, QuestionId, SubjectId

SCHEMA_VERSION = "1.0"
NEGATIVE_MARKING_VALUES = (Decimal(0), Decimal("0.25"), Decimal("0.5"))
MARK_STEPS = (Decimal("0.25"), Decimal("0.5"), Decimal(1))
DEFAULT_MARK_STEP = Decimal("0.5")

MAX_SECTIONS = 20
MAX_ITEMS = 100
MAX_PARTS = 10
MAX_STEPS = 20
MAX_MARKS = Decimal(1000)
MAX_DURATION = 1440
MAX_TEXT = 200

METHOD_NAMES = {
    EvaluationMethod.EXACT_PATTERN_MATCH: "Exact Pattern Match",
    EvaluationMethod.OMR_BUBBLE_SCAN: "OMR Bubble Scan",
    EvaluationMethod.KEYWORD_FORMULA: "Keyword + Formula",
    EvaluationMethod.SEMANTIC_RUBRIC: "Semantic Rubric",
    EvaluationMethod.DIAGRAM: "Diagram",
}

_TOP_KEYS = (
    "schema_version",
    "title",
    "course_code",
    "subject_id",
    "duration_minutes",
    "total_marks",
    "negative_marking",
    "mark_step",
    "sections",
)
_SECTION_KEYS = ("label", "title", "method", "choice", "items")
_QUESTION_KEYS = ("label", "marks", "question_id", "parts", "steps")
_PART_KEYS = ("label", "marks", "question_id", "steps")
_STEP_KEYS = ("label", "marks")
_MARKS_SHAPE = re.compile(r"^\d+(\.\d{1,2})?$")


@dataclass(frozen=True, slots=True)
class Issue:
    """One problem: where it is (``sections[1].items[3].marks``) and what is wrong."""

    path: str
    message: str


@dataclass(frozen=True, slots=True)
class SectionSummary:
    label: str
    method: EvaluationMethod
    items: int
    counted: int
    max_marks: Decimal

    @property
    def describe(self) -> str:
        how = "all" if self.counted == self.items else f"best {self.counted} of"
        return f"{self.label}: {self.max_marks:f} ({how} {self.items})"


@dataclass(frozen=True, slots=True)
class DocumentReport:
    """What ``check_document`` found. ``blueprint`` is set only when there are no issues; it is
    the values to store (the caller supplies id and ownership)."""

    issues: tuple[Issue, ...]
    warnings: tuple[Issue, ...]
    sections: tuple[SectionSummary, ...]
    computed_total: Decimal | None
    question_count: int
    unlinked: tuple[str, ...]
    parsed: "ParsedBlueprint | None"

    @property
    def valid(self) -> bool:
        return not self.issues


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsedBlueprint:
    subject_id: SubjectId
    title: str
    course_code: str
    duration_minutes: int | None
    total_marks: Decimal
    mark_step: Decimal
    sections: tuple[Section, ...]

    def build(self, *, id: BlueprintId, meta: ContentMeta) -> ExamBlueprint:
        return ExamBlueprint(
            id=id,
            meta=meta,
            subject_id=self.subject_id,
            title=self.title,
            course_code=self.course_code,
            duration_minutes=self.duration_minutes,
            total_marks=self.total_marks,
            mark_step=self.mark_step,
            sections=self.sections,
        )


def check_document(document: object) -> DocumentReport:
    return _Checker().run(document)


# --- reading ----------------------------------------------------------------------------------


class _Checker:
    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.warnings: list[Issue] = []

    def bad(self, path: str, message: str) -> None:
        self.issues.append(Issue(path, message))

    # primitives: each returns None after reporting, so callers just skip the value

    def obj(self, value: object, path: str, keys: tuple[str, ...]) -> Mapping[str, object] | None:
        if not isinstance(value, Mapping):
            self.bad(path or "(document)", "must be an object")
            return None
        for key in value:
            if key not in keys:
                self.bad(
                    f"{path}.{key}" if path else key, f"unknown field; allowed: {', '.join(keys)}"
                )
        return value

    def array(
        self, value: object, path: str, *, low: int, high: int, what: str
    ) -> list[object] | None:
        if not isinstance(value, list):
            self.bad(path, "must be a list")
            return None
        if len(value) < low:
            self.bad(path, f"needs at least {low} {what}")
            return None
        if len(value) > high:
            self.bad(path, f"allows at most {high} {what}")
            return None
        return value

    def text(
        self, o: Mapping[str, object], key: str, path: str, *, required: bool = True
    ) -> str | None:
        where = f"{path}.{key}" if path else key
        if key not in o:
            if required:
                self.bad(where, "is required")
            return None
        value = o[key]
        if not isinstance(value, str):
            self.bad(where, "must be text")
            return None
        value = value.strip()
        if required and not value:
            self.bad(where, "must not be empty")
            return None
        if len(value) > MAX_TEXT:
            self.bad(where, f"is longer than {MAX_TEXT} characters")
            return None
        return value

    def number(self, value: object, path: str, *, positive: bool = True) -> Decimal | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.bad(path, "must be a number")
            return None
        try:
            number = Decimal(str(value))
        except InvalidOperation:  # pragma: no cover - str(float) is always parseable
            self.bad(path, "must be a number")
            return None
        if not number.is_finite() or number < 0 or (positive and number == 0):
            self.bad(path, "must be greater than 0" if positive else "must not be negative")
            return None
        if number > MAX_MARKS:
            self.bad(path, f"must not exceed {MAX_MARKS}")
            return None
        if not _MARKS_SHAPE.match(f"{number:f}"):
            self.bad(path, "uses at most 2 decimal places")
            return None
        return number

    def marks_of(self, o: Mapping[str, object], key: str, path: str) -> Decimal | None:
        where = f"{path}.{key}"
        if key not in o:
            self.bad(where, "is required")
            return None
        return self.number(o[key], where)

    def integer(self, value: object, path: str, *, low: int, high: int) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            self.bad(path, "must be a whole number")
            return None
        if not low <= value <= high:
            self.bad(path, f"must be between {low} and {high}")
            return None
        return value

    def uuid(self, value: object, path: str) -> UUID | None:
        if not isinstance(value, str):
            self.bad(path, "must be a question id (UUID) or null")
            return None
        try:
            return UUID(value)
        except ValueError:
            self.bad(path, "is not a valid id (UUID)")
            return None

    # the document

    def run(self, document: object) -> DocumentReport:
        top = self.obj(document, "", _TOP_KEYS)
        if top is None:
            return self.report(None, ())
        version = top.get("schema_version")
        if version != SCHEMA_VERSION:
            self.bad("schema_version", f'must be "{SCHEMA_VERSION}"')
        title = self.text(top, "title", "")
        course_code = self.text(top, "course_code", "")
        subject_id = self.subject(top)
        duration = self.duration(top)
        total = self.total(top)
        negative = self.negative_marking(top)
        mark_step = self.mark_step(top)

        sections: list[Section] = []
        raw_sections = self.array(
            top.get("sections"), "sections", low=1, high=MAX_SECTIONS, what="sections"
        )
        for i, raw in enumerate(raw_sections or []):
            section = self.section(raw, f"sections[{i}]", negative or Decimal(0))
            if section is not None:
                sections.append(section)
        complete = raw_sections is not None and len(sections) == len(raw_sections)
        if complete:
            self.unique_question_labels(sections)

        summaries = tuple(
            SectionSummary(
                label=s.label,
                method=s.method,
                items=len(s.items),
                counted=s.counted_items,
                max_marks=s.max_marks,
            )
            for s in sections
        )
        computed = sum((s.max_marks for s in summaries), Decimal(0)) if complete else None
        if complete and total is not None and computed is not None and computed != total:
            detail = ", ".join(s.describe for s in summaries)
            self.bad(
                "total_marks",
                f"The exam total is {total:f} but the sections add up to {computed:f} ({detail}). "
                "Change the total or the sections.",
            )
        if (
            complete
            and negative is not None
            and negative > 0
            and not any(s.method in OBJECTIVE_METHODS for s in sections)
        ):
            self.warnings.append(
                Issue(
                    "negative_marking",
                    "Negative marking applies only to sections evaluated by Exact Pattern Match "
                    "or OMR Bubble Scan; no section uses either, so it has no effect.",
                )
            )

        parsed: ParsedBlueprint | None = None
        if (
            not self.issues
            and title is not None
            and course_code is not None
            and subject_id is not None
            and total is not None
            and mark_step is not None
        ):
            parsed = ParsedBlueprint(
                subject_id=subject_id,
                title=title,
                course_code=course_code,
                duration_minutes=duration,
                total_marks=total,
                mark_step=mark_step,
                sections=tuple(sections),
            )
        return self.report(parsed, summaries, computed, sections)

    def report(
        self,
        parsed: ParsedBlueprint | None,
        summaries: tuple[SectionSummary, ...],
        computed: Decimal | None = None,
        sections: Iterable[Section] = (),
    ) -> DocumentReport:
        slots = [slot for s in sections for slot in s.slots()]
        unlinked = tuple(
            label for s in sections for slot in s.slots() for label in _unlinked_labels(slot)
        )
        return DocumentReport(
            issues=tuple(self.issues),
            warnings=tuple(self.warnings),
            sections=summaries,
            computed_total=computed,
            question_count=len(slots),
            unlinked=unlinked,
            parsed=parsed,
        )

    # header fields

    def subject(self, top: Mapping[str, object]) -> SubjectId | None:
        if "subject_id" not in top:
            self.bad("subject_id", "is required: pick or create a subject")
            return None
        found = self.uuid(top["subject_id"], "subject_id")
        return None if found is None else SubjectId(found)

    def duration(self, top: Mapping[str, object]) -> int | None:
        if top.get("duration_minutes") is None:
            return None  # optional: an exam without a stated length
        return self.integer(top["duration_minutes"], "duration_minutes", low=1, high=MAX_DURATION)

    def total(self, top: Mapping[str, object]) -> Decimal | None:
        if "total_marks" not in top:
            self.bad("total_marks", "is required")
            return None
        return self.number(top["total_marks"], "total_marks")

    def negative_marking(self, top: Mapping[str, object]) -> Decimal | None:
        value = top.get("negative_marking", 0)
        number = self.number(value, "negative_marking", positive=False)
        if number is not None and number not in NEGATIVE_MARKING_VALUES:
            self.bad("negative_marking", "must be 0, 0.25 or 0.5 marks per wrong answer")
            return None
        return number

    def mark_step(self, top: Mapping[str, object]) -> Decimal | None:
        if "mark_step" not in top:
            return DEFAULT_MARK_STEP
        number = self.number(top["mark_step"], "mark_step")
        if number is not None and number not in MARK_STEPS:
            self.bad("mark_step", "must be 0.25, 0.5 or 1: the unit marks are rounded to")
            return None
        return number

    # sections

    def section(self, raw: object, path: str, negative: Decimal) -> Section | None:
        o = self.obj(raw, path, _SECTION_KEYS)
        if o is None:
            return None
        before = len(self.issues)
        label = self.text(o, "label", path)
        title = self.text(o, "title", path, required=False) or ""
        method = self.method(o, path)
        raw_items = self.array(
            o.get("items"), f"{path}.items", low=1, high=MAX_ITEMS, what="questions"
        )
        items: list[BlueprintItem] = []
        for j, raw_item in enumerate(raw_items or []):
            item = self.item(raw_item, f"{path}.items[{j}]")
            if item is not None:
                items.append(item)
        rule = self.choice(o, f"{path}.choice", len(raw_items) if raw_items else 0, label)
        if len(self.issues) > before or label is None or method is None or rule is None:
            return None
        if method in OBJECTIVE_METHODS and negative > 0:
            items = [_with_negative(i, negative) for i in items]
        return Section(label=label, title=title, method=method, rule=rule, items=tuple(items))

    def method(self, o: Mapping[str, object], path: str) -> EvaluationMethod | None:
        if "method" not in o:
            self.bad(f"{path}.method", "is required")
            return None
        try:
            return EvaluationMethod(str(o["method"]))
        except ValueError:
            allowed = ", ".join(m.value for m in EvaluationMethod)
            self.bad(f"{path}.method", f"must be one of: {allowed}")
            return None

    def choice(
        self, section: Mapping[str, object], path: str, count: int, label: str | None
    ) -> ChoiceRule | None:
        if "choice" not in section:
            return AllOf()
        o = self.obj(section["choice"], path, ("rule", "n"))
        if o is None:
            return None
        rule = o.get("rule")
        if rule == "all":
            if "n" in o:
                self.bad(f"{path}.n", 'only goes with rule "any"')
            return AllOf()
        if rule != "any":
            self.bad(f"{path}.rule", 'must be "all" or "any"')
            return None
        n = self.integer(o.get("n"), f"{path}.n", low=1, high=MAX_ITEMS)
        if n is None:
            return None
        if count and n > count:
            name = f"Section {label}" if label else "This section"
            self.bad(
                f"{path}.n",
                f"{name}: answering any {n} of {count} is impossible; choose between 1 and {count}",
            )
            return None
        return AnyN(n)

    # items: a question slot, or an OR group of slots

    def item(self, raw: object, path: str) -> BlueprintItem | None:
        if isinstance(raw, Mapping) and raw.get("type") == "or":
            o = self.obj(raw, path, ("type", "alternatives"))
            if o is None:
                return None
            alts_raw = self.array(
                o.get("alternatives"),
                f"{path}.alternatives",
                low=2,
                high=MAX_ITEMS,
                what="alternatives",
            )
            if alts_raw is None:
                return None
            alts = [
                self.slot(a, f"{path}.alternatives[{k}]", typed=False)
                for k, a in enumerate(alts_raw)
            ]
            if any(a is None for a in alts):
                return None
            slots = tuple(a for a in alts if a is not None)
            marks = sorted({a.marks for a in slots})
            if len(marks) > 1:
                names = " or ".join(f"{a.label} ({a.marks:f})" for a in slots)
                self.bad(
                    path,
                    f"OR pair {names}: alternatives must carry equal marks; "
                    f"found {', '.join(f'{m:f}' for m in marks)}",
                )
                return None
            return OrGroup(alternatives=slots)
        if isinstance(raw, Mapping) and raw.get("type") != "question":
            self.bad(f"{path}.type", 'must be "question" or "or"')
            return None
        return self.slot(raw, path, typed=True)

    def slot(self, raw: object, path: str, *, typed: bool) -> QuestionSlot | None:
        o = self.obj(raw, path, (("type",) if typed else ()) + _QUESTION_KEYS)
        if o is None:
            return None
        before = len(self.issues)
        label = self.text(o, "label", path)
        marks = self.marks_of(o, "marks", path)
        question_id = self.link(o, path)
        parts = self.parts(o, path, label, marks)
        steps = self.steps(o, f"{path}.steps", marks, label)
        if parts and question_id is not None:
            self.bad(
                path, f"Question {label}: link the question on each sub-part, not on the question"
            )
        if parts and steps:
            self.bad(f"{path}.steps", f"Question {label}: put step marks on its sub-parts")
        if len(self.issues) > before or label is None or marks is None:
            return None
        return QuestionSlot(
            label=label, marks=marks, question_id=question_id, parts=tuple(parts), steps=steps
        )

    def link(self, o: Mapping[str, object], path: str) -> QuestionId | None:
        raw = o.get("question_id")
        if raw is None:
            return None
        found = self.uuid(raw, f"{path}.question_id")
        return None if found is None else QuestionId(found)

    def parts(
        self, o: Mapping[str, object], path: str, label: str | None, marks: Decimal | None
    ) -> list[SubPart]:
        if "parts" not in o or o["parts"] == []:
            return []
        raw = self.array(o["parts"], f"{path}.parts", low=1, high=MAX_PARTS, what="sub-parts")
        parts: list[SubPart] = []
        for k, p in enumerate(raw or []):
            ppath = f"{path}.parts[{k}]"
            po = self.obj(p, ppath, _PART_KEYS)
            if po is None:
                continue
            before = len(self.issues)
            plabel = self.text(po, "label", ppath)
            pmarks = self.marks_of(po, "marks", ppath)
            plink = self.link(po, ppath)
            psteps = self.steps(po, f"{ppath}.steps", pmarks, f"{label}.{plabel}")
            if len(self.issues) == before and plabel is not None and pmarks is not None:
                parts.append(SubPart(label=plabel, marks=pmarks, question_id=plink, steps=psteps))
        if raw is not None and len(parts) == len(raw):
            names = [p.label for p in parts]
            if len(set(names)) != len(names):
                self.bad(f"{path}.parts", f"Question {label}: sub-part labels must be different")
            total = sum((p.marks for p in parts), Decimal(0))
            if marks is not None and total != marks:
                split = " + ".join(f"{p.label} {p.marks:f}" for p in parts)
                self.bad(
                    f"{path}.parts",
                    f"Question {label}: the sub-parts add up to {total:f} ({split}) "
                    f"but the question carries {marks:f} marks",
                )
        return parts

    def steps(
        self, o: Mapping[str, object], path: str, marks: Decimal | None, owner: str | None
    ) -> tuple[Step, ...]:
        if "steps" not in o or o["steps"] == []:
            return ()
        items = self.array(o["steps"], path, low=1, high=MAX_STEPS, what="steps")
        steps: list[Step] = []
        for k, s in enumerate(items or []):
            spath = f"{path}[{k}]"
            so = self.obj(s, spath, _STEP_KEYS)
            if so is None:
                continue
            before = len(self.issues)
            slabel = self.text(so, "label", spath)
            smarks = self.marks_of(so, "marks", spath)
            if len(self.issues) == before and slabel is not None and smarks is not None:
                steps.append(Step(label=slabel, marks=smarks))
        if items is not None and len(steps) == len(items):
            names = [s.label for s in steps]
            if len(set(names)) != len(names):
                self.bad(path, f"Question {owner}: step labels must be different")
            total = sum((s.marks for s in steps), Decimal(0))
            if marks is not None and total != marks:
                self.bad(
                    path,
                    f"Question {owner}: the step marks add up to {total:f} "
                    f"but it carries {marks:f} marks",
                )
        return tuple(steps)

    def unique_question_labels(self, sections: list[Section]) -> None:
        seen: dict[str, str] = {}
        for section in sections:
            for slot in section.slots():
                if slot.label in seen:
                    where = (
                        f"section {section.label}"
                        if seen[slot.label] == section.label
                        else f"sections {seen[slot.label]} and {section.label}"
                    )
                    self.bad("sections", f"Question number {slot.label} is used twice ({where})")
                seen.setdefault(slot.label, section.label)
        names = [s.label for s in sections]
        if len(set(names)) != len(names):
            self.bad("sections", "Section labels must be different")


def _unlinked_labels(slot: QuestionSlot) -> Iterable[str]:
    if slot.parts:
        return (f"{slot.label}.{p.label}" for p in slot.parts if p.question_id is None)
    return (slot.label,) if slot.question_id is None else ()


def _with_negative(item: BlueprintItem, negative: Decimal) -> BlueprintItem:
    if isinstance(item, OrGroup):
        return OrGroup(
            alternatives=tuple(replace(a, negative_marks=negative) for a in item.alternatives)
        )
    return replace(item, negative_marks=negative)


# --- the way back -----------------------------------------------------------------------------


def _num(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _step_json(steps: tuple[Step, ...]) -> list[JsonValue]:
    return [{"label": s.label, "marks": _num(s.marks)} for s in steps]


def _slot_json(slot: QuestionSlot, *, typed: bool) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {"type": "question"} if typed else {}
    out.update(
        label=slot.label,
        marks=_num(slot.marks),
        question_id=None if slot.question_id is None else str(slot.question_id),
    )
    if slot.parts:
        out["parts"] = [
            {
                "label": p.label,
                "marks": _num(p.marks),
                "question_id": None if p.question_id is None else str(p.question_id),
                **({"steps": _step_json(p.steps)} if p.steps else {}),
            }
            for p in slot.parts
        ]
    if slot.steps:
        out["steps"] = _step_json(slot.steps)
    return out


def blueprint_to_document(blueprint: ExamBlueprint) -> dict[str, JsonValue]:
    """The public form. Negative marking is exam-wide in the document: the value carried by
    the slots of objective sections (the document builder sets them all alike)."""
    negative = max(
        (
            slot.negative_marks
            for section in blueprint.sections
            if section.method in OBJECTIVE_METHODS
            for slot in section.slots()
        ),
        default=Decimal(0),
    )

    def item_json(item: BlueprintItem) -> dict[str, JsonValue]:
        if isinstance(item, OrGroup):
            return {
                "type": "or",
                "alternatives": [_slot_json(a, typed=False) for a in item.alternatives],
            }
        return _slot_json(item, typed=True)

    sections: list[JsonValue] = []
    for s in blueprint.sections:
        choice: dict[str, JsonValue] = (
            {"rule": "any", "n": s.rule.n} if isinstance(s.rule, AnyN) else {"rule": "all"}
        )
        sections.append(
            {
                "label": s.label,
                "title": s.title,
                "method": s.method.value,
                "choice": choice,
                "items": [item_json(i) for i in s.items],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "title": blueprint.title,
        "course_code": blueprint.course_code,
        "subject_id": str(blueprint.subject_id),
        "duration_minutes": blueprint.duration_minutes,
        "total_marks": _num(blueprint.total_marks),
        "negative_marking": _num(negative),
        "mark_step": _num(blueprint.mark_step),
        "sections": sections,
    }


def require_valid(report: DocumentReport) -> ParsedBlueprint:
    """The parsed blueprint, or one ``InvariantError`` that lists every issue."""
    if report.parsed is None or report.issues:
        raise InvariantError(
            "The blueprint is not valid: "
            + "; ".join(f"{i.path}: {i.message}" for i in report.issues)
        )
    return report.parsed
