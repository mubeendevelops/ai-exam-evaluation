"""
core/diagram_extractor.py — turns a handwritten/scanned diagram image into
the structured graph shape used throughout Task 4 (plan.md §3):

    {"schema_version": 1,
     "nodes": [{"node_id": str, "label": str, "bbox": [x, y, w, h] | None,
                "confidence": float | None}, ...],
     "edges": [{"edge_id": str, "from_node": str, "to_node": str,
                "label": str | None, "confidence": float | None}, ...]}

STATUS: real HANDWRITTEN LABEL extraction is implemented via PaddleOCR
(local, free, no API key, no student data leaves the server) — its
DB-based text detector (PP-OCRv5) locates candidate label regions, and its
recognition module (also PP-OCRv5) reads each cropped region.

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

import contextlib
import io
import os

SCHEMA_VERSION = 1

# PP-OCRv5 detection: a DB-based (Differentiable Binarization) text
# detector, the same family EasyOCR's CRAFT detector belongs to (a trained
# model, not a threshold/morphology heuristic — see module docstring for
# why that distinction matters on real ruled-paper photos). "_server_"
# (larger, more accurate) rather than "_mobile_" (smaller/faster), matching
# this codebase's existing preference for accuracy over latency in this
# pipeline (the old TrOCR-based code had the same preference — its own TODO
# was to move from the *small*-handwritten checkpoint up to *base*-handwritten
# for exactly this reason). Override with PADDLEOCR_DET_MODEL.
_DET_MODEL_NAME = os.environ.get("PADDLEOCR_DET_MODEL", "PP-OCRv5_server_det")

# PP-OCRv5 recognition — the "handwriting-tuned" checkpoint asked for:
# unlike PP-OCRv4 (tuned mainly for printed/scene text, same role TrOCR's
# *printed* checkpoint would have played), PP-OCRv5's training data
# explicitly broadens coverage to include handwritten text, which is this
# whole pipeline's actual input (see module docstring — this is a
# handwritten/scanned diagram label pipeline end to end, there is no
# "printed" code path to preserve here). The "en_..._mobile_" variant (not
# the multilingual "_server_" default) is used specifically because, when
# compared side by side during migration testing, the
# multilingual model misread diagram scribbles/arrows as CJK characters
# ("个", "不") on real handwritten input, while the English-constrained
# model's character set naturally rules that out and scored equal-or-better
# on every real label. Override with PADDLEOCR_REC_MODEL if the exam
# content's language isn't English (see the model list in
# paddlex.modules.text_recognition.model_list for other language options).
_REC_MODEL_NAME = os.environ.get("PADDLEOCR_REC_MODEL", "en_PP-OCRv5_mobile_rec")

REGION_CROP_PADDING_PX = 6

# Detection thresholds — lowered from PaddleOCR's own defaults (thresh=0.3,
# box_thresh=0.6, unclip_ratio=1.5) for the same reason the old EasyOCR
# thresholds were lowered from ITS defaults: hand-drawn labels are
# lower-contrast and less uniform than the printed/scene text these
# detectors are tuned for by default. unclip_ratio is raised so boxes are
# expanded a bit more generously around the detected text core, since
# handwritten strokes vary more in size than printed glyphs.
DETECT_THRESH = 0.2
DETECT_BOX_THRESH = 0.3
DETECT_UNCLIP_RATIO = 2.0

# Disables oneDNN/MKL-DNN acceleration for both models. Required, not
# optional, on this deployment's paddlepaddle build (3.3.1, CPU): with
# oneDNN enabled, PP-OCRv5_server_det's inference raises
# `NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
# not support [pir::ArrayAttribute<pir::DoubleAttribute>]` from paddle's PIR
# executor — a paddlepaddle/oneDNN op-support gap, not a model or code bug.
# Disabling oneDNN avoids the unsupported code path entirely, at a modest
# CPU inference speed cost. Revisit if a paddlepaddle upgrade fixes the
# underlying op support.
_ENABLE_MKLDNN = False

_text_detector = None
_text_recognizer = None


# Skips paddlex's "Checking connectivity to the model hosters, this may
# take a while" probe on every single call — paddlex's own message names
# this exact env var as the documented way to bypass it. Set once at import
# time (not per-call) since it's a load-time behavior, not something that
# should flip mid-process.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


@contextlib.contextmanager
def _quiet_paddle():
    """Suppresses PaddleOCR/paddlex/paddle's own chatter during model
    construction and inference — "Using official model...", "Model files
    already exist. Using cached files...", per-file download progress bars,
    plus a harmless UserWarning paddle emits on CPU builds without ccache
    installed. Some of this comes through Python's logging module (a
    library-configured handler can hold a direct reference to the original
    stdout, so redirecting sys.stdout alone doesn't catch it — hence
    disabling logging output directly here), some through plain print().
    Scoped narrowly to each model-construction/predict() call (the same
    "don't blanket-hide warnings globally" approach the old EasyOCR code
    used for its own harmless warning), not applied globally — real errors
    still raise and their tracebacks still print, since this only silences
    logging/stdout, not exceptions or stderr.

    This exists purely to keep scripts/evaluate_diagram_answer.py's output
    readable; it has no effect on what gets extracted or stored."""
    import logging
    import warnings
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning, message="No ccache found")
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield
        finally:
            logging.disable(logging.NOTSET)


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


def _get_text_detector():
    """Lazy-loaded PaddleOCR text-detection module (detection only —
    recognition is _get_text_recognizer's job, see _recognize_text)."""
    global _text_detector
    if _text_detector is None:
        from paddleocr import TextDetection
        with _quiet_paddle():
            _text_detector = TextDetection(
                model_name=_DET_MODEL_NAME,
                enable_mkldnn=_ENABLE_MKLDNN,
                thresh=DETECT_THRESH,
                box_thresh=DETECT_BOX_THRESH,
                unclip_ratio=DETECT_UNCLIP_RATIO,
            )
    return _text_detector


def _get_text_recognizer():
    """Lazy-loaded PaddleOCR text-recognition module."""
    global _text_recognizer
    if _text_recognizer is None:
        from paddleocr import TextRecognition
        with _quiet_paddle():
            _text_recognizer = TextRecognition(
                model_name=_REC_MODEL_NAME,
                enable_mkldnn=_ENABLE_MKLDNN,
            )
    return _text_recognizer


def _detect_text_regions(image) -> list[tuple[int, int, int, int]]:
    """Finds candidate label bounding boxes via PaddleOCR's DB-based text
    detector (detection only — recognition is _recognize_text's job).
    Returns (x, y, w, h) boxes, top-to-bottom then left-to-right.

    PaddleOCR's detector reports arbitrary quadrilaterals (`dt_polys`) —
    these are reduced to their axis-aligned bounding box for simplicity,
    the same approach the old EasyOCR-based code used for its rotated/skewed
    ("free_list") regions, since the recognizer expects a rectangular crop
    and does not need perspective correction for a mild skew."""
    import numpy as np

    detector = _get_text_detector()
    arr = np.array(image)  # RGB, as PaddleOCR expects

    with _quiet_paddle():
        results = list(detector.predict(arr))
    if not results:
        return []
    polys = dict(results[0]).get("dt_polys", [])

    boxes = []
    for poly in polys:
        xs = [int(pt[0]) for pt in poly]
        ys = [int(pt[1]) for pt in poly]
        x0, y0 = min(xs), min(ys)
        boxes.append((x0, y0, max(xs) - x0, max(ys) - y0))

    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


def _recognize_text(crop) -> tuple[str, float | None]:
    """Runs one cropped region through PaddleOCR's recognizer. Returns
    (text, confidence); confidence is PaddleOCR's own `rec_score` for the
    predicted sequence — not calibrated, but usable as a relative signal for
    low-confidence routing, the same role answer_blocks.confidence_score
    already plays for text OCR (and the same role the old code's
    manually-computed TrOCR confidence played)."""
    import numpy as np

    recognizer = _get_text_recognizer()
    with _quiet_paddle():
        results = list(recognizer.predict(np.array(crop)))
    if not results:
        return "", None

    result = dict(results[0])
    text = (result.get("rec_text") or "").strip()
    score = result.get("rec_score")
    confidence = round(float(score), 4) if score is not None else None
    return text, confidence


def extract_diagram_structure(blob_url: str) -> dict:
    """Real image -> graph extraction: detects candidate label regions,
    reads each with PaddleOCR's recognizer, returns them as nodes. edges is
    always empty — see the module docstring; edge/arrow detection is a
    separate, not-yet-built piece."""
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
            continue  # detected region, but recognizer read nothing usable — drop it

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
