"""The result sheet PDF (P18), read back with PyMuPDF: every field the sheet promises, escaping,
drawings cut from the page, pages and footers, and the design's 10-second budget. All data is
synthetic."""

import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import cv2
import numpy as np
import pymupdf
import pytest

from tarn_adapters.sheets.pdf import PyMuPdfSheetRenderer, mark, thumbnail
from tarn_core.domain.common import Box
from tarn_core.domain.review import ResultLine
from tarn_core.domain.sheet import SheetAnswer, SheetCriterion, SheetDiagram, SheetDocument


def page_jpeg(width: int = 1240, height: int = 1754) -> bytes:
    image = np.full((height, width, 3), 255, np.uint8)
    cv2.rectangle(image, (300, 300), (700, 500), (0, 0, 0), 4)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return bytes(encoded)


def answer(label: str, **kw: object) -> SheetAnswer:
    fields: dict[str, object] = {
        "label": label,
        "section_label": "Part A",
        "question": f"Explain synthetic concept {label}.",
        "max_marks": Decimal(4),
        "ai_mark": Decimal("3"),
        "teacher_mark": Decimal("3.5"),
        "counted": True,
        "outcome": "counted",
        "excerpt": f"Synthetic answer text for {label}. " * 12,
        "criteria": (
            SheetCriterion(
                label="Definition",
                weight=Decimal(2),
                credit=Decimal(1),
                marks=Decimal(2),
                reason="2 of 2 items",
            ),
            SheetCriterion(
                label="Example", weight=Decimal(2), credit=Decimal("0.5"), marks=Decimal(1)
            ),
        ),
        "tags": ("partial",),
        "remarks": "Good start.",
    }
    fields.update(kw)
    return SheetAnswer(**fields)  # type: ignore[arg-type]


def document(answers: tuple[SheetAnswer, ...], **kw: object) -> SheetDocument:
    lines = (
        *tuple(
            ResultLine(
                section_label="Part A",
                slot_label=a.label,
                mark=a.teacher_mark,
                counted=a.counted,
                reason=a.outcome,
            )
            for a in answers
        ),
        ResultLine(
            section_label="Part B", slot_label="9", mark=None, counted=False, reason="not attempted"
        ),
    )
    fields: dict[str, object] = {
        "college_name": "Test College A",
        "exam": "Synthetic paper, CI shape",
        "course": "SYN101 · Synthetic Civics",
        "student_name": "Student A1",
        "usn": "TST26A0001",
        "version": 1,
        "total": Decimal("7"),
        "max_marks": Decimal(50),
        "issued_by": "Teacher A",
        "generated_at": datetime(2026, 10, 6, 9, 30, tzinfo=UTC),
        "lines": lines,
        "answers": answers,
    }
    fields.update(kw)
    return SheetDocument(**fields)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Pdf:
    pages: list[str]
    """The text of each page (ligatures folded: "ﬂ" reads as "fl")."""
    images: list[int]
    """How many pictures each page holds."""

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def flat(self) -> str:
        return " ".join(self.text.split())


def read(pdf: bytes) -> Pdf:
    opened: Any = pymupdf.open  # PyMuPDF ships no types for the document API
    doc = opened("pdf", pdf)
    count = len(doc)
    return Pdf(
        pages=[unicodedata.normalize("NFKC", doc[n].get_text()) for n in range(count)],
        images=[len(doc[n].get_images()) for n in range(count)],
    )


def test_every_field_of_the_sheet_is_in_the_pdf() -> None:
    answers = (
        answer(
            "1",
            diagrams=(
                SheetDiagram(
                    image=page_jpeg(),
                    box=Box(x0=250, y0=250, x1=800, y1=600),
                    caption="Drawing 1 (flowchart)",
                ),
            ),
        ),
        answer("2", counted=False, outcome="not counted: best N", ai_mark=None),
    )
    pdf = PyMuPdfSheetRenderer().render(
        document(answers, version=2, amendment_note="2: re-marked after review")
    )
    assert pdf.startswith(b"%PDF")
    pdf_read = read(pdf)
    for wanted in (
        "Test College A",
        "Synthetic paper, CI shape",
        "SYN101 · Synthetic Civics",
        "Student A1",
        "TST26A0001",
        "Teacher A",
        "06 Oct 2026",
        "Result sheet · version 2",
        "Total:",
        "7 / 50",
        "Amendment note (version 2): 2: re-marked after review",
        "Marks by question",
        "Answers not counted: Q2",
        "not counted: best N",
        "not attempted",
        "Question 1 · 3.5 / 4",
        "Explain synthetic concept 1.",
        "Synthetic answer text for 1.",
        "Drawing 1 (flowchart)",
        "AI 3 · teacher 3.5 (overridden)",
        "no AI mark (marked by the teacher)",
        "Definition",
        "2 of 2 items",
        "Example",
        "Tags: partial",
        "Remarks: Good start.",
    ):
        assert wanted in pdf_read.flat, wanted
    assert sum(pdf_read.images) == 1  # the drawing
    assert "result sheet v2" in pdf_read.text and "page 1 of" in pdf_read.text


def test_a_first_version_has_no_amendment_note_and_says_nothing_was_dropped() -> None:
    sheet = read(PyMuPdfSheetRenderer().render(document((answer("1"),))))
    assert "Amendment note" not in sheet.text
    assert "Answers not counted: none." in sheet.flat


def test_student_and_teacher_text_is_escaped() -> None:
    hostile = '<b>bold</b> & <img src="x"> <script>alert(1)</script>'
    sheet = read(
        PyMuPdfSheetRenderer().render(
            document(
                (answer("1", excerpt=hostile, remarks=hostile, tags=("<i>t</i>",)),),
                student_name="A <u>B</u> & C",
            )
        )
    )
    assert hostile in sheet.flat and "A <u>B</u> & C" in sheet.flat and "<i>t</i>" in sheet.flat


def test_marks_print_without_exponents_or_trailing_zeros() -> None:
    assert [mark(Decimal(v)) for v in ("10", "2.50", "0.5", "100", "0")] == [
        "10",
        "2.5",
        "0.5",
        "100",
        "0",
    ]
    assert mark(None) == "—"


def test_a_long_sheet_runs_over_pages_with_a_footer_on_each() -> None:
    answers = tuple(answer(str(n)) for n in range(1, 18))
    started = time.perf_counter()
    sheet = read(PyMuPdfSheetRenderer().render(document(answers)))
    assert time.perf_counter() - started < 10  # design.md: result sheet PDF under 10 seconds
    assert len(sheet.pages) > 2
    for number, page in enumerate(sheet.pages, start=1):
        assert f"page {number} of {len(sheet.pages)}" in page


def test_a_missing_or_unreadable_page_image_leaves_a_caption() -> None:
    box = Box(x0=0, y0=0, x1=100, y1=100)
    answers = (
        answer(
            "1",
            diagrams=(
                SheetDiagram(image=None, box=box, caption="Drawing 1 (flowchart)"),
                SheetDiagram(image=b"not an image", box=box, caption="Drawing 2 (block)"),
            ),
        ),
    )
    sheet = read(PyMuPdfSheetRenderer().render(document(answers)))
    assert sheet.text.count("image not available") == 2 and sheet.images == [0]


def test_thumbnails_are_clamped_to_the_page_and_shrunk() -> None:
    full = page_jpeg()
    cut = thumbnail(full, Box(x0=100, y0=100, x1=5000, y1=5000))  # runs off the page
    assert cut is not None
    decoded = cv2.imdecode(np.frombuffer(cut, np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None and 0 < decoded.shape[1] <= 520
    assert thumbnail(full, Box(x0=2000, y0=2000, x1=3000, y1=3000)) is None  # outside the page
    assert thumbnail(b"junk", Box(x0=0, y0=0, x1=10, y1=10)) is None


@pytest.mark.parametrize("version", [1, 2, 3])
def test_each_version_renders_its_own_number(version: int) -> None:
    sheet = read(PyMuPdfSheetRenderer().render(document((answer("1"),), version=version)))
    assert f"Result sheet · version {version}" in sheet.flat
