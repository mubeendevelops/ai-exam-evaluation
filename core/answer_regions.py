"""core/answer_regions.py — one answer's persisted REGIONS, in reading order,
plus the page-image reference behind them.

WHY THIS EXISTS SEPARATELY FROM core/results.py
core/results.py answers "which answers match these filters" — one row per
answer, cheap to page. This module answers the question the Results screen
(UI-2B) asks about ONE answer: "which regions of which scanned pages make up
this answer, in what order, and what does each one say". That is a different
query at a different granularity, against answer_blocks rather than answers.

WHY IT IS IN core/ AND NOT api/services/
CLAUDE_CONTEXT.md §11 rule 1: query work lives in core/, so both front doors
reach it. scripts/ingest_booklet.py already prints a region report from the
segmenter's own output; a `--show` flag that wanted the PERSISTED regions
back would find this function already written, exactly as
core/question_bank.py::list_questions was written for the question endpoints.

TENANCY. Every query carries an explicit `a.college_id = %s` on top of RLS,
the same belt-and-braces core/jobs.py and core/results.py use: the policies
only run for a non-superuser role (migration 016), and these predicates
isolate tenants regardless of which role PGUSER names. A region is reached
THROUGH its answer — never by block_id alone — so another college's region is
not addressable here at all.

═══════════════════════════════════════════════════════════════════════════
WHAT IS NOT HERE, AND WHY (read before adding it)
═══════════════════════════════════════════════════════════════════════════

**The text a plugin extracted from a scanned region is not persisted.**
`answer_blocks.content` holds text that was ALREADY DIGITAL when the block was
created; core/booklet_persist.py::insert_region_block writes it as a literal
NULL for every region cut out of a scanned page, and nothing ever comes back
to fill it in (`answer_blocks` has exactly one writer and no UPDATE anywhere).
The OCR output lives in core/booklet_evaluator.py's Extraction objects for the
duration of one run and is dropped: _component_row() records a region's ids,
page, bbox and scores but not `extraction.content`, and the merged text built
in build_components() survives only as a character count.

So `text` below is populated from `answer_blocks.content` when it is there,
and is None — with `text_source` saying so — when it is not. It is NOT faked,
and nothing is reconstructed from the score. Filling that gap needs
migrations/019_answer_block_extractions.sql.proposed, which is append-only by
construction (new row + is_current flip) because overwriting a prior
extraction would settle CLAUDE_CONTEXT.md §10's open decision 2 by accident.
When 019 lands, the ONLY change here is the LEFT JOIN marked TODO(019) below
plus the two `text_source` values it adds; every caller and every response
field already carries the shape.

**Unassigned regions are not here either.** `answer_blocks.answer_id` is NOT
NULL, so a region the segmenter could not assign to a question is persisted
NOWHERE (§7C) and surfaces only in the ingestion/evaluation report's review
queue. This module reads answer_blocks; it must not grow a second, invented
home for them.
"""
from __future__ import annotations

from typing import Any

#: Reading order for one answer's regions: page first, then the segmenter's
#: within-page sequence, then block_id so the order is total (two regions can
#: legitimately share a sequence_order after a re-ingest — §7C: re-ingestion
#: DUPLICATES rather than replaces). Identical to the ORDER BY in
#: core/booklet_evaluator.py::load_booklet_tasks and to the sort key
#: build_components() merges text on, which is what makes `reading_order`
#: below the same order the scored text was concatenated in.
_READING_ORDER = "ab.page_number NULLS LAST, ab.sequence_order, ab.block_id"


def list_answer_regions(cur, *, answer_id, college_id) -> list[dict[str, Any]]:
    """Every persisted region of one answer, in reading order, tenant-scoped.

    Returns [] both for an answer with no regions and for an answer that does
    not belong to this college — the caller (which has already resolved the
    answer itself, and 404s when that fails) is what tells those apart. That
    is deliberate: a function that raised "no such answer" here would be a
    second, competing existence check against the one the report already does.
    """
    cur.execute(
        f"""
        SELECT ab.block_id, ab.block_type, ab.page_number, ab.region_bbox,
               ab.sequence_order, ab.classification_label,
               ab.classification_confidence, ab.needs_review,
               ab.content, ab.confidence_score,
               ab.page_image_url IS NOT NULL AS has_page_image,
               ab.blob_url      IS NOT NULL AS has_region_image
          -- TODO(019): LEFT JOIN answer_block_extractions abe
          --                 ON abe.block_id = ab.block_id AND abe.is_current
          --            and select abe.text / abe.ocr_confidence / abe.plugin.
          --            Nothing else in this file or above it changes.
          FROM answers       a
          JOIN answer_blocks ab ON ab.answer_id = a.answer_id
         WHERE a.answer_id = %s AND a.college_id = %s
         ORDER BY {_READING_ORDER}
        """,
        (str(answer_id), str(college_id)),
    )

    regions = []
    for index, row in enumerate(cur.fetchall()):
        (block_id, block_type, page_number, region_bbox, sequence_order,
         classification_label, classification_confidence, needs_review,
         content, confidence_score, has_page_image, has_region_image) = row
        regions.append({
            "block_id": str(block_id),
            "block_type": block_type,
            "page_number": page_number,
            "region_bbox": region_bbox,
            "sequence_order": int(sequence_order or 0),
            # Position of this region within its question's answer. The whole
            # point of returning it: §7D merges a question's text regions into
            # ONE scored answer in this order, so the UI can show which part
            # of the merged text came from which region — and where the seam
            # is when an answer spans a page break.
            "reading_order": index,
            "classification_label": classification_label,
            "classification_confidence": (float(classification_confidence)
                                          if classification_confidence is not None
                                          else None),
            "needs_review": bool(needs_review),
            # The vocabulary core/booklet_evaluator.py::load_booklet_tasks uses
            # for the same flag, so a region's reason reads identically in the
            # API and in the CLI's report.
            "ingestion_flags": ["needs_review_at_ingestion"] if needs_review else [],
            "text": content,
            "text_source": "answer_blocks.content" if content is not None else None,
            "ocr_confidence": (float(confidence_score)
                               if confidence_score is not None else None),
            "ocr_confidence_source": ("answer_blocks.confidence_score"
                                      if confidence_score is not None else None),
            "has_page_image": bool(has_page_image),
            "has_region_image": bool(has_region_image),
        })
    return regions


def get_page_image_ref(cur, *, answer_id, page_number, college_id) -> str | None:
    """The stable "bucket/key" ref of one page's stored image, or None.

    ONE None FOR EVERY REASON, on purpose: no such answer, another college's
    answer, no region on that page, or regions with no page_image_url (every
    block predating booklet ingestion — migration 013's columns are nullable)
    all return None, so the endpoint above cannot be turned into an existence
    oracle over another college's answer ids by comparing responses.

    The ref is resolved THROUGH the answer (answers -> answer_blocks), never
    from a page_image_url handed in by a caller, and it is returned as the
    stable ref it is stored as. Presigning happens at the API edge, per
    request; a presigned URL must never be stored or returned from core
    (CLAUDE_CONTEXT.md §10, migration 013's COMMENT on page_image_url).

    DISTINCT is what makes "the page image" well defined: every region cut
    from one page carries the same page_image_url (core/booklet_ingest.py
    uploads the deskewed page once), so several rows agree. If a re-ingest
    ever produced two different images for one page, the newest-first ordering
    below picks one deterministically rather than 500-ing on a second row.
    """
    cur.execute(
        """
        SELECT ab.page_image_url
          FROM answers       a
          JOIN answer_blocks ab ON ab.answer_id = a.answer_id
         WHERE a.answer_id = %s AND a.college_id = %s
           AND ab.page_number = %s AND ab.page_image_url IS NOT NULL
         ORDER BY ab.sequence_order DESC, ab.block_id DESC
         LIMIT 1
        """,
        (str(answer_id), str(college_id), int(page_number)),
    )
    row = cur.fetchone()
    return row[0] if row else None


def list_answer_pages(cur, *, answer_id, college_id) -> list[dict[str, Any]]:
    """The pages this answer has regions on, and whether each has an image.

    Lets the Results screen build its page strip from the same response that
    drives everything else, instead of probing the image endpoint per page to
    find out which ones exist.
    """
    cur.execute(
        """
        SELECT ab.page_number,
               COUNT(*)                                        AS regions,
               bool_or(ab.page_image_url IS NOT NULL)          AS has_image,
               bool_or(ab.needs_review)                        AS needs_review
          FROM answers       a
          JOIN answer_blocks ab ON ab.answer_id = a.answer_id
         WHERE a.answer_id = %s AND a.college_id = %s
           AND ab.page_number IS NOT NULL
         GROUP BY ab.page_number
         ORDER BY ab.page_number
        """,
        (str(answer_id), str(college_id)),
    )
    return [
        {"page_number": page, "regions": int(count),
         "has_image": bool(has_image), "needs_review": bool(needs_review)}
        for page, count, has_image, needs_review in cur.fetchall()
    ]


# ═════════════════════ joining regions to what was scored ═══════════════════

def attach_evaluation_detail(regions: list[dict], metrics: dict | None) -> list[dict]:
    """Annotate each region with what the evaluation did with it. PURE.

    `metrics` is evaluation_results.metrics for the CURRENT ledger row, as
    core/booklet_evaluator.py::_ledger_result wrote it. Mutates and returns
    `regions` — this is a reshape of two things the caller already has, not a
    third query.

    Adds, per region:
      question        the label the ingestion assigned it ('Q2a'), from
                      metrics["question"] — the paper slot label, which is NOT
                      derivable from the answer row alone (there is no FK from
                      exams to generated_papers, §7C).
      scored          whether this region ended up inside a scored component.
      component_index which component (index into metrics["components"]).
      order_in_component / merged_with
                      where it sat in a merged text answer, and how many
                      regions were merged with it. merged_with > 1 is the
                      page-break case §7D decision 3 exists for.
      failure         the region's own failure record, when one stage failed
                      on it — partial failure is the normal case (§7D), so a
                      region that was never scored says why instead of simply
                      being absent from the components.

    OCR CONFIDENCE, AND ITS ONE HONEST SOURCE HERE. A component row carries
    `extraction_confidence`, but for a MERGED text component that number is
    the MINIMUM across its parts (build_components, decision 4), not any one
    region's own confidence — the per-part values live in the merged
    ExtractionResult's metrics, which the text plugin does not copy into its
    EvaluationResult, so they are gone by the time anything is persisted. It
    is therefore attributed to a region ONLY when that region was the
    component's only part; otherwise the region keeps ocr_confidence=None and
    the merged minimum stays where it belongs, on the component. 019 is what
    makes this per-region for real.
    """
    metrics = metrics or {}
    question_label = metrics.get("question")
    by_block: dict[str, dict] = {}

    for index, component in enumerate(metrics.get("components") or []):
        block_ids = [str(b) for b in (component.get("block_ids") or []) if b]
        merged_with = len(block_ids) or int(component.get("merged_from") or 1)
        for position, block_id in enumerate(block_ids):
            by_block[block_id] = {
                "scored": True,
                "component_index": index,
                "order_in_component": position,
                "merged_with": merged_with,
                "component_score": component.get("score"),
                "component_confidence": component.get("confidence"),
                "component_is_primary": bool(component.get("primary")),
                "component_extraction_confidence": component.get("extraction_confidence"),
            }

    failures_by_block: dict[str, dict] = {}
    for failure in metrics.get("failures") or []:
        block_id = failure.get("block_id")
        if block_id:
            failures_by_block.setdefault(str(block_id), failure)

    for region in regions:
        block_id = region["block_id"]
        detail = by_block.get(block_id)
        region["question"] = question_label
        region["failure"] = failures_by_block.get(block_id)
        if detail is None:
            region.update({
                "scored": False, "component_index": None,
                "order_in_component": None, "merged_with": 0,
                "component_score": None, "component_confidence": None,
                "component_is_primary": False,
            })
            continue

        region.update({k: v for k, v in detail.items()
                       if k != "component_extraction_confidence"})
        if (region["ocr_confidence"] is None
                and detail["merged_with"] == 1
                and detail["component_extraction_confidence"] is not None):
            region["ocr_confidence"] = detail["component_extraction_confidence"]
            region["ocr_confidence_source"] = "component_extraction"

    return regions


def merged_text(regions: list[dict]) -> dict[str, Any]:
    """The text that was ACTUALLY SCORED, reassembled, with its seams marked.

    §7D decision 3: a question's text regions are ONE answer, concatenated in
    reading order with a blank line between them and evaluated once. The
    teacher needs to see both that merged text and which region each part of
    it came from — an answer that runs over a page break is the case where the
    two differ and where a bad merge is invisible in the score alone.

    So this returns the joined string PLUS a per-part offset/length into it,
    which is what lets the UI highlight "this paragraph came from page 2,
    region 1" without re-deriving the join client-side and getting the
    separator wrong.

    `available` is False, with a `reason`, when any part's text is missing —
    the usual case today, since scanned regions have no persisted text (see
    the module docstring). A partial merge is NOT returned: a merged answer
    missing its second page would read as a complete answer that simply says
    less, which is precisely the failure §7D's merge exists to prevent.

    The separator and the mergeable block types come from
    core/booklet_evaluator.py rather than being repeated here, so this cannot
    drift from what the evaluator actually did.
    """
    from core.booklet_evaluator import MERGEABLE_BLOCK_TYPES, TEXT_MERGE_SEPARATOR

    parts = [r for r in regions if r["block_type"] in MERGEABLE_BLOCK_TYPES]
    block_ids = [r["block_id"] for r in parts]

    if not parts:
        return {"text": None, "available": False, "separator": TEXT_MERGE_SEPARATOR,
                "block_ids": [], "parts": [],
                "reason": "this answer has no text regions to merge"}

    missing = [r["block_id"] for r in parts if not r["text"]]
    if missing:
        return {
            "text": None, "available": False, "separator": TEXT_MERGE_SEPARATOR,
            "block_ids": block_ids, "parts": [],
            "reason": (
                f"{len(missing)} of {len(parts)} text region(s) have no persisted "
                f"extraction, so the merged answer cannot be shown without "
                f"inventing the missing part. See core/answer_regions.py's "
                f"docstring and migrations/019_answer_block_extractions.sql."
            ),
        }

    text = TEXT_MERGE_SEPARATOR.join(str(r["text"]) for r in parts)
    detail, offset = [], 0
    for r in parts:
        length = len(str(r["text"]))
        detail.append({
            "block_id": r["block_id"], "page_number": r["page_number"],
            "reading_order": r["reading_order"], "offset": offset, "length": length,
        })
        offset += length + len(TEXT_MERGE_SEPARATOR)

    return {"text": text, "available": True, "separator": TEXT_MERGE_SEPARATOR,
            "block_ids": block_ids, "parts": detail, "reason": None}
