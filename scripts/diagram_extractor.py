"""
core/diagram_extractor.py — turns a handwritten/scanned diagram image into
the structured graph shape used throughout Task 4 (plan.md §3):

    {"schema_version": 1,
     "nodes": [{"node_id": str, "label": str, "bbox": [x, y, w, h] | None,
                "confidence": float | None}, ...],
     "edges": [{"edge_id": str, "from_node": str, "to_node": str,
                "label": str | None, "confidence": float | None}, ...]}

STATUS (confirmed with product owner): real HANDWRITTEN LABEL extraction is
implemented — EasyOCR's CRAFT-based text detector locates candidate label
regions, then Microsoft's TrOCR reads each one (local, free, no API
key/cost, no student data leaves the server — see the OCR-engine decision
this resolves, plan.md §4.3 / PROJECT_CONTEXT.md §7 "OCR engine choice").

Detection went through two iterations. The first cut used plain OpenCV
(Otsu threshold + morphological dilation + contours) — no ML model. That
worked on a clean synthetic test image, but failed on a real photo of
handwriting on ruled notebook paper: the ruled lines and connecting
arrows/box borders are picked up as "text" by simple thresholding, and no
amount of morphology tuning reliably separated them from actual letters
(verified against a real test photo — line-shaped regions produced garbage
OCR output like repeated "0 000 000...", and a fixed-pixel dilation kernel
tuned for one image resolution didn't generalize to another). Separating
handwritten text from touching graphical elements is exactly the problem
trained text-detection models exist for, so detection now uses EasyOCR's
CRAFT detector (still fully local/free) purely for region-finding — TrOCR
remains the recognizer. This combination was verified end-to-end against a
real photographed diagram and correctly located 6 of 7 hand-drawn labels
(the miss was text rotated 90°, a known limitation of this detector -
see _detect_text_regions).

NOT implemented: edge/arrow detection. Text-region detection tells us WHERE
labels are and WHAT they say; it says nothing about which nodes are
connected to which — that needs line/arrow tracing, a separate CV problem
this module does not attempt. extract_diagram_structure() always returns
edges=[] for real extraction and says so explicitly in the returned dict's
"extraction_warnings" — callers (core/diagram_evaluator.py) must not treat
an empty extracted edge list as "the student drew no connections," only as
"connections were not checked."

Real image bytes are only available for `blob_url` values from real
("minio") storage — a "dummy-storage/..." placeholder has no object behind
it and raises a clear error instead of silently returning nothing.
"""
from __future__ import annotations

import io
import os

SCHEMA_VERSION = 1

# microsoft/trocr-small-handwritten (not -base-): smaller download, faster
# CPU inference, matching this codebase's existing "small/fast model first"
# choice for sentence-transformers (all-MiniLM-L6-v2). Override with
# TROCR_MODEL if -base-handwritten's better accuracy is worth the extra
# size/latency for a given deployment.
_TROCR_MODEL_NAME = os.environ.get("TROCR_MODEL", "microsoft/trocr-small-handwritten")

REGION_CROP_PADDING_PX = 6

# EasyOCR detector confidence thresholds — lowered slightly from its
# defaults (0.7 / 0.4 / 0.4) since hand-drawn labels are lower-contrast and
# less uniform than the printed/scene text EasyOCR is tuned for by default.
DETECT_TEXT_THRESHOLD = 0.5
DETECT_LOW_TEXT = 0.3
DETECT_LINK_THRESHOLD = 0.3

_trocr_processor = None
_trocr_model = None
_easyocr_reader = None


def stub_extract(blob_url: str) -> dict:
    """Deterministic fake extraction, for testing without touching the real
    pipeline (no image fetch, no OpenCV, no TrOCR model load). Returns a
    fixed graph regardless of input — labels are prefixed "[STUB]" so it's
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


def _get_easyocr_reader():
    """Lazy-loaded EasyOCR reader. verbose=False suppresses its own
    "Using CPU. Note: this module is much faster with a GPU." print; the
    warnings suppression below is scoped narrowly to just this constructor
    call, not applied globally, since it's specifically silencing a known,
    harmless PyTorch deprecation notice (torch.quantize_per_tensor) raised
    while EasyOCR builds its internal quantized model — not a blanket
    "hide all warnings" for the rest of this process."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import warnings
        import easyocr
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _easyocr_reader


def _detect_text_regions(image) -> list[tuple[int, int, int, int]]:
    """Finds candidate label bounding boxes via EasyOCR's CRAFT text
    detector (detection only — recognition is TrOCR's job, see
    _recognize_text). Returns (x, y, w, h) boxes, top-to-bottom then
    left-to-right.

    EasyOCR reports two kinds of regions: axis-aligned ("horizontal_list")
    and arbitrary quadrilaterals ("free_list", for rotated/skewed text —
    e.g. a label on a page photographed at a slight angle). Both are
    included here, with free-list quads reduced to their axis-aligned
    bounding box for simplicity, since TrOCR itself expects a rectangular
    crop and does not need perspective correction for a mild skew. Text
    rotated a full 90° (e.g. a label written sideways along a margin) is a
    known miss for this detector at default settings — not handled here."""
    import numpy as np

    reader = _get_easyocr_reader()
    arr = np.array(image)  # RGB, as EasyOCR expects

    horizontal_list, free_list = reader.detect(
        arr,
        text_threshold=DETECT_TEXT_THRESHOLD,
        low_text=DETECT_LOW_TEXT,
        link_threshold=DETECT_LINK_THRESHOLD,
    )

    boxes = []
    for x_min, x_max, y_min, y_max in horizontal_list[0]:
        boxes.append((int(x_min), int(y_min), int(x_max - x_min), int(y_max - y_min)))
    for poly in free_list[0]:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x0, y0 = int(min(xs)), int(min(ys))
        boxes.append((x0, y0, int(max(xs)) - x0, int(max(ys)) - y0))

    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def _get_trocr():
    """Lazy-loaded TrOCR processor/model. set_verbosity_error() suppresses
    transformers' own INFO/WARNING-level logging during from_pretrained()
    — specifically "Config of the encoder/decoder is overwritten by shared
    config..." and "Some weights ... were not initialized ... You should
    probably TRAIN this model...". Both are expected/harmless for this
    architecture (VisionEncoderDecoderModel composing a DeiT encoder + TrOCR
    decoder shares config by design; the unused vision pooler head is never
    called during text generation) — not actual problems to warn about."""
    global _trocr_processor, _trocr_model
    if _trocr_model is None:
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        from transformers.utils import logging as hf_logging
        hf_logging.set_verbosity_error()
        _trocr_processor = TrOCRProcessor.from_pretrained(_TROCR_MODEL_NAME)
        _trocr_model = VisionEncoderDecoderModel.from_pretrained(_TROCR_MODEL_NAME)
        _trocr_model.eval()
    return _trocr_processor, _trocr_model


def _recognize_text(crop) -> tuple[str, float | None]:
    """Runs one cropped region through TrOCR. Returns (text, confidence);
    confidence is the mean per-token probability of the generated sequence
    (manually computed from generate()'s per-step logits, not via
    model.compute_transition_scores() — that helper assumes a top-level
    config.vocab_size, which a composite VisionEncoderDecoderModel's config
    doesn't have, and raises AttributeError). Not calibrated, but usable as
    a relative signal for low-confidence routing, the same role
    answer_blocks.confidence_score already plays for text OCR."""
    import torch
    import torch.nn.functional as F

    processor, model = _get_trocr()
    pixel_values = processor(images=crop, return_tensors="pt").pixel_values

    with torch.no_grad():
        outputs = model.generate(
            pixel_values, max_new_tokens=32,
            output_scores=True, return_dict_in_generate=True,
        )

    text = processor.batch_decode(outputs.sequences, skip_special_tokens=True)[0].strip()

    confidence = None
    try:
        # outputs.sequences includes the decoder start token, so the token
        # generated at step i is sequences[i + 1]; outputs.scores[i] holds
        # that step's pre-softmax logits.
        sequence = outputs.sequences[0]
        log_probs = [
            F.log_softmax(step_logits[0], dim=-1)[sequence[i + 1]].item()
            for i, step_logits in enumerate(outputs.scores)
        ]
        if log_probs:
            confidence = round(float(torch.exp(torch.tensor(sum(log_probs) / len(log_probs)))), 4)
    except Exception:
        confidence = None  # confidence is best-effort; a bad extraction shouldn't block on this

    return text, confidence


def extract_diagram_structure(blob_url: str) -> dict:
    """Real image -> graph extraction: detects candidate label regions,
    reads each with TrOCR, returns them as nodes. edges is always empty —
    see the module docstring; edge/arrow detection is a separate, not-yet-
    built piece."""
    image = _load_image(blob_url)
    boxes = _detect_text_regions(image)

    nodes = []
    for i, (x, y, w, h) in enumerate(boxes):
        left = max(0, x - REGION_CROP_PADDING_PX)
        top = max(0, y - REGION_CROP_PADDING_PX)
        right = min(image.width, x + w + REGION_CROP_PADDING_PX)
        bottom = min(image.height, y + h + REGION_CROP_PADDING_PX)
        crop = image.crop((left, top, right, bottom))

        text, confidence = _recognize_text(crop)
        if not text:
            continue  # detected region, but TrOCR read nothing usable — drop it

        nodes.append({
            "node_id": f"n{i + 1}",
            "label": text,
            "bbox": [x, y, w, h],
            "confidence": confidence,
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
