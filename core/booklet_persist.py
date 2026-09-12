"""Booklet region persistence — regions to answers/answer_blocks rows.

The DB half of booklet ingestion (Task 5). core/booklet_segmenter.py is pure
CV/OCR and knows nothing about Postgres; this module owns every write, the
same split core/table_extractor.py (pure) and core/plugins/persistence.py
(writes) already use.

RLS: the caller owns the connection and MUST set the tenant GUC as the first
statement of the transaction —
    cur.execute("SET LOCAL app.is_platform_admin = 'true'")
exactly as scripts/upload_diagram_scan.py does. Booklet ingestion is a
system-level operation, not a tenant request, so it runs as platform admin.
RLS here fails closed AND SILENTLY (CLAUDE_CONTEXT.md §6): a forgotten GUC
looks identical to "this student has no answers". Reuses
core/plugins/persistence.py::RLSVisibilityError rather than inventing a second
exception for the same failure.

RESOLVING A MARKER TO A QUESTION — and a real schema gap. Mapping the label
"Q2a" to a question_id runs
    pattern_slots.slot_label -> paper_questions.slot_id -> question_id
scoped by a paper. It must be scoped, because slot_label is unique only within
a section, not across papers. The paper cannot be derived from the answer:
`exams` has no paper_id column, so there is NO FK path from a scanned
booklet's exam to the paper it was generated from. The paper id is therefore
passed in by the caller (scripts/ingest_booklet.py --paper-id) rather than
inferred. Adding `exams.paper_id UUID NULL REFERENCES generated_papers` would
close this properly, but exam/answer modelling is on CLAUDE_CONTEXT.md §10's
list of product decisions not to settle unilaterally, so it is left open and
flagged here instead.

FLAG, NEVER GUESS. Three schema realities can each stop a region from being
persisted, and all three are reported per region rather than raised:
  * the marker resolves to no question on this paper  -> question_not_found_on_paper
  * the question exists but is not status='live'      -> question_not_live
    (trg_answers_question_must_be_live rejects the INSERT outright)
  * the region was never assigned a marker at all     -> its unassigned_reason
A region that cannot be placed is NOT written with a guessed answer_id.
answer_blocks.answer_id is NOT NULL, so there is nowhere to park an orphan
region without weakening an existing FK — they live in the report only.
"""
import json
import uuid

from core import db
from core.plugins.persistence import RLSVisibilityError, _rls_context

# block_type values that a plugin can actually score today
# (core/plugins/ registers text, table and diagram).
ROUTABLE_BLOCK_TYPES = {"text", "table", "diagram"}


def resolve_marker_to_question(cur, *, paper_id: str, marker_label: str):
    """Resolve a marker label ('Q2a') to (question_id, question_status).

    Returns None when the paper has no slot with that label. Scoped by
    paper_id via paper_questions -> paper_sections, since slot_label repeats
    across papers.
    """
    cur.execute(
        """
        SELECT pq.question_id, q.status
          FROM paper_questions  pq
          JOIN paper_sections   ps ON ps.paper_section_id = pq.paper_section_id
          JOIN pattern_slots    sl ON sl.slot_id          = pq.slot_id
          JOIN questions        q  ON q.question_id       = pq.question_id
         WHERE ps.paper_id = %s
           AND sl.slot_label = %s
        """,
        (paper_id, marker_label),
    )
    row = cur.fetchone()
    return (str(row[0]), row[1]) if row else None


def _verify_paper_exists(cur, paper_id: str) -> None:
    cur.execute("SELECT 1 FROM generated_papers WHERE paper_id = %s", (paper_id,))
    if cur.fetchone() is None:
        raise ValueError(
            f"no generated_papers row with paper_id={paper_id!r}. "
            "Create one with scripts/generate_paper.py, or pass the id of an "
            "existing paper — marker labels cannot be resolved without it."
        )


def _verify_tenant_rows(cur, *, student_id: str, exam_id: str) -> None:
    """students/exams are RLS-protected, so a wrong id and a missing GUC look
    identical. Separate the two for the caller."""
    for table, column, value in (
        ("students", "student_id", student_id),
        ("exams", "exam_id", exam_id),
    ):
        cur.execute(f"SELECT 1 FROM {table} WHERE {column} = %s", (value,))
        if cur.fetchone() is None:
            raise RLSVisibilityError(
                f"no visible {table} row with {column}={value!r}. "
                f"RLS context: {_rls_context(cur)}. Either the id is wrong or "
                f"the transaction lacks the tenant GUC (it fails closed and "
                f"silently — CLAUDE_CONTEXT.md §6)."
            )


def upsert_answer(cur, *, question_id: str, student_id: str, exam_id: str,
                  source_scan_url: str) -> str:
    """Find or create the answers row for this (question, student, exam).

    An answer is per-question, so one booklet creates several. Re-ingesting
    the same booklet reuses the existing row rather than creating a duplicate
    — nothing in this schema prevents duplicate submissions
    (CLAUDE_CONTEXT.md §10), so ingestion declines to add to the problem.

    college_id is NOT supplied: trg_answers_derive_college derives it from the
    student and force-overwrites anything passed in (003_multi_tenancy.sql).
    """
    cur.execute(
        """
        SELECT answer_id, source_scan_url FROM answers
         WHERE question_id = %s AND student_id = %s AND exam_id = %s
        """,
        (question_id, student_id, exam_id),
    )
    row = cur.fetchone()
    if row:
        answer_id, existing_scan_url = str(row[0]), row[1]
        if existing_scan_url != source_scan_url:
            # A later re-ingestion under a NEW upload must move this pointer
            # forward — otherwise it still names the FIRST scan this answer
            # ever saw, and a later evaluate keyed on the new upload's blob
            # url (api/services/evaluation.py's exact-match lookup) finds
            # nothing despite the blocks this call is about to write existing
            # right here. Blocks from the earlier scan are NOT removed (that
            # would be a data-loss decision this function has no business
            # making); only the pointer this row is looked up by moves.
            cur.execute(
                "UPDATE answers SET source_scan_url = %s WHERE answer_id = %s",
                (source_scan_url, answer_id),
            )
        return answer_id

    answer_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO answers (answer_id, question_id, student_id, exam_id,
                             source_scan_url, status)
        VALUES (%s, %s, %s, %s, %s, 'pending_evaluation')
        """,
        (answer_id, question_id, student_id, exam_id, source_scan_url),
    )
    return answer_id


def _next_sequence_order(cur, answer_id: str) -> int:
    """Same idiom as scripts/upload_diagram_scan.py, so booklet regions append
    cleanly beside any hand-attached block."""
    cur.execute(
        "SELECT COALESCE(MAX(sequence_order), 0) + 1 FROM answer_blocks WHERE answer_id = %s",
        (answer_id,),
    )
    return int(cur.fetchone()[0])


def insert_region_block(cur, *, answer_id: str, region: dict, blob_url: str | None) -> str:
    """Insert one region as an answer_blocks row. Returns block_id.

    college_id is omitted deliberately — trg_answer_blocks_derive_college
    force-derives it from the parent answer.
    """
    block_id = str(uuid.uuid4())
    cur.execute(
        """
        INSERT INTO answer_blocks (
            block_id, answer_id, block_type, blob_url, content, sequence_order,
            page_number, region_bbox, page_image_url,
            classification_label, classification_confidence, needs_review
        )
        VALUES (%s, %s, %s, %s, NULL, %s, %s, %s::jsonb, %s, %s, %s, %s)
        """,
        (
            block_id, answer_id, region["block_type"], blob_url,
            _next_sequence_order(cur, answer_id),
            region["page_number"], json.dumps(region["bbox"]),
            region.get("page_image_url"),
            region.get("layout_label"), region.get("confidence"),
            bool(region.get("needs_review")),
        ),
    )
    return block_id


def persist_regions(
    conn,
    *,
    regions: list[dict],
    paper_id: str,
    student_id: str,
    exam_id: str,
    source_scan_url: str,
    dry_run: bool = False,
) -> dict:
    """Write every placeable region as an answer_blocks row.

    `regions` come from core/booklet_segmenter.py::segment_booklet, each
    already carrying assigned_question (or None) and, when a crop was uploaded,
    a "blob_url".

    The caller must have set the RLS GUC on this connection already.

    Returns a per-region outcome list plus counts:
        {"answers": {label: answer_id}, "results": [...],
         "persisted": int, "skipped": int}
    Every skipped region carries a `skip_reason`; nothing is silently dropped.
    Commits, or rolls back when dry_run — the same contract
    core/plugins/persistence.py::write_evaluation_result offers.
    """
    cur = conn.cursor()
    results, answers, resolution_cache = [], {}, {}
    persisted = skipped = 0

    try:
        _verify_paper_exists(cur, paper_id)
        _verify_tenant_rows(cur, student_id=student_id, exam_id=exam_id)

        for region in regions:
            label = region.get("assigned_question")
            outcome = {
                "page_number": region["page_number"],
                "bbox": region["bbox"],
                "block_type": region["block_type"],
                "confidence": region.get("confidence"),
                "assigned_question": label,
                "needs_review": bool(region.get("needs_review")),
                "block_id": None,
                "skip_reason": None,
            }

            if label is None:
                outcome["skip_reason"] = region.get("unassigned_reason", "unassigned")
                skipped += 1
                results.append(outcome)
                continue

            if label not in resolution_cache:
                resolution_cache[label] = resolve_marker_to_question(
                    cur, paper_id=paper_id, marker_label=label)
            resolved = resolution_cache[label]

            if resolved is None:
                outcome["skip_reason"] = "question_not_found_on_paper"
                skipped += 1
                results.append(outcome)
                continue

            question_id, status = resolved
            if status != "live":
                # trg_answers_question_must_be_live would reject the INSERT;
                # report it per region instead of failing the whole booklet.
                outcome["skip_reason"] = f"question_not_live (status={status})"
                skipped += 1
                results.append(outcome)
                continue

            if label not in answers:
                answers[label] = upsert_answer(
                    cur, question_id=question_id, student_id=student_id,
                    exam_id=exam_id, source_scan_url=source_scan_url)

            outcome["answer_id"] = answers[label]
            outcome["block_id"] = insert_region_block(
                cur, answer_id=answers[label], region=region,
                blob_url=region.get("blob_url"))
            if region["block_type"] not in ROUTABLE_BLOCK_TYPES:
                outcome["needs_review"] = True
            persisted += 1
            results.append(outcome)

        db.end_transaction(conn, dry_run=dry_run)
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {
        "answers": answers,
        "results": results,
        "persisted": persisted,
        "skipped": skipped,
    }
