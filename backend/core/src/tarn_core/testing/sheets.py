"""A renderer that records what it was asked to draw (tests only). The adapter's real PDF is
checked in the adapter tests."""

from tarn_core.domain.sheet import SheetDocument


class FakeSheetRenderer:
    def __init__(self) -> None:
        self.documents: list[SheetDocument] = []

    def render(self, document: SheetDocument) -> bytes:
        self.documents.append(document)
        return f"%PDF-fake v{document.version} {document.usn}".encode()
