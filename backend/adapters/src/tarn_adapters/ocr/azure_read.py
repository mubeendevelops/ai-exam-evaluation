"""Azure AI Document Intelligence, ``prebuilt-read`` model (second cloud handwriting engine;
off by default, design decision 4). One call per page; the words come back with polygons in
the page's unit (pixels for an image) and confidences 0..1, and the core aligns them onto our
lines.

The client is injected, so tests run on recorded responses without the SDK or a key."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tarn_adapters.ocr.images import decode, polygon_box
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineFailedError

NAME = "azure"
MODEL = "prebuilt-read"

type AnalyzeRead = Callable[[bytes], Mapping[str, Any]]
"""Returns the ``analyzeResult`` object of the REST response (``pages`` → ``words``)."""


def parse_result(
    result: Mapping[str, Any], ref: EngineRef, width: int, height: int
) -> list[LineReading]:
    pages = result.get("pages", [])
    if not pages:
        return []
    page = pages[0]
    scale_x = width / float(page.get("width") or width)
    scale_y = height / float(page.get("height") or height)
    readings = []
    for word in page.get("words", []):
        text = str(word.get("content", "")).strip()
        polygon = word.get("polygon") or []
        if not text or len(polygon) < 4:
            continue
        points = [
            (polygon[i] * scale_x, polygon[i + 1] * scale_y) for i in range(0, len(polygon) - 1, 2)
        ]
        box = polygon_box(points, width, height)
        if box is None:
            continue
        confidence = min(1.0, max(0.0, float(word.get("confidence", 0.0))))
        readings.append(LineReading(engine=ref, text=text, box=box, confidence=confidence))
    return readings


def sdk_analyze(endpoint: str, key: str, timeout_seconds: float) -> AnalyzeRead:
    try:
        from azure.ai.documentintelligence import (
            DocumentIntelligenceClient,
        )
        from azure.core.credentials import AzureKeyCredential
    except ImportError as error:
        raise EngineFailedError(
            "azure-ai-documentintelligence is not installed (uv sync --group cloud-ocr)"
        ) from error
    client = DocumentIntelligenceClient(endpoint, AzureKeyCredential(key))

    def analyze(image: bytes) -> Mapping[str, Any]:
        poller = client.begin_analyze_document(
            MODEL, body=image, content_type="application/octet-stream"
        )
        return poller.result(timeout=timeout_seconds).as_dict()  # type: ignore[no-any-return]

    return analyze


class AzureReadEngine:
    def __init__(self, analyze: AnalyzeRead, *, version: str = MODEL) -> None:
        self.ref = EngineRef(name=NAME, version=version)
        self._analyze = analyze

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        height, width = decode(image).shape[:2]
        try:
            result = self._analyze(image)
        except Exception as error:
            raise EngineFailedError(f"Azure Read call failed ({type(error).__name__})") from None
        return parse_result(result, self.ref, width, height)
