"""Port for drawing the result sheet."""

from typing import Protocol

from tarn_core.domain.sheet import SheetDocument


class SheetRenderer(Protocol):
    """Draws a ``SheetDocument`` as a PDF (an adapter: the core imports no PDF library)."""

    def render(self, document: SheetDocument) -> bytes: ...
