"""
core/diagram_extractor.py — turns a handwritten/scanned diagram image into
the structured graph shape used throughout Task 4 (plan.md §3):

    {"schema_version": 1,
     "nodes": [{"node_id": str, "label": str, "bbox": [x, y, w, h] | None,
                "confidence": float | None,
                "ocr_engine": str | None,
                "shape_bbox": [x, y, w, h], "shape_type": str | None}, ...],
     "edges": [{"edge_id": str, "from_node": str, "to_node": str,
                "label": str | None, "direction": str, "confidence": float | None,
                "needs_review": bool}, ...]}

"ocr_engine" names which OCR engine plugin's read won for that node (e.g.
"paddleocr" or "tesseract") — new since the fallback-OCR framework
(core/ocr_fallback.py); absent/None on stub_extract()'s fake nodes.

"shape_bbox"/"shape_type" and node/edge geometry all come from
core/diagram_shapes.py (OpenCV contour + line/arrow detection) — see that
module's docstring for what diagram content this was validated against
(box+straight-arrow diagrams, confirmed against this repo's real images)
and its known limitations before trusting edge output for scoring.

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

EDGE/ARROW DETECTION (2026-08-30): implemented via core/diagram_shapes.py
(pure OpenCV — contour detection for pairing a label with its enclosing box,
Hough line detection for connectors, dark-pixel-density heuristic for
arrowheads). This resolves plan.md §4.3/PROJECT_CONTEXT.md §7's open
"shape/edge detection engine" decision by extending this codebase's own
existing precedent (PaddleOCR was likewise built in-repo rather than
consumed as an external service, per this module's own migration history
above) rather than treating it as an external service to select later.

Edge detection is meaningfully less mature than label OCR: it has been
validated only against this repo's two real images
(media/diagrams/buses.webp, media/diagrams/hand_drawn_buses_image.jpeg),
both a single diagram family (box diagrams, straight arrows). See
core/diagram_shapes.py's module docstring for known failure modes (a box
whose outline is interrupted by an internal divider can under-size its
mask; arrow-direction detection is unreliable when a line touches a box
border on both ends). Every edge and every node carries a "confidence";
edges below the module's threshold are marked "needs_review": true, same
review-routing convention as answer_blocks.confidence_score
(PROJECT_CONTEXT.md §5) — callers must not treat a low-confidence or
missing edge as "the student drew no connection," only as "not reliably
detected." core/diagram_evaluator.py already handled edge comparison before
this change (it was built ahead of extraction, per plan.md §5) and needed
no changes to consume real (non-empty) edges.

Real image bytes are only available for `blob_url` values from real
("minio") storage — a "dummy-storage/..." placeholder has no object behind
it and raises a clear error instead of silently returning nothing.

HAND-DRAWN ACCURACY FIXES (2026-08-30, same day, after real end-to-end
scoring against hand_drawn_buses_image.jpeg surfaced three root causes —
see the scripts/evaluate_diagram_answer.py run this followed from for the
original diagnosis):

1. A region OCR fails to read isn't necessarily noise — "System Bus" in
   that image is written rotated 90°, which every engine reads as garbage
   at 0°. _recognize_label() now retries a failed read (below
   MIN_LABEL_CHARS) with ±90° rotations before giving up. If a region is
   large enough to plausibly be a real label (>=MIN_REAL_LABEL_AREA_PX2,
   tuned against this image's real-label-vs-noise-region size gap) but
   still unreadable after retry, it's now kept as a "[unreadable]",
   needs_review=True node instead of silently dropped — so its bbox still
   participates in shape/edge detection even though its label can't be
   matched. Previously a dropped node meant every reference edge touching
   it auto-scored "missing" with no way to tell "genuinely absent" from
   "OCR couldn't read this one label."
2. Noise filtering was character-count-only (MIN_LABEL_CHARS), which let
   2+-char OCR misreads of stray marks/arrowheads through as fake nodes
   (e.g. "IT", "Tr", "is", "↑↑" on the same image — all read from the
   arrow/bus-line area, not real labels). Added MIN_LABEL_ALPHA_CHARS
   (rejects non-alphabetic noise like "↑↑"), MIN_LABEL_CONFIDENCE (rejects
   low-confidence short reads unless the region is large enough to be a
   real label per #1), and _drop_nodes_inside_siblings() (rejects a node
   whose own bbox sits inside another node's drawn shape).
3. A real-but-wrong OCR read on a short label (e.g. "CPU" -> "CPV", a
   single-character miss at 0.98 engine confidence — not noise) was falling
   through glossary fuzzy-matching because difflib's ratio metric punishes
   short strings disproportionately for one character. Fixed in
   core/diagram_evaluator.py (see SHORT_LABEL_MAX_EDITS there), not here —
   noted since it's part of the same fix set and was diagnosed from the
   same run.

See core/diagram_shapes.py's docstring for the matching box-detection fix
(#2 there is a distinct, deeper root cause than "small gaps" — page-wide
ruled-line connectivity, not a tuning issue).
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


# Labels shorter than this are dropped as node candidates before shape/edge
# detection ever sees them. In practice these are near-always OCR
# misreading part of an arrowhead or a stray mark as a 1-character "word"
# (e.g. "4", "A", "|" — observed against media/diagrams/buses.webp), not a
# real single-letter label a diagram would use. Left in, they'd get masked
# as nodes in detect_edges() exactly where a real arrow's line/arrowhead
# is, breaking that line's detection — so this filter is load-bearing for
# edge detection, not just label-quality cleanup.
MIN_LABEL_CHARS = 2

# A read must contain at least this many alphabetic characters to be kept as
# a node candidate — rules out arrow-glyph/punctuation noise that clears
# MIN_LABEL_CHARS on character count alone (e.g. "↑↑", "--"), observed
# against media/diagrams/hand_drawn_buses_image.jpeg's arrow/bus-line area.
MIN_LABEL_ALPHA_CHARS = 2

# A read below this confidence is dropped as noise UNLESS its region is
# large enough to plausibly be a real label (see MIN_REAL_LABEL_AREA_PX2) —
# tuned against hand_drawn_buses_image.jpeg, where every genuine label read
# scored >=0.65 and the noise reads that slip past the two filters above
# ("IT" 0.5, "Tr" 0.37, "is" 0.31) all scored well below this.
MIN_LABEL_CONFIDENCE = 0.55

# A region at least this large (px^2) is treated as "plausibly a real label
# even if this read looks like noise/failed" — drives the retry-then-keep-
# unlabeled path in _recognize_label below. Tuned against the same image:
# every genuine label region there is >=48,650px^2 (Memory, the smallest);
# every stray-mark/arrowhead region is <=15,876px^2 ("IT", the largest).
# 20,000 sits with wide margin on both sides of that real gap.
MIN_REAL_LABEL_AREA_PX2 = 20_000

# A node fully (or almost fully) inside another node's own shape_bbox is
# dropped as noise rather than kept as a separate node — e.g. a stray mark
# or arrow fragment sitting inside a box that already has its own label.
# Ratio is (overlap area / candidate's own bbox area).
CONTAINED_IN_SIBLING_RATIO = 0.8


def _is_noise_text(text: str) -> bool:
    alpha_count = sum(1 for c in text if c.isalpha())
    return alpha_count < MIN_LABEL_ALPHA_CHARS


def _recognize_label(ocr, crop) -> "object | None":
    """Reads one cropped region, retrying with rotation/contrast variants
    when the first read fails MIN_LABEL_CHARS — see module docstring update
    (2026-08-30) for why: a label written rotated 90° (this repo's "System
    Bus", written vertically) reads as a 1-2 char garbage token at 0°
    orientation, but the ensemble picks up a much better (if still
    imperfect) read once rotated. Returns the best OCRResult found across
    all attempts, or None if nothing cleared MIN_LABEL_CHARS anywhere."""
    attempts = [crop, crop.rotate(90, expand=True), crop.rotate(-90, expand=True)]
    best = None
    for attempt in attempts:
        result = ocr.recognize(attempt)
        if not result.text or len(result.text) < MIN_LABEL_CHARS:
            continue
        if best is None or (result.confidence or 0) > (best.confidence or 0):
            best = result
    return best


def _bbox_overlap_area(a: list[int], b: list[int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ox = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    oy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ox * oy


def _drop_nodes_inside_siblings(nodes: list[dict]) -> list[dict]:
    """Drops a node whose OWN text bbox sits almost entirely inside another
    node's enclosing SHAPE (drawn box), e.g. a stray mark/arrow fragment
    that OCR misread as a short word inside a box that already has its own
    real label — see MIN_LABEL_ALPHA_CHARS/MIN_LABEL_CONFIDENCE above for
    the noise this doesn't already catch (a 2+ alpha-char, decent-confidence
    misread can still slip through on text content alone)."""
    kept = []
    for node in nodes:
        own_area = node["bbox"][2] * node["bbox"][3]
        if own_area <= 0:
            kept.append(node)
            continue
        inside_sibling = any(
            other is not node
            and _bbox_overlap_area(node["bbox"], other["shape_bbox"]) >= own_area * CONTAINED_IN_SIBLING_RATIO
            for other in nodes
        )
        if not inside_sibling:
            kept.append(node)
    return kept


def extract_diagram_structure(blob_url: str) -> dict:
    """Real image -> graph extraction. Nodes: detects candidate label
    regions (core/ocr_fallback.py's primary engine), reads each region
    across every registered OCR engine plugin and keeps the best-confidence
    read (see core/ocr_fallback.py's module docstring), then pairs each
    label with its enclosing drawn shape (core/diagram_shapes.pair_shapes).
    Edges: detected separately via core/diagram_shapes.detect_edges, using
    the paired shapes to mask node regions out before line/arrow detection.
    See core/diagram_shapes.py's module docstring for what this has been
    validated against and its known limitations."""
    from core import diagram_shapes

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
        is_large_region = (w * h) >= MIN_REAL_LABEL_AREA_PX2
        usable = (
            result.text and len(result.text) >= MIN_LABEL_CHARS
            and not _is_noise_text(result.text)
            and (result.confidence is None or result.confidence >= MIN_LABEL_CONFIDENCE
                 or is_large_region)
        )

        if not usable:
            if not is_large_region:
                continue  # too small to plausibly be a real label — noise, drop it
            # Large enough to plausibly be a real label even though this read
            # wasn't usable — retry with rotation before giving up on it.
            retried = _recognize_label(ocr, crop)
            if retried is not None and not _is_noise_text(retried.text):
                result = retried
            else:
                nodes.append({
                    "node_id": f"n{i + 1}",
                    "label": "[unreadable]",
                    "bbox": [x, y, w, h],
                    "confidence": 0.0,
                    "ocr_engine": None,
                    "needs_review": True,
                })
                continue

        nodes.append({
            "node_id": f"n{i + 1}",
            "label": result.text,
            "bbox": [x, y, w, h],
            "confidence": result.confidence,
            "ocr_engine": result.engine,
        })

    import numpy as np
    gray = np.array(image.convert("L"))

    diagram_shapes.pair_shapes(gray, nodes)
    nodes = _drop_nodes_inside_siblings(nodes)
    edges = diagram_shapes.detect_edges(gray, nodes)

    warnings = []
    low_conf_nodes = [n["label"] for n in nodes if n["shape_type"] is None]
    if low_conf_nodes:
        warnings.append(
            f"No enclosing shape found for {len(low_conf_nodes)} node(s) "
            f"({', '.join(low_conf_nodes)}) — shape_bbox falls back to the "
            f"label's own text bbox for these."
        )
    needs_review_edges = [e["edge_id"] for e in edges if e["needs_review"]]
    if needs_review_edges:
        warnings.append(
            f"{len(needs_review_edges)} edge(s) below the confidence "
            f"threshold ({', '.join(needs_review_edges)}) — flagged "
            f"needs_review, should route to human review rather than be "
            f"trusted as-detected."
        )
    warnings.append(
        "Edge detection (core/diagram_shapes.py) has only been validated "
        "against box+straight-arrow diagrams — treat results on other "
        "diagram families as unverified. A missing edge should be read as "
        "'not reliably detected', not 'confirmed absent'."
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "nodes": nodes,
        "edges": edges,
        "extraction_warnings": warnings,
    }
