"""Google Cloud Document AI, an OCR processor (Enterprise Document OCR: it reads handwriting;
third cloud engine, off by default, design decision 4). One synchronous call per page; the
tokens (words) come back with their boxes and confidences 0-1, and the core aligns them onto our
lines.

The response is the ``Document`` message as a dict, in either spelling Google's libraries give
(``Document.to_dict``: snake_case, ``Document.to_json``: camelCase). The client is injected, so
tests run on recorded-format responses without the SDK or credentials."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tarn_adapters.ocr.images import decode, polygon_box
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.errors import EngineFailedError

NAME = "docai"

type ProcessDocument = Callable[[bytes], Mapping[str, Any]]
"""Returns the ``document`` of the ``ProcessResponse`` (``text`` and ``pages[].tokens[]``)."""


def _get(obj: Mapping[str, Any], snake: str, camel: str) -> Any:
    return obj.get(snake, obj.get(camel))


def _anchor_text(text: str, layout: Mapping[str, Any]) -> str:
    anchor = _get(layout, "text_anchor", "textAnchor") or {}
    parts = []
    for segment in _get(anchor, "text_segments", "textSegments") or []:
        start = int(_get(segment, "start_index", "startIndex") or 0)  # absent when 0
        end = int(_get(segment, "end_index", "endIndex") or 0)
        parts.append(text[start:end])
    return "".join(parts).strip()


def _points(
    poly: Mapping[str, Any], width: int, height: int, page_w: float, page_h: float
) -> list[tuple[float, float]]:
    normalized = _get(poly, "normalized_vertices", "normalizedVertices") or []
    if normalized:
        return [(float(v.get("x", 0)) * width, float(v.get("y", 0)) * height) for v in normalized]
    vertices = poly.get("vertices") or []
    return [
        (float(v.get("x", 0)) * width / page_w, float(v.get("y", 0)) * height / page_h)
        for v in vertices
    ]


def parse_document(
    document: Mapping[str, Any], ref: EngineRef, width: int, height: int
) -> list[LineReading]:
    """The tokens of the first page as readings in page pixels."""
    pages = document.get("pages") or []
    if not pages:
        return []
    page = pages[0]
    dimension = page.get("dimension") or {}
    page_w = float(dimension.get("width") or width)
    page_h = float(dimension.get("height") or height)
    text = str(document.get("text", ""))
    readings = []
    for token in page.get("tokens") or []:
        layout = token.get("layout") or {}
        word = _anchor_text(text, layout)
        if not word:
            continue
        box = polygon_box(
            _points(
                _get(layout, "bounding_poly", "boundingPoly") or {}, width, height, page_w, page_h
            ),
            width,
            height,
        )
        if box is None:
            continue
        confidence = min(1.0, max(0.0, float(layout.get("confidence", 0.0))))
        readings.append(LineReading(engine=ref, text=word, box=box, confidence=confidence))
    return readings


def _mime_type(image: bytes) -> str:
    return "image/png" if image[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def sdk_process(processor: str, timeout_seconds: float) -> ProcessDocument:
    """A ``ProcessDocument`` over the Document AI client, in the processor's own location
    (``projects/<p>/locations/<l>/processors/<id>``), with Application Default Credentials."""
    parts = processor.split("/")
    if len(parts) < 6 or parts[0] != "projects" or parts[2] != "locations":
        raise EngineFailedError(
            "TARN_DOCAI_PROCESSOR is not projects/<p>/locations/<l>/processors/<id>"
        )
    try:
        import google.cloud.documentai_v1 as documentai
        from google.api_core.client_options import ClientOptions
    except ImportError as error:
        raise EngineFailedError(
            "google-cloud-documentai is not installed (uv sync --group cloud-ocr)"
        ) from error
    client = documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=f"{parts[3]}-documentai.googleapis.com")
    )

    def process(image: bytes) -> Mapping[str, Any]:
        request = documentai.ProcessRequest(
            name=processor,
            raw_document=documentai.RawDocument(content=image, mime_type=_mime_type(image)),
        )
        result = client.process_document(request=request, timeout=timeout_seconds)
        return documentai.Document.to_dict(result.document)  # type: ignore[no-any-return]

    return process


class DocumentAiEngine:
    def __init__(self, process: ProcessDocument, *, version: str = "ocr-processor") -> None:
        self.ref = EngineRef(name=NAME, version=version)
        self._process = process

    def read(self, image: bytes, lines: Sequence[Box]) -> Sequence[LineReading]:
        height, width = decode(image).shape[:2]
        try:
            document = self._process(image)
        except Exception as error:
            # The SDK's message can echo request details; keep only the class name.
            raise EngineFailedError(f"Document AI call failed ({type(error).__name__})") from None
        return parse_document(document, self.ref, width, height)
