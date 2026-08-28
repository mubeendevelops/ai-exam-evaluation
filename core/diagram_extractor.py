"""
core/diagram_extractor.py — turns a handwritten/scanned diagram image into
the structured graph shape used throughout Task 4 (plan.md §3):

    {"schema_version": 1,
     "nodes": [{"node_id": str, "label": str, "bbox": [x, y, w, h] | None,
                "confidence": float | None,
                "ocr_engine": str | None}, ...],
     "edges": [{"edge_id": str, "from_node": str, "to_node": str,
                "label": str | None, "confidence": float | None}, ...]}

"ocr_engine" names which OCR engine plugin's read won for that node (e.g.
"paddleocr" or "tesseract") — new since the fallback-OCR framework
(core/ocr_fallback.py); absent/None on stub_extract()'s fake nodes.

STATUS: real HANDWRITTEN LABEL extraction is implemented via a
multi-engine "fallback OCR" framework (core/ocr_fallback.py): PaddleOCR's
DB-based text detector (PP-OCRv5) locates candidate label regions, then
EVERY registered OCR engine plugin (core/ocr_engines/ — currently PaddleOCR
and Tesseract) reads each cropped region, and the highest-confidence read
wins per region. See core/ocr_fallback.py's module docstring for why
("boosting"-style ensemble: no single engine is best on every handwriting
style) and scripts/benchmark_ocr_engines.py for the accuracy data used to
validate/tune that heuristic.

MIGRATION HISTORY (2026-08-28, later same day): PaddleOCR's own
detection/recognition calls were moved out of this module and into
core/ocr_engines/paddleocr_engine.py so they could be plugged into
core/ocr_fallback.py's engine roster alongside Tesseract
(core/ocr_engines/tesseract_engine.py) — this module now only orchestrates
region-cropping and delegates recognition to that framework. No behavior
changed for PaddleOCR itself (same models, same thresholds); the change is
that a second engine's read is now considered and can win per region.

MIGRATION HISTORY (2026-08-28): this module previously used EasyOCR's CRAFT
detector for detection and Microsoft's TrOCR for recognition. That
implementation was fully removed once the PaddleOCR migration was verified —
it is not present anywhere in this repo (check git history before this date
if it's ever needed for reference). Reasons for the migration:
  - One dependency family (paddleocr + paddlepaddle) instead of two
    (easyocr's own torch pin + a separately-pinned transformers/torch/
    torchvision/sentencepiece/protobuf stack for TrOCR — see requirements.txt
    history for the fragility that pinning caused).
  - Both detection and recognition come from actively-maintained PP-OCR
    checkpoints trained on data that explicitly includes handwriting (see
    PADDLEOCR_REC_MODEL below), rather than a small TrOCR checkpoint whose
    real-world accuracy on this project's own test image was measured at
    2/6 usable reads (CLAUDE_CONTEXT.md §11).
  - Verified end-to-end, before removing the old code, against the real
    hand-drawn photo media/diagrams/hand_drawn_buses_image.jpeg and the
    clean digital media/diagrams/buses.webp (same diagram, both ship in this
    repo): fuzzy-matched label accuracy against the known 6-label ground
    truth went from 4/12 total (old pipeline, both images) to 11/12 (new
    pipeline) — see conversation/PR history for the full before/after
    output; the comparison script that produced these numbers was itself
    removed after verification since it depended on the deleted legacy code.
  - Confirmed further against real production data already stored in this
    project's DB (question 88880001-..., answer 88880003-...): the old
    pipeline's stored score was 1.85/10 with 2/6 labels glossary-matched;
    re-scoring the same stored scan with the new pipeline gave 2.14/10 with
    5/7 labels matched (evaluation_results is append-only, so both scores
    are still there to compare directly).

Detection region-finding itself went through the same two-iteration history
the old EasyOCR-based code documented (plain OpenCV failed on real
ruled-paper photos — ruled lines and arrows got picked up as "text"; a
trained text-detection model was needed). PaddleOCR's detector is DB-based
(Differentiable Binarization), the same family of approach EasyOCR's CRAFT
detector belongs to — a learned model, not a threshold/morphology heuristic
— so it inherits that robustness.

NOT implemented: edge/arrow detection (unchanged from before this
migration — out of scope here, see plan.md §7 "Phase 2"). Text-region
detection tells us WHERE labels are and WHAT they say; it says nothing
about which nodes are connected to which. extract_diagram_structure()
always returns edges=[] for real extraction and says so explicitly in the
returned dict's "extraction_warnings" — callers (core/diagram_evaluator.py)
must not treat an empty extracted edge list as "the student drew no
connections," only as "connections were not checked."

Real image bytes are only available for `blob_url` values from real
("minio") storage — a "dummy-storage/..." placeholder has no object behind
it and raises a clear error instead of silently returning nothing.
"""
from __future__ import annotations

import io

SCHEMA_VERSION = 1

REGION_CROP_PADDING_PX = 6

_fallback_ocr = None


def _get_fallback_ocr():
    """Lazy-loaded FallbackOCR instance (core/ocr_fallback.py) — holds the
    registered engine plugins (core/ocr_engines/) and does the actual
    detect/recognize work. Lazy for the same reason the old direct
    PaddleOCR calls were lazy: constructing engines is cheap, but importing
    paddleocr/pytesseract and loading their models is not, so it shouldn't
    happen at module import time (e.g. --stub-extraction runs never need
    to pay this cost)."""
    global _fallback_ocr
    if _fallback_ocr is None:
        from core.ocr_fallback import FallbackOCR
        _fallback_ocr = FallbackOCR()
    return _fallback_ocr


def stub_extract(blob_url: str) -> dict:
    """Deterministic fake extraction, for testing without touching the real
    pipeline (no image fetch, no PaddleOCR model load). Returns a fixed
    graph regardless of input — labels are prefixed "[STUB]" so it's
    obvious in output/logs that this isn't a real reading of the image."""
    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": [
            {"node_id": "n1", "label": "[STUB] CPU", "bbox": None, "confidence": 1.0},
            {"node_id": "n2", "label": "[STUB] Memory", "bbox": None, "confidence": 1.0},
        ],
        "edges": [
            {"edge_id": "e1", "from_node": "n1", "to_node": "n2", "label": None, "confidence": 1.0},
        ],
    }


def _load_image(blob_url: str):
    """Fetches the real image bytes for a stored blob_url and returns a
    PIL Image. Raises clearly for a dummy-storage placeholder, which has no
    real bytes to fetch."""
    from PIL import Image
    from core import storage

    if blob_url.startswith("dummy-storage/"):
        raise ValueError(
            f"blob_url {blob_url!r} is a dummy-storage placeholder with no "
            f"real object behind it — real extraction has nothing to read. "
            f"Use --stub-extraction for dummy-storage test data, or point "
            f"this at a real MinIO-uploaded scan."
        )

    data = storage.get_object_bytes(blob_url)
    return Image.open(io.BytesIO(data)).convert("RGB")


def extract_diagram_structure(blob_url: str) -> dict:
    """Real image -> graph extraction: detects candidate label regions
    (core/ocr_fallback.py's primary engine), then reads each region across
    every registered OCR engine plugin and keeps the best-confidence read
    (see core/ocr_fallback.py's module docstring), returning them as nodes.
    edges is always empty — see the module docstring; edge/arrow detection
    is a separate, not-yet-built piece."""
    image = _load_image(blob_url)
    ocr = _get_fallback_ocr()
    boxes = ocr.detect_regions(image)

    nodes = []
    for i, (x, y, w, h) in enumerate(boxes):
        left = max(0, x - REGION_CROP_PADDING_PX)
        top = max(0, y - REGION_CROP_PADDING_PX)
        right = min(image.width, x + w + REGION_CROP_PADDING_PX)
        bottom = min(image.height, y + h + REGION_CROP_PADDING_PX)
        crop = image.crop((left, top, right, bottom))

        result = ocr.recognize(crop)
        if not result.text:
            continue  # detected region, but no engine read anything usable — drop it

        nodes.append({
            "node_id": f"n{i + 1}",
            "label": result.text,
            "bbox": [x, y, w, h],
            "confidence": result.confidence,
            "ocr_engine": result.engine,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": [],
        "extraction_warnings": [
            "Edge/arrow detection is not implemented — 'edges' is always "
            "empty for real extraction. Any 'missing edge' finding from "
            "core/diagram_evaluator.py should be read as 'not verified', "
            "not 'confirmed absent', until edge detection is built.",
        ],
    }
