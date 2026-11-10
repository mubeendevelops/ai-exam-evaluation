"""``PaperRenderer`` over PyMuPDF (P34, D224): a question paper as plain A4. HTML laid out by
``pymupdf.Story`` (long text wraps across pages), a footer on every page. Every text is
HTML-escaped. The document has no keys, rubrics or reference answers to draw."""

import html

import pymupdf

from tarn_adapters.sheets.pdf import MARGINS, PAGE, mark, write_story
from tarn_core.domain.paper_sheet import (
    PaperChoice,
    PaperDocument,
    PaperItem,
    PaperQuestion,
    PaperSection,
)

CSS = """
body { font-family: sans-serif; font-size: 10pt; color: #111827; }
h1 { font-size: 15pt; margin: 0; text-align: center; }
h2 { font-size: 13pt; margin: 4pt 0 0 0; text-align: center; }
h3 { font-size: 11pt; margin: 14pt 0 2pt 0; }
p { margin: 2pt 0; }
.q { margin: 6pt 0 2pt 0; }
.part { margin: 2pt 0 2pt 18pt; }
.muted { color: #4b5563; }
.meta { font-size: 9pt; text-align: center; color: #374151; }
.rule { font-style: italic; color: #374151; }
.draft { background-color: #fef3c7; padding: 4pt; margin: 6pt 0; }
.or { text-align: center; font-weight: bold; margin: 3pt 0; }
.missing { color: #9ca3af; font-style: italic; }
"""

NOT_LINKED = "Question not linked yet."
FOOTER_CSS = "* { font-family: sans-serif; font-size: 7pt; color: #6b7280; }"


def escape(text: str) -> str:
    return html.escape(text, quote=True)


def _duration(minutes: int | None) -> str:
    if minutes is None:
        return ""
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{rest} minutes"
    text = f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{text} {rest} minutes" if rest else text


def _text(text: str | None) -> str:
    if text is None:
        return f'<span class="missing">{NOT_LINKED}</span>'
    return "<br/>".join(escape(line) for line in text.splitlines()) or "&nbsp;"


class PyMuPdfPaperRenderer:
    def render(self, document: PaperDocument) -> bytes:
        body = self._html(document)
        pdf = write_story(lambda: pymupdf.Story(body, user_css=CSS))
        return self._footers(pdf, document)

    def _html(self, d: PaperDocument) -> str:
        codes = " · ".join(
            part
            for part in (
                f"Exam code: {escape(d.exam_code)}" if d.exam_code else "",
                f"Course code: {escape(d.course_code)}" if d.course_code else "",
                f"Subject: {escape(d.subject_name)}" if d.subject_name else "",
            )
            if part
        )
        times = " · ".join(
            part
            for part in (
                f"Time: {_duration(d.duration_minutes)}" if d.duration_minutes else "",
                f"Max marks: {mark(d.total_marks)}",
            )
            if part
        )
        parts = [f"<h1>{escape(d.college_name)}</h1>", f"<h2>{escape(d.title)}</h2>"]
        if codes:
            parts.append(f'<p class="meta">{codes}</p>')
        parts.append(f'<p class="meta"><b>{times}</b></p>')
        if d.unlinked:
            parts.append(
                f'<p class="draft"><b>DRAFT.</b> {d.unlinked} question'
                f"{'s are' if d.unlinked != 1 else ' is'} not linked yet.</p>"
            )
        parts.extend(self._section(s) for s in d.sections)
        return "\n".join(parts)

    def _section(self, s: PaperSection) -> str:
        heading = f"Section {escape(s.label)}"
        if s.title and s.title.strip().casefold() != heading.casefold():
            heading += f": {escape(s.title)}"
        rule = escape(s.rule) + (f" {escape(s.negative_note)}" if s.negative_note else "")
        out = [f"<h3>{heading}</h3>", f'<p class="rule">{rule}</p>']
        out.extend(self._item(i) for i in s.items)
        return "\n".join(out)

    def _item(self, item: PaperItem) -> str:
        # Paragraphs, not a table: Story cannot split a table cell taller than a page.
        if isinstance(item, PaperChoice):
            return '<p class="or">OR</p>'.join(self._question(q) for q in item.alternatives)
        return self._question(item)

    @staticmethod
    def _question(q: PaperQuestion) -> str:
        number = f"<b>{escape(q.number)}.</b>"
        if not q.parts:
            return f'<p class="q">{number} {_text(q.text)} <b>[{mark(q.marks)}]</b></p>'
        rows = [f'<p class="q">{number} <b>[{mark(q.marks)}]</b></p>']
        rows.extend(
            f'<p class="part">({escape(p.label)}) {_text(p.text)} <b>[{mark(p.marks)}]</b></p>'
            for p in q.parts
        )
        return "".join(rows)

    @staticmethod
    def _footers(pdf: bytes, document: PaperDocument) -> bytes:
        doc = pymupdf.open("pdf", pdf)
        total = len(doc)
        label = document.title + (f" · {document.exam_code}" if document.exam_code else "")
        for number in range(total):
            page = doc.load_page(number)
            # An HTML box: the title may be in any script, and Story falls back to fonts
            # that have its glyphs (``insert_text`` knows only Latin base fonts).
            page.insert_htmlbox(
                pymupdf.Rect(MARGINS[0], PAGE.height - 40, PAGE.width - MARGINS[0], PAGE.height),
                f"{escape(label)} — page {number + 1} of {total}",
                css=FOOTER_CSS,
            )
        out = doc.tobytes(deflate=True)
        doc.close()
        return bytes(out)
