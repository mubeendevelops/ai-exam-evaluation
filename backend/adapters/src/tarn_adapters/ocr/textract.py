"""Amazon Textract ``DetectDocumentText`` (cloud handwriting engine; off by default, design
decision 4). One synchronous call per page; the WORD blocks come back with their boxes
(fractions of the page) and confidences (0-100), and the core aligns them onto our lines.

The client is injected, so tests run on recorded responses without boto3 or credentials."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tarn_adapters.ocr.images import decode, polygon_box
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineFailedError

NAME = "textract"

type DetectText = Callable[[bytes], Mapping[str, Any]]


def parse_blocks(
    response: Mapping[str, Any], ref: EngineRef, width: int, height: int
) -> list[LineReading]:
    """WORD blocks of a DetectDocumentText response as readings in page pixels."""
    readings = []
    for block in response.get("Blocks", []):
        if block.get("BlockType") != "WORD" or not str(block.get("Text", "")).strip():
            continue
        geometry = block.get("Geometry", {})
        polygon = geometry.get("Polygon")
        if polygon:
            points = [(p["X"] * width, p["Y"] * height) for p in polygon]
        else:
            bb = geometry["BoundingBox"]
            left, top = bb["Left"] * width, bb["Top"] * height
            points = [(left, top), (left + bb["Width"] * width, top + bb["Height"] * height)]
        box = polygon_box(points, width, height)
        if box is None:
            continue
        confidence = min(100.0, max(0.0, float(block.get("Confidence", 0.0)))) / 100
        readings.append(
            LineReading(engine=ref, text=str(block["Text"]), box=box, confidence=confidence)
        )
    return readings


def boto3_detect(region: str, timeout_seconds: float) -> DetectText:
    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise EngineFailedError("boto3 is not installed (uv sync --group cloud-ocr)") from error
    client = boto3.client(
        "textract",
        region_name=region,
        config=Config(
            connect_timeout=10, read_timeout=timeout_seconds, retries={"max_attempts": 2}
        ),
    )

    def detect(image: bytes) -> Mapping[str, Any]:
        return client.detect_document_text(Document={"Bytes": image})  # type: ignore[no-any-return]

    return detect


class TextractEngine:
    def __init__(self, detect: DetectText, *, version: str = "DetectDocumentText") -> None:
        self.ref = EngineRef(name=NAME, version=version)
        self._detect = detect

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        height, width = decode(image).shape[:2]
        try:
            response = self._detect(image)
        except Exception as error:
            # The SDK's message can echo request details; keep only the class name.
            raise EngineFailedError(f"Textract call failed ({type(error).__name__})") from None
        return parse_blocks(response, self.ref, width, height)
