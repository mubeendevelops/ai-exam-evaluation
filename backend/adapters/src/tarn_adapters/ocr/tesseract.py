"""Tesseract (printed text: covers, printed headings, keys). Reads each detected line crop with
page segmentation mode 7 (one line); the confidence is the length-weighted mean of the word
confidences (Tesseract reports 0-100 per word)."""

from collections.abc import Sequence
from typing import Any

from tarn_adapters.ocr.images import crop, decode
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineFailedError

NAME = "tesseract"


def _module() -> Any:
    try:
        import pytesseract
    except ImportError as error:  # pragma: no cover  (the ocr group is not installed)
        raise EngineFailedError("pytesseract is not installed") from error
    return pytesseract


class TesseractEngine:
    def __init__(self, *, language: str = "eng", pad: int = 4) -> None:
        tess = _module()
        try:
            version = str(tess.get_tesseract_version())
        except Exception as error:  # the binary is missing
            raise EngineFailedError("the tesseract binary is not available") from error
        self.ref = EngineRef(name=NAME, version=version)
        self._language = language
        self._pad = pad
        self._tess = tess

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        pixels = decode(image)
        readings = []
        for box in lines:
            piece = crop(pixels, box, self._pad)
            if piece is None:
                continue
            data = self._tess.image_to_data(
                piece,
                lang=self._language,
                config="--psm 7 --oem 1",
                output_type=self._tess.Output.DICT,
            )
            text, confidence = words_to_line(data["text"], data["conf"])
            readings.append(LineReading(engine=self.ref, text=text, box=box, confidence=confidence))
        return readings


def words_to_line(
    texts: Sequence[str], confidences: Sequence[float | int | str]
) -> tuple[str, float]:
    """Tesseract's word list as one line: words joined by spaces, confidence 0..1 weighted by
    word length (entries with confidence -1 are layout rows, not words)."""
    words: list[tuple[str, float]] = []
    for text, conf in zip(texts, confidences, strict=True):
        value = float(conf)
        if value < 0 or not str(text).strip():
            continue
        words.append((str(text).strip(), min(100.0, value) / 100))
    if not words:
        return "", 0.0
    total = sum(len(w) for w, _ in words)
    return " ".join(w for w, _ in words), sum(len(w) * c for w, c in words) / total
