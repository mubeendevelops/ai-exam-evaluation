"""Ports for drawing PDFs: the result sheet (P18) and the question paper (P34)."""

from typing import Protocol

from tarn_core.domain.paper_sheet import PaperDocument
from tarn_core.domain.sheet import SheetDocument


class SheetRenderer(Protocol):
    """Draws a ``SheetDocument`` as a PDF (an adapter: the core imports no PDF library)."""

    def render(self, document: SheetDocument) -> bytes: ...


class PaperRenderer(Protocol):
    """Draws a ``PaperDocument`` (a question paper) as a PDF."""

    def render(self, document: PaperDocument) -> bytes: ...
