"""The question paper PDF (P34, D224) on the seeded QP-IPR paper: its text, the OR pairs and
sub-parts, no key text, page breaks for long text, and text in other scripts."""

import re
from dataclasses import replace
from decimal import Decimal
from typing import Any

import pymupdf
import pytest

from tarn_adapters.sheets import PyMuPdfPaperRenderer
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.content import Question, ReferenceAnswer, RubricCriterion
from tarn_core.domain.paper_sheet import (
    PaperChoice,
    PaperDocument,
    PaperQuestion,
    PaperSection,
)
from tarn_core.services.access import Reach
from tarn_core.services.paper_sheet import PaperSheets
from tarn_core.testing import InMemory
from tarn_core.testing.seed_world import seed_in_memory, seeded_blueprints

IPR = "QP-IPR Intellectual Property Rights, July 2021"
LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}


def flat(text: str) -> str:
    """Text as compared: ligatures spelled out, every run of white space one space."""
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    return re.sub(r"\s+", " ", text).strip()


def read(pdf: bytes) -> tuple[int, str]:
    opened: Any = pymupdf.open  # PyMuPDF ships no types for the document API
    doc = opened("pdf", pdf)
    try:
        return len(doc), flat(" ".join(page.get_text() for page in doc))
    finally:
        doc.close()


@pytest.fixture(scope="module")
def seeded() -> tuple[InMemory, ExamBlueprint, PaperDocument, bytes]:
    mem = InMemory()
    seed_in_memory(mem)
    paper = next(b for b in seeded_blueprints(mem) if b.title == IPR)
    sheets = PaperSheets(
        content=mem.content, colleges=mem.colleges, renderer=PyMuPdfPaperRenderer()
    )
    document, pdf = sheets.pdf(
        paper.meta.college_id, paper.id, paper.meta.version, reach=Reach(None)
    )
    return mem, paper, document, pdf


def test_the_seeded_paper_reads_back(
    seeded: tuple[InMemory, ExamBlueprint, PaperDocument, bytes],
) -> None:
    mem, paper, document, pdf = seeded
    cid = paper.meta.college_id
    pages, text = read(pdf)
    assert pdf.startswith(b"%PDF") and pages >= 1
    for expected in (
        document.college_name,
        IPR,
        "Exam code: QP-IPR-2021-07",
        f"Max marks: {document.total_marks.normalize():f}",
        f"page 1 of {pages}",
    ):
        assert flat(expected) in text, expected
    for section in document.sections:
        assert flat(section.rule) in text
    for slot in paper.slots():
        for _, question_id, _ in leaves(slot):
            assert question_id is not None
            question = mem.content.get(cid, Question, question_id)
            assert flat(question.text) in text, question.code
    choices = sum(
        len(i.alternatives) - 1
        for s in document.sections
        for i in s.items
        if isinstance(i, PaperChoice)
    )
    assert choices >= 1 and text.count(" OR ") >= choices
    split = [
        i for s in document.sections for i in s.items if isinstance(i, PaperQuestion) and i.parts
    ]
    for item in split:
        for part in item.parts:
            assert f"({part.label})" in text


def test_no_key_text_is_printed(
    seeded: tuple[InMemory, ExamBlueprint, PaperDocument, bytes],
) -> None:
    mem, paper, _, pdf = seeded
    cid = paper.meta.college_id
    _, text = read(pdf)
    secrets = []
    for slot in paper.slots():
        for _, question_id, _ in leaves(slot):
            assert question_id is not None
            secrets += [a.text for a in mem.content.for_question(cid, ReferenceAnswer, question_id)]
            secrets += [
                c.label for c in mem.content.for_question(cid, RubricCriterion, question_id)
            ]
    secrets = [flat(s) for s in secrets if len(s) > 12]
    assert secrets
    assert [s for s in secrets if s in text] == []


def _document(text: str, title: str = "Long paper") -> PaperDocument:
    question = PaperQuestion(number="1", text=text, marks=Decimal(10))
    return PaperDocument(
        college_name="Synthetic College",
        title=title,
        exam_code="SYN-1",
        course_code="SYN101",
        subject_name="Synthetic Physics",
        duration_minutes=90,
        total_marks=Decimal(10),
        version=1,
        sections=(
            PaperSection(
                label="A",
                title="Long answers",
                rule="Answer all questions.",
                negative_note="",
                items=(PaperChoice(alternatives=(question, replace(question, number="2"))),),
            ),
        ),
    )


def test_long_text_wraps_across_pages() -> None:
    long = " ".join(f"word{n}" for n in range(4000))
    pages, text = read(PyMuPdfPaperRenderer().render(_document(long)))
    assert pages >= 3
    assert "word0 " in text and "word3999" in text  # nothing lost at a page break
    assert f"page {pages} of {pages}" in text


def test_text_in_other_scripts_and_markup_is_kept() -> None:
    question = "Derive v = 1/√(με) for εr ≥ 1; café, naïve, <b>not bold</b> & 中文"
    pages, text = read(
        PyMuPdfPaperRenderer().render(_document(question, title="Ondas — ε μ paper"))
    )
    assert pages == 1
    assert flat(question) in text
    assert "Ondas — ε μ paper · SYN-1 — page 1 of 1" in text
    assert "Time: 1 hour 30 minutes" in text
