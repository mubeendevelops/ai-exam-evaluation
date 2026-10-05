"""Importer for the UNT short-answer grading data v2.0 (Mohler, Bunescu and Mihalcea, ACL 2011):
introductory computer-science questions, an instructor answer each, and student answers graded
0-5 by two people (the average, in half marks). Typed text, not handwriting.

Layout (the dataset's README): ``data/docs/files`` lists the questions (``#`` = left out by
the authors: selection and ordering questions); ``data/raw/<q>`` one student answer per line,
``data/scores/<q>/ave`` one average grade per line in the same order; ``data/raw/questions``
and ``data/sent/answers`` (instructor answers split into sentences by ``<STOP>``).

The set becomes ``questions.json`` (one semantic criterion per instructor sentence, weights
splitting the 5 marks evenly: the shape of Tarn's semantic criteria) and ``answers.jsonl``
with the averages as teacher marks."""

import json
import re
from pathlib import Path

from tarn_adapters.scoring.sets import write_answers

_TAGS = re.compile(r"<[^>]+>")
_BRACKETS = {"-LRB-": "(", "-RRB-": ")", "-LSB-": "[", "-RSB-": "]", "-LCB-": "{", "-RCB-": "}"}


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="latin-1").splitlines() if line.strip()]


def _by_code(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in _lines(path):
        code, _, text = line.partition(" ")
        out[code] = text.strip()
    return out


def _clean(text: str) -> str:
    for token, char in _BRACKETS.items():
        text = text.replace(token, char)
    text = " ".join(_TAGS.sub(" ", text).split())
    return re.sub(r"\s+([)\]}])", r"\1", re.sub(r"([(\[{])\s+", r"\1", text))


def import_mohler(root: Path, out: Path) -> tuple[int, int]:
    """Write the calibration set; returns (questions, answers)."""
    data = root / "data" if (root / "data").is_dir() else root
    codes = [
        c for c in (x.strip() for x in _lines(data / "docs" / "files")) if not c.startswith("#")
    ]
    questions = _by_code(data / "raw" / "questions")
    keys = _by_code(data / "raw" / "answers")
    sentences = _by_code(data / "sent" / "answers")
    doc: dict[str, dict[str, object]] = {}
    rows: list[dict[str, object]] = []
    for code in codes:
        answers = _lines(data / "raw" / code)
        grades = _lines(data / "scores" / code / "ave")
        if len(answers) != len(grades):
            raise ValueError(f"{code}: {len(answers)} answers but {len(grades)} grades")
        statements = [_clean(s) for s in sentences.get(code, keys[code]).split("<STOP>")]
        name = f"M-{code}"
        doc[name] = {
            "text": _clean(questions[code]),
            "max_marks": "5",
            "mark_step": "0.5",
            "key": _clean(keys[code]),
            "statements": [s for s in statements if s],
        }
        for k, (answer, grade) in enumerate(zip(answers, grades, strict=True)):
            _, _, text = answer.partition(" ")
            rows.append(
                {"id": f"{name}:{k}", "question": name, "text": _clean(text), "teacher_mark": grade}
            )
    write_answers(out, rows)
    (out / "questions.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return len(doc), len(rows)
