"""``SheetRenderer`` over PyMuPDF: HTML laid out by ``pymupdf.Story`` into A4 pages, with a
footer on every page. Everything the student wrote or the teacher typed is HTML-escaped.
Drawings are cut from the cleaned page image (OpenCV) and shrunk to a thumbnail."""

import html
import io
from datetime import UTC
from decimal import Decimal

import cv2
import numpy as np
import pymupdf

from tarn_core.domain.common import Box
from tarn_core.domain.sheet import SheetAnswer, SheetDiagram, SheetDocument

THUMB_WIDTH = 520
PAGE = pymupdf.paper_rect("a4")
MARGINS = (42, 42, -42, -56)

CSS = """
body { font-family: sans-serif; font-size: 9pt; color: #1f2937; }
h1 { font-size: 17pt; margin: 0; color: #111827; }
h2 { font-size: 11pt; margin: 14pt 0 4pt 0; color: #4c1d95; }
h3 { font-size: 10pt; margin: 0; color: #111827; }
p { margin: 2pt 0; }
table { width: 100%; }
th { text-align: left; font-size: 8pt; color: #6b7280; padding: 2pt 4pt; }
td { padding: 2pt 4pt; }
.muted { color: #6b7280; }
.total { font-size: 14pt; font-weight: bold; color: #065f46; }
.note { background-color: #fef3c7; padding: 5pt; margin: 6pt 0; }
.answer { margin-top: 10pt; padding: 6pt; background-color: #f3f4f6; }
.text { background-color: #ffffff; padding: 4pt; margin: 3pt 0; }
.gone { color: #9ca3af; }
.tag { color: #5b21b6; }
"""


def mark(value: Decimal | None) -> str:
    if value is None:
        return "—"
    text = format(value.normalize(), "f")
    return text


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def thumbnail(image: bytes, box: Box) -> bytes | None:
    """The part of a page image inside ``box``, as a JPEG at most ``THUMB_WIDTH`` wide."""
    page = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if page is None:
        return None
    h, w = page.shape[:2]
    x0, y0, x1, y1 = max(box.x0, 0), max(box.y0, 0), min(box.x1, w), min(box.y1, h)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = page[y0:y1, x0:x1]
    if crop.shape[1] > THUMB_WIDTH:
        scale = THUMB_WIDTH / crop.shape[1]
        crop = cv2.resize(crop, (THUMB_WIDTH, max(1, round(crop.shape[0] * scale))))
    ok, encoded = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return bytes(encoded) if ok else None


class PyMuPdfSheetRenderer:
    def render(self, document: SheetDocument) -> bytes:
        archive = pymupdf.Archive()
        body = self._html(document, archive)
        buffer = io.BytesIO()
        writer = pymupdf.DocumentWriter(buffer)
        story = pymupdf.Story(body, user_css=CSS, archive=archive)
        where = PAGE + MARGINS
        more = 1
        while more:
            device = writer.begin_page(PAGE)
            more, _ = story.place(where)
            story.draw(device)
            writer.end_page()
        writer.close()
        return self._footers(buffer.getvalue(), document)

    # --- layout --------------------------------------------------------------------------

    def _html(self, d: SheetDocument, archive: pymupdf.Archive) -> str:
        parts = [
            f"<h1>{_e(d.college_name)}</h1>",
            (
                '<p class="note"><b>DRAFT.</b> AI-suggested marks. No teacher has approved '
                "them: nothing here is final.</p>"
                if d.draft
                else f'<p class="muted">Result sheet · version {d.version}</p>'
            ),
            "<table>",
            self._row("Exam", d.exam),
            self._row("Course", d.course),
            self._row("Student", d.student_name),
            self._row("USN", d.usn),
            (
                self._row("Status", "Draft, awaiting a teacher's approval")
                if d.draft
                else self._row("Evaluated by", d.issued_by)
            ),
            self._row("Generated", d.generated_at.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")),
            "</table>",
            f"<p>{'AI-suggested total' if d.draft else 'Total'}: "
            f'<span class="total">{mark(d.total)} / {mark(d.max_marks)}</span></p>',
        ]
        if d.amendment_note:
            parts.append(
                f'<p class="note"><b>Amendment note (version {d.version}):</b> '
                f"{_e(d.amendment_note)}</p>"
            )
        parts.append("<h2>Marks by question</h2>")
        parts.append(
            "<table><tr><th>Section</th><th>Question</th><th>Mark</th><th>Counts</th></tr>"
        )
        for ln in d.lines:
            gone = ln.mark is not None and not ln.counted
            cls = ' class="gone"' if gone else ""
            parts.append(
                f"<tr{cls}><td>{_e(ln.section_label)}</td><td>Q{_e(ln.slot_label)}</td>"
                f"<td>{mark(ln.mark)}</td><td>{_e(ln.reason)}</td></tr>"
            )
        parts.append("</table>")
        if d.not_counted:
            names = ", ".join(f"Q{_e(ln.slot_label)}" for ln in d.not_counted)
            parts.append(
                f"<p><b>Answers not counted:</b> {names}. They were marked but do not enter "
                "the total.</p>"
            )
        else:
            parts.append('<p class="muted">Answers not counted: none.</p>')
        parts.append("<h2>Answers</h2>")
        count = 0
        for a in d.answers:
            parts.append(self._answer(a, archive, count, draft=d.draft))
            count += len(a.diagrams)
        return "\n".join(parts)

    @staticmethod
    def _row(label: str, value: str) -> str:
        return f'<tr><td class="muted" width="22%">{_e(label)}</td><td>{_e(value)}</td></tr>'

    def _answer(
        self, a: SheetAnswer, archive: pymupdf.Archive, first_image: int, *, draft: bool = False
    ) -> str:
        counts = "" if a.counted else f" · <b>{_e(a.outcome or 'not counted')}</b>"
        shown = a.ai_mark if draft else a.teacher_mark
        out = [
            '<div class="answer">',
            f"<h3>Question {_e(a.label)} · {mark(shown)} / {mark(a.max_marks)}{counts}</h3>",
        ]
        if a.question:
            out.append(f'<p class="muted">{_e(a.question)}</p>')
        out.append(
            f'<div class="text">{_e(a.excerpt)}</div>'
            if a.excerpt
            else '<p class="muted">No text was read for this answer.</p>'
        )
        for k, diagram in enumerate(a.diagrams):
            out.append(self._diagram(diagram, archive, f"d{first_image + k}.jpg"))
        if draft:
            out.append(
                "<p>Marks: no AI mark (the key is guidance only: for the teacher to mark)</p>"
                if a.ai_mark is None
                else f"<p>Marks: AI suggestion {mark(a.ai_mark)} · not yet approved</p>"
            )
        else:
            ai = (
                "no AI mark (marked by the teacher)"
                if a.ai_mark is None
                else f"AI {mark(a.ai_mark)}"
            )
            out.append(
                f"<p>Marks: {ai} · teacher {mark(a.teacher_mark)}"
                f"{' (overridden)' if a.overridden else ''}</p>"
            )
        if a.criteria:
            out.append("<table><tr><th>Criterion (AI suggestion)</th><th>Weight</th>")
            out.append("<th>Credit</th><th>Marks</th></tr>")
            for c in a.criteria:
                reason = f' <span class="muted">{_e(c.reason)}</span>' if c.reason else ""
                out.append(
                    f"<tr><td>{_e(c.label)}{reason}</td><td>{mark(c.weight)}</td>"
                    f"<td>{mark(c.credit)}</td><td>{mark(c.marks)}</td></tr>"
                )
            out.append("</table>")
        if a.tags:
            out.append(f'<p class="tag">Tags: {_e(", ".join(a.tags))}</p>')
        if a.remarks:
            out.append(f"<p><b>Remarks:</b> {_e(a.remarks)}</p>")
        out.append("</div>")
        return "\n".join(out)

    @staticmethod
    def _diagram(diagram: SheetDiagram, archive: pymupdf.Archive, name: str) -> str:
        cut = None if diagram.image is None else thumbnail(diagram.image, diagram.box)
        if cut is None:
            return f'<p class="muted">{_e(diagram.caption)} (image not available)</p>'
        archive.add(cut, name)
        return f'<p class="muted">{_e(diagram.caption)}</p><p><img src="{name}" width="260"/></p>'

    @staticmethod
    def _footers(pdf: bytes, document: SheetDocument) -> bytes:
        doc = pymupdf.open("pdf", pdf)
        total = len(doc)
        kind = "DRAFT (AI suggestions)" if document.draft else f"result sheet v{document.version}"
        label = f"{document.student_name} · {document.usn} · {document.exam} · {kind}"
        for number in range(total):
            page = doc.load_page(number)
            page.insert_text(
                (MARGINS[0], PAGE.height - 30),
                f"{label}  —  page {number + 1} of {total}",
                fontsize=7,
                color=(0.42, 0.45, 0.5),
            )
        out = doc.tobytes(deflate=True)
        doc.close()
        return bytes(out)
