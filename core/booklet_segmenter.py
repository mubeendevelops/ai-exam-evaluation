"""Page segmentation, region classification and question association.

The second stage of the full-booklet pipeline (Task 5). Takes prepared page
images from core/booklet_ingest.py and returns regions — each a bounding box
with a block_type ('text' | 'table' | 'diagram' | 'formula'), a classification
confidence, and the question marker it belongs to. Pure CV/OCR: no DB, no
storage, no network. Persistence lives in core/booklet_persist.py, mirroring
how core/table_extractor.py stays pure and core/plugins/persistence.py owns
the writes.

ENGINE CHOICE — PaddleOCR's LayoutDetection (PP-DocLayout_plus-L), chosen over
both the full PP-StructureV3 pipeline and a hand-rolled OpenCV heuristic:

  vs PP-StructureV3: LayoutDetection *is* the layout model inside
  PP-StructureV3; the pipeline wraps it with table-structure, formula and OCR
  sub-pipelines that this repo already owns as plugins. Running it would
  duplicate core/table_extractor.py, bypass core/ocr_fallback.py, and
  CLAUDE_CONTEXT.md §7B measured that stack at 48.2 s/image against 3.4 s for
  this repo's own. Measured here: LayoutDetection is ~18 s one-time
  construction, then ~3 s/page.

  vs an OpenCV ruling-line + contour-density heuristic: the deciding argument
  is confidence honesty. A region's classification confidence gates whether a
  human reviews it, and a learned detector emits a real score. A heuristic
  would have to invent one, and an invented confidence driving an unattended
  routing decision is worse than no confidence at all. The heuristic survives
  only as a fallback (_segment_heuristic) for when the model cannot be loaded,
  and everything it produces is force-flagged needs_review.

  §7B records that PP-StructureV3 crashes on this repo's pinned
  paddlepaddle 3.3.1 unless constructed with enable_mkldnn=False. The same
  guard applies to LayoutDetection here, and is applied through the shared
  core/paddle_workarounds.py helper — the same one
  core/ocr_engines/paddleocr_engine.py uses for TextDetection/TextRecognition
  (see that module's docstring for the crash detail and the version this is
  measured against).

QUESTION MARKERS COME FROM A SEPARATE OCR PASS, not from the layout model.
This is a measured decision, not a stylistic one: on the benchmark booklet the
layout model labels "Q1." as `paragraph_title` (0.548), merges the "a)" marker
into the paragraph that follows it, and on page 2 misses the "Q2." marker
region entirely. Markers are small, and layout models are trained to find
prose blocks. Reading them with the existing core/ocr_fallback.py ensemble
found all five markers at 0.90-0.9996 confidence.

MEASURED SCOPE — scored by scripts/benchmark_booklet_segmentation.py against
media/booklets/ground_truth.json (4 pages, 10 answer regions, 5 markers):

    region detection (IoU >= 0.5)   10/10 = 1.000   (per-region IoU 0.75-0.98)
    classification                  10/10 = 1.000
    question assignment             10/10 = 1.000
    spurious regions                0
    markers detected                5/5   (Q1. Q2. a) b) Q3.)

READ THOSE NUMBERS CAREFULLY, the same way CLAUDE_CONTEXT.md §7/§7B ask their
own benchmarks to be read. The fixture pages are RENDERED, not scanned: clean
unbroken ink, no perspective, no camera noise, no ruled-paper bleed-through,
and one region per block with generous whitespace between blocks. A 1.000 here
is evidence that the wiring is correct — coordinates, reading order, cross-page
marker carry-over — and is NOT evidence about real scanned booklets.

NOT handled, and deliberately not faked: multi-column pages (reading order
here is a single-column sort by y then x), rotated/perspective camera photos,
regions that interleave rather than stack, and answer text on ruled notebook
paper — the last being this project's real input distribution and its known
hard case. No fixture for any of these was fabricated.
"""
import os
import re

import numpy as np

from core.paddle_workarounds import construct, quiet_paddle

# --- classification tunables --------------------------------------------

# Below this, a region keeps its predicted block_type but is flagged for a
# human rather than routed to a plugin unattended. 0.60 was chosen against
# this repo's existing fixtures, where it correctly separates the confident
# reads (bordered table 0.972, diagram 0.888-0.981, prose 0.88-0.97) from the
# genuinely marginal ones (borderless table 0.536, sparse text page 0.534).
DEFAULT_MIN_CONFIDENCE = 0.60

# The heuristic fallback cannot produce a calibrated score, so everything it
# emits is pinned here AND flagged — a fabricated confidence must never be
# able to clear DEFAULT_MIN_CONFIDENCE.
HEURISTIC_CONFIDENCE = 0.30

LAYOUT_MODEL_NAME = os.environ.get("PADDLEOCR_LAYOUT_MODEL", "PP-DocLayout_plus-L")

# PP-DocLayout_plus-L's full 20-label vocabulary, collapsed onto the four
# answer_block_type enum values from migration 001. Labels are lossy on
# purpose; the raw label is preserved per region as `layout_label` so a
# mis-route can be debugged against what was actually predicted.
LAYOUT_TO_BLOCK_TYPE = {
    "text": "text",
    "paragraph_title": "text",
    "doc_title": "text",
    "abstract": "text",
    "content": "text",
    "reference": "text",
    "reference_content": "text",
    "footnote": "text",
    "aside_text": "text",
    "number": "text",
    "algorithm": "text",
    "figure_title": "text",
    "table": "table",
    "image": "diagram",
    "chart": "diagram",
    "formula": "formula",
}

# Page furniture, never a student answer. Dropped before assignment so it
# cannot consume a question marker or appear in the review queue.
IGNORED_LAYOUT_LABELS = {"header", "footer", "seal", "formula_number"}

# 'formula' is a valid answer_block_type but NO plugin supports it
# (core/plugins/ registers text, table and diagram only), so a formula region
# would silently vanish at routing time. Flag it instead.
UNSUPPORTED_BLOCK_TYPES = {"formula"}

# --- marker tunables -----------------------------------------------------

# Markers sit in the left margin and are short. Restricting OCR candidates to
# that band is what keeps marker detection to a handful of recognize() calls
# per page instead of one per text line.
MARKER_LEFT_BAND_RATIO = 0.30
MARKER_MAX_WIDTH_RATIO = 0.30
MARKER_MIN_CONFIDENCE = 0.50

# A marker candidate whose centre falls inside a table or diagram is a cell
# label or a node label, not a question number. Without this, the benchmark
# table's "FCFS"/"SJF" column and any numbered cell become phantom questions.
MARKER_EXCLUDE_BLOCK_TYPES = {"table", "diagram"}

# Full-token match only. The OCR detector returns the marker as its own box,
# so anchoring both ends is safe and rejects prose that merely starts with a
# digit ("4 ms of quantum..."), which a prefix match would swallow.
NUMERIC_MARKER_RE = re.compile(r"^\(?\s*(?:Q|q)?\s*(\d{1,2})\s*[.):]?\s*\)?$")
SUBPART_MARKER_RE = re.compile(r"^\(?\s*([a-h])\s*[.):]\s*\)?$")

# Two markers within this many pixels of each other vertically cannot be
# ordered reliably, so regions after them are flagged ambiguous, not guessed.
MARKER_AMBIGUITY_PX = 12

# A layout region this well covered by a marker's own box IS the marker, and
# is dropped rather than emitted as an answer region.
MARKER_REGION_OVERLAP = 0.60

_LAYOUT_MODEL = None


def _get_layout_model():
    """Lazily construct and cache the layout detector.

    Cached at module level because construction measured ~18 s against ~3 s
    per page — rebuilding it per page would dominate a booklet's runtime.
    Lazy so that --stub runs, and every import of this module, cost nothing.

    Construction (including the enable_mkldnn guard — see module docstring /
    §7B and core/paddle_workarounds.py) goes through the same helper
    core/ocr_engines/paddleocr_engine.py uses; only the caching shown here is
    specific to this module.
    """
    global _LAYOUT_MODEL
    if _LAYOUT_MODEL is None:
        from paddleocr import LayoutDetection
        _LAYOUT_MODEL = construct(LayoutDetection, model_name=LAYOUT_MODEL_NAME)
    return _LAYOUT_MODEL


def _to_xywh(coordinate) -> list[int]:
    """Layout boxes arrive as [x1, y1, x2, y2] float32; the rest of this repo
    speaks [x, y, w, h] ints (core/table_extractor.py's per-cell bbox)."""
    x1, y1, x2, y2 = (int(round(float(v))) for v in coordinate)
    return [x1, y1, x2 - x1, y2 - y1]


def _overlap_ratio(inner: list[int], outer: list[int]) -> float:
    """Fraction of `inner`'s area that falls inside `outer`."""
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    left, top = max(ix, ox), max(iy, oy)
    right, bottom = min(ix + iw, ox + ow), min(iy + ih, oy + oh)
    if right <= left or bottom <= top:
        return 0.0
    area = iw * ih
    return ((right - left) * (bottom - top)) / area if area else 0.0


def _contains_point(bbox: list[int], x: int, y: int) -> bool:
    bx, by, bw, bh = bbox
    return bx <= x <= bx + bw and by <= y <= by + bh


def classify_page(
    image,
    *,
    page_number: int = 1,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> list[dict]:
    """Segment one prepared page into classified regions.

    Returns a list of region dicts sorted in reading order:
        {"page_number", "bbox": [x, y, w, h], "block_type", "layout_label",
         "confidence", "needs_review", "review_reasons": [...], "segmenter"}

    Falls back to _segment_heuristic if the layout model cannot be loaded,
    the same way core/ocr_fallback.py degrades when an engine is unavailable
    rather than taking the whole pipeline down with it.
    """
    try:
        model = _get_layout_model()
    except Exception as exc:                       # noqa: BLE001 - see below
        # Deliberately broad: a missing model file, an incompatible paddle
        # build and an oneDNN crash all surface differently, and none of them
        # should abort a booklet when a degraded-but-flagged path exists.
        regions = _segment_heuristic(image, page_number=page_number)
        for region in regions:
            region["review_reasons"].append(f"layout_model_unavailable: {exc.__class__.__name__}")
        return regions

    array = np.array(image.convert("RGB"))[:, :, ::-1]   # PIL RGB -> OpenCV BGR
    with quiet_paddle():
        predictions = model.predict(array)

    regions = []
    for prediction in predictions:
        for box in prediction["boxes"]:
            label = str(box["label"])
            if label in IGNORED_LAYOUT_LABELS:
                continue
            block_type = LAYOUT_TO_BLOCK_TYPE.get(label)
            if block_type is None:
                # An unmapped label is a model/version change, not a student
                # error. Keep the region, route it nowhere, flag it loudly.
                block_type, reasons = "text", [f"unmapped_layout_label:{label}"]
            else:
                reasons = []

            confidence = float(box["score"])
            if confidence < min_confidence:
                reasons.append("low_confidence")
            if block_type in UNSUPPORTED_BLOCK_TYPES:
                reasons.append("no_plugin_supports_block_type")

            regions.append({
                "page_number": page_number,
                "bbox": _to_xywh(box["coordinate"]),
                "block_type": block_type,
                "layout_label": label,
                "confidence": round(confidence, 4),
                "needs_review": bool(reasons),
                "review_reasons": reasons,
                "segmenter": "layout_model",
            })

    # Single-column reading order. Multi-column pages are out of scope and
    # this sort is where that shows up first — see the module docstring.
    regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return regions


def _segment_heuristic(image, *, page_number: int = 1) -> list[dict]:
    """Fallback segmentation: OpenCV ruling lines + contour density.

    Used ONLY when the layout model is unavailable. Reuses
    core/table_extractor.py::detect_ruling_lines rather than re-deriving it.
    Every region it returns is flagged: this path cannot produce a calibrated
    confidence, and an uncalibrated number must not be able to authorize
    unattended routing.
    """
    import cv2
    from core.diagram_shapes import binarize
    from core.table_extractor import detect_ruling_lines

    gray = np.array(image.convert("L"))
    ink = binarize(gray)

    # Dilate horizontally to merge words into blocks, then take connected
    # components as candidate regions.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (45, 15))
    blocks = cv2.dilate(ink, kernel, iterations=2)
    contours, _ = cv2.findContours(blocks, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    rows, cols = detect_ruling_lines(gray)
    page_area = gray.shape[0] * gray.shape[1]

    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < page_area * 0.002:      # discard specks
            continue

        # A region spanned by several ruling lines on BOTH axes is a table;
        # otherwise high ink density with few lines suggests a drawing.
        inner_rows = [r for r in rows if y < r < y + h]
        inner_cols = [c for c in cols if x < c < x + w]
        density = float(cv2.countNonZero(ink[y:y + h, x:x + w])) / max(w * h, 1)

        if len(inner_rows) >= 2 and len(inner_cols) >= 2:
            block_type = "table"
        elif density < 0.06 and h > 80:
            block_type = "diagram"
        else:
            block_type = "text"

        regions.append({
            "page_number": page_number,
            "bbox": [x, y, w, h],
            "block_type": block_type,
            "layout_label": f"heuristic:{block_type}",
            "confidence": HEURISTIC_CONFIDENCE,
            "needs_review": True,
            "review_reasons": ["heuristic_segmentation"],
            "segmenter": "heuristic",
        })

    regions.sort(key=lambda r: (r["bbox"][1], r["bbox"][0]))
    return regions


def stub_segment(image=None, *, page_number: int = 1) -> list[dict]:
    """Deterministic fixed regions, no model load and no OCR — so --stub runs
    offline, per PROJECT_CONTEXT.md rule 6."""
    return [
        {"page_number": page_number, "bbox": [100, 150, 900, 200],
         "block_type": "text", "layout_label": "stub", "confidence": 0.99,
         "needs_review": False, "review_reasons": [], "segmenter": "stub"},
        {"page_number": page_number, "bbox": [100, 400, 900, 300],
         "block_type": "table", "layout_label": "stub", "confidence": 0.42,
         "needs_review": True, "review_reasons": ["low_confidence"],
         "segmenter": "stub"},
    ]


def stub_markers(*, page_number: int = 1) -> list[dict]:
    """One deterministic 'Q1' marker on page 1, none afterwards.

    stub_segment() alone produces regions that NOTHING can place: with no
    markers, assign_regions() correctly refuses to guess and every region
    comes back unassigned — so a --stub run exercised segmentation and then
    stopped short of assignment, association and persistence, which is most of
    what ingestion actually does. This marker closes that gap without
    weakening the contract: it sits above the stub regions and carries a real
    confidence, so assignment runs the same code it runs on a real booklet.

    Page 1 only, deliberately. An answer that continues onto later pages has
    no marker of its own, so a single marker on the first page is also the
    stub of the cross-page carry-over rule (§7C) rather than a marker
    conveniently repeated on every page.
    """
    if page_number != 1:
        return []
    return [{
        "page_number": 1,
        # Left band, above stub_segment's first region (y=150), and far enough
        # from it that MARKER_REGION_OVERLAP cannot swallow the region.
        "bbox": [40, 60, 60, 40],
        "raw_text": "Q1.",
        "kind": "numeric",
        "value": "1",
        "confidence": 0.99,
        "ocr_engine": "stub",
    }]


# --- question markers ----------------------------------------------------

def _normalize_marker(text: str) -> tuple[str, str] | None:
    """Map raw OCR text to (kind, value): ('numeric', '3') or ('subpart', 'a')."""
    cleaned = text.strip().replace(" ", "")
    if not cleaned:
        return None
    match = NUMERIC_MARKER_RE.match(cleaned)
    if match:
        return "numeric", str(int(match.group(1)))
    match = SUBPART_MARKER_RE.match(cleaned)
    if match:
        return "subpart", match.group(1).lower()
    return None


def detect_question_markers(
    image,
    *,
    page_number: int = 1,
    regions: list[dict] | None = None,
    ocr=None,
) -> list[dict]:
    """Find question-number markers on one page via the existing OCR ensemble.

    `regions` (from classify_page) is used only to reject candidates that fall
    inside a table or diagram — a numbered table cell is not a question.

    Returns [{"page_number", "bbox", "raw_text", "kind", "value",
              "confidence"}] sorted top-to-bottom.
    """
    if ocr is None:
        from core.ocr_fallback import FallbackOCR
        ocr = FallbackOCR()

    width = image.width
    excluded = [
        r["bbox"] for r in (regions or [])
        if r["block_type"] in MARKER_EXCLUDE_BLOCK_TYPES
    ]

    markers = []
    for (x, y, w, h) in ocr.detect_regions(image):
        # Cheap geometric filter first: markers are short and left-aligned.
        if x > width * MARKER_LEFT_BAND_RATIO or w > width * MARKER_MAX_WIDTH_RATIO:
            continue
        centre_x, centre_y = x + w // 2, y + h // 2
        if any(_contains_point(bbox, centre_x, centre_y) for bbox in excluded):
            continue

        result = ocr.recognize(image.crop((x, y, x + w, y + h)))
        parsed = _normalize_marker(result.text or "")
        if parsed is None:
            continue
        confidence = result.confidence if result.confidence is not None else 0.0
        kind, value = parsed
        markers.append({
            "page_number": page_number,
            "bbox": [x, y, w, h],
            "raw_text": result.text,
            "kind": kind,
            "value": value,
            "confidence": round(float(confidence), 4),
            "ocr_engine": result.engine,
        })

    markers.sort(key=lambda m: (m["bbox"][1], m["bbox"][0]))
    return markers


def assign_regions(regions: list[dict], markers: list[dict]) -> list[dict]:
    """Attach each region to the most recent preceding question marker.

    Both lists span the WHOLE booklet and carry page_number, because a marker
    on page 2 owns the regions that continue onto page 3 — assignment is not
    a per-page operation.

    Rules, in order:
      * A region coincident with a marker's own box IS that marker: dropped.
      * A region is owned by the last marker at or above its top edge.
      * A sub-part marker ('a)') composes with the last numeric marker,
        giving 'Q2a'. A sub-part with no numeric marker before it is not
        guessed at — regions under it are flagged.
      * Regions before any marker are left unassigned with a reason.

    Ambiguity is FLAGGED, NEVER GUESSED (the ingestion contract): a region
    gets `assigned_question=None` plus an `unassigned_reason` rather than a
    plausible-looking wrong answer.

    Returns new region dicts with `assigned_question`, `unassigned_reason`
    and updated review flags. Input is not mutated.
    """
    ordered_markers = sorted(markers, key=lambda m: (m["page_number"], m["bbox"][1]))

    # Pre-compute each marker's resolved label and any defect it carries.
    resolved: list[dict] = []
    current_numeric = None
    for index, marker in enumerate(ordered_markers):
        defect = None
        if marker["confidence"] < MARKER_MIN_CONFIDENCE:
            defect = "low_confidence_marker"

        previous = ordered_markers[index - 1] if index else None
        if (previous
                and previous["page_number"] == marker["page_number"]
                and abs(previous["bbox"][1] - marker["bbox"][1]) < MARKER_AMBIGUITY_PX):
            defect = "ambiguous_marker"

        if marker["kind"] == "numeric":
            current_numeric = marker["value"]
            label = f"Q{marker['value']}"
        else:
            if current_numeric is None:
                # 'a)' with no question number before it anywhere.
                label, defect = None, defect or "subpart_without_question"
            else:
                label = f"Q{current_numeric}{marker['value']}"

        resolved.append({**marker, "label": label, "defect": defect})

    assigned = []
    for region in regions:
        region = {**region, "review_reasons": list(region["review_reasons"])}
        key = (region["page_number"], region["bbox"][1])

        # Is this region just a marker that the layout model also boxed?
        if any(_overlap_ratio(region["bbox"], m["bbox"]) >= MARKER_REGION_OVERLAP
               or _overlap_ratio(m["bbox"], region["bbox"]) >= MARKER_REGION_OVERLAP
               for m in resolved
               if m["page_number"] == region["page_number"]):
            continue

        owner = None
        for marker in resolved:
            marker_key = (marker["page_number"], marker["bbox"][1])
            # "At or above": a marker sharing a region's top edge (the layout
            # model merges 'a)' into the paragraph beside it) still owns it.
            if marker_key <= (key[0], key[1] + marker["bbox"][3]):
                owner = marker
            else:
                break

        if owner is None:
            region["assigned_question"] = None
            region["unassigned_reason"] = "no_preceding_marker"
            region["needs_review"] = True
            region["review_reasons"].append("no_preceding_marker")
        elif owner["defect"] or owner["label"] is None:
            region["assigned_question"] = None
            region["unassigned_reason"] = owner["defect"] or "unresolved_marker"
            region["needs_review"] = True
            region["review_reasons"].append(region["unassigned_reason"])
        else:
            region["assigned_question"] = owner["label"]
            region["unassigned_reason"] = None

        assigned.append(region)

    return assigned


def segment_booklet(
    pages: list[dict],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    stub: bool = False,
) -> dict:
    """Run classification + marker detection + assignment over a whole booklet.

    `pages` is core/booklet_ingest.py::ingest_booklet()['pages'].
    Returns {"regions": [...], "markers": [...]} with regions in booklet
    reading order and every region carrying an assigned_question or an
    unassigned_reason.
    """
    ocr = None
    if not stub:
        from core.ocr_fallback import FallbackOCR
        ocr = FallbackOCR()

    all_regions, all_markers = [], []
    for page in pages:
        number, image = page["page_number"], page["image"]
        if stub:
            regions = stub_segment(image, page_number=number)
            markers = stub_markers(page_number=number)
        else:
            regions = classify_page(image, page_number=number, min_confidence=min_confidence)
            markers = detect_question_markers(image, page_number=number,
                                              regions=regions, ocr=ocr)
        for region in regions:
            region["page_image_url"] = page.get("page_image_url")
        all_regions.extend(regions)
        all_markers.extend(markers)

    return {
        "regions": assign_regions(all_regions, all_markers),
        "markers": all_markers,
    }
