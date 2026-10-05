"""Calibration sets: teacher-marked answers on disk (student data: keep them under the
git-ignored ``var/scoring/``).

A set is a folder with:

- ``answers.jsonl``: one answer per line, ``{"id": "...", "question": "<code>", "text": "...",
  "teacher_mark": "3.5" (optional)}``;
- ``marks.json`` (optional): ``{"marked_by": "teacher" | "claude" | "dataset",
  "marks": {"<id>": "3.5", …}}``; a mark here wins over one in ``answers.jsonl`` (so a rebuilt
  ``answers.jsonl`` keeps its marks);
- ``questions.json`` (optional, public datasets): ``{"<code>": {"text": "...", "max_marks":
  "5", "mark_step": "0.5", "key": "...", "statements": ["...", …]}}``: one semantic criterion
  per statement, weights splitting the marks evenly. Without it, the codes are looked up in
  the development seed (``QP-CI-Q12``…).

Answers without a mark are left out of fitting and reports."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.content import (
    ContentMeta,
    CriterionType,
    Glossary,
    Question,
    ReferenceAnswer,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.ids import CollegeId, CriterionId, QuestionId, UserId
from tarn_core.services.scoring.calibration import CalibrationQuestion, MarkedAnswer
from tarn_core.testing import InMemory

_NOBODY = UserId(uuid5(NAMESPACE_URL, "tarn:calibration:nobody"))
_NO_COLLEGE = CollegeId(uuid5(NAMESPACE_URL, "tarn:calibration:college"))


@dataclass(frozen=True, slots=True)
class CalibrationSet:
    name: str
    questions: tuple[CalibrationQuestion, ...]
    answers: tuple[MarkedAnswer, ...]
    """Marked answers only."""
    unmarked: int
    marked_by: str


def _rows(path: Path) -> list[dict[str, object]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_set(folder: Path, seeded: InMemory | None = None) -> CalibrationSet:
    rows = _rows(folder / "answers.jsonl")
    marks: Mapping[str, object] = {}
    marked_by = "dataset"
    marks_file = folder / "marks.json"
    if marks_file.exists():
        data = json.loads(marks_file.read_text(encoding="utf-8"))
        marks = data.get("marks", {})
        marked_by = str(data.get("marked_by", "teacher"))
    questions_file = folder / "questions.json"
    if questions_file.exists():
        questions = dataset_questions(json.loads(questions_file.read_text(encoding="utf-8")))
    else:
        if seeded is None:
            raise ValueError(f"{folder.name}: no questions.json and no seeded content to use")
        codes = {str(r["question"]) for r in rows}
        questions = seeded_questions(seeded, codes)
    answers = []
    unmarked = 0
    for row in rows:
        mark = marks.get(str(row.get("id")), row.get("teacher_mark"))
        if mark is None:
            unmarked += 1
            continue
        answers.append(
            MarkedAnswer(
                question=str(row["question"]),
                text=str(row["text"]),
                teacher_mark=Decimal(str(mark)),
                marked_by=marked_by,
            )
        )
    return CalibrationSet(
        name=folder.name,
        questions=tuple(questions),
        answers=tuple(answers),
        unmarked=unmarked,
        marked_by=marked_by,
    )


def dataset_questions(data: Mapping[str, Mapping[str, object]]) -> list[CalibrationQuestion]:
    meta = ContentMeta(owning_college_id=_NO_COLLEGE, created_by=_NOBODY)
    questions = []
    for code, q in data.items():
        max_marks = Decimal(str(q["max_marks"]))
        raw = q.get("statements") or [q["key"]]
        statements = [str(s) for s in raw] if isinstance(raw, list) else [str(raw)]
        statements = [s for s in statements if s.strip()]
        question_id = QuestionId(uuid5(NAMESPACE_URL, f"tarn:calibration:{code}"))
        weights = _split(max_marks, len(statements))
        criteria = tuple(
            RubricCriterion(
                id=CriterionId(uuid5(NAMESPACE_URL, f"tarn:calibration:{code}:{k}")),
                meta=meta,
                question_id=question_id,
                label=f"point {k + 1}",
                type=CriterionType.SEMANTIC,
                weight=w,
                params=SemanticParams(reference_statement=s),
            )
            for k, (s, w) in enumerate(zip(statements, weights, strict=True))
        )
        questions.append(
            CalibrationQuestion(
                code=code,
                text=str(q["text"]),
                max_marks=max_marks,
                criteria=criteria,
                key_texts=(str(q.get("key", "")),) if q.get("key") else (),
                mark_step=Decimal(str(q.get("mark_step", "0.5"))),
            )
        )
    return questions


def _split(total: Decimal, n: int) -> list[Decimal]:
    """``n`` weights adding up exactly to ``total`` (the last one takes the remainder)."""
    each = (total / n).quantize(Decimal("0.0001"))
    return [each] * (n - 1) + [total - each * (n - 1)]


def seeded_questions(seeded: InMemory, codes: set[str]) -> list[CalibrationQuestion]:
    """The seeded questions with these codes that have AI-scored criteria (guidance-only keys
    are marked manually and have nothing to calibrate)."""
    steps: dict[QuestionId, Decimal] = {}
    for blueprint in seeded.content.latest(ExamBlueprint):
        for slot in blueprint.slots():
            for _, question_id, _ in leaves(slot):
                if question_id is not None:
                    steps[question_id] = blueprint.mark_step
    out = []
    for question in seeded.content.latest(Question):
        if question.code not in codes:
            continue
        criteria = tuple(seeded.content.for_question(RubricCriterion, question.id))
        if not criteria:
            continue
        keys = [
            k.text
            for k in seeded.content.for_question(ReferenceAnswer, question.id)
            if not k.guidance_only
        ]
        glossaries = seeded.content.for_question(Glossary, question.id)
        off = [t for g in glossaries for t in g.off_target_terms]
        out.append(
            CalibrationQuestion(
                code=question.code,
                text=question.text,
                max_marks=question.max_marks,
                criteria=criteria,
                key_texts=tuple(keys),
                off_target_terms=tuple(off),
                mark_step=steps.get(question.id, Decimal("0.5")),
            )
        )
    return out


def write_answers(folder: Path, rows: Sequence[Mapping[str, object]]) -> Path:
    """Write ``answers.jsonl`` readable by the owner only (it holds student answers)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "answers.jsonl"
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    path.chmod(0o600)
    return path
