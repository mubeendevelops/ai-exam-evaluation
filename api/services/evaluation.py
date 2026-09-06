"""api/services/evaluation.py — adapter between the routers and core/.

TRANSLATION ONLY. There is no scoring, aggregation, confidence or persistence
logic in this file, and there must never be. Every number it returns was
computed by core/booklet_evaluator.py; every row it writes goes through
core/plugins/persistence.py or core/answer_evaluation.py. What lives here is
the part that is genuinely API-shaped: turning {upload_id, exam_id,
student_id} into the arguments core/booklet_evaluator.load_booklet_tasks
actually takes, and turning an evaluation_results row into a response body.

If you are about to add an `if` here that changes a score, a threshold, or
which reference is used — it belongs in core/, where the CLI can reach it too.
The whole reason this repo was written as scripts over core/ is that both
front doors call the same functions (CLAUDE_CONTEXT.md §11 rule 1).

WHY "A BOOKLET" IS ADDRESSED AS (student_id, exam_id, source_scan_url):
there is no booklets table and no exams.paper_id column (§7C's open schema
gap), so core/booklet_evaluator.load_booklet_tasks identifies a booklet the
only way the data allows — the answers one student wrote for one exam,
optionally narrowed to one ingestion by source_scan_url. The API's upload_id
resolves to exactly that source_scan_url (booklet_uploads.blob_url), which is
what makes {upload_id, exam_id, student_id} a well-formed request rather than
three loosely related ids.
"""
from __future__ import annotations

from typing import Any

import core.answer_evaluation
import core.booklet_evaluator
import core.jobs
import core.pagination
import core.results
import core.uploads

#: evaluation_jobs.job_type for a booklet evaluation.
JOB_TYPE_BOOKLET_EVAL = "booklet_eval"


class UploadNotFoundError(LookupError):
    """The upload_id does not resolve for this college.

    Its own type so the router can answer 404 without having to guess whether
    a None meant "missing", "another tenant's", or "no RLS context" — the
    three causes zero rows has on an RLS-protected table (§6).
    """


class AnswerNotFoundError(LookupError):
    """The answer_id does not resolve for this college. Same reasoning."""


# ─────────────────────────────── enqueue ────────────────────────────────────

def enqueue_booklet_evaluation(
    cur,
    *,
    college_id,
    upload_id,
    exam_id,
    student_id,
    stub: bool = False,
    stub_extraction: bool = False,
    stub_llm: bool = False,
    method: str | None = None,
) -> dict[str, Any]:
    """Resolves the upload, then queues one booklet_eval job for it.

    The upload is resolved FIRST, and tenant-scoped, for two reasons. It is
    the only integrity check available — migration 015 deliberately puts no FK
    from evaluation_jobs.payload's upload_id to booklet_uploads, because
    payload is generic across job types — so without this a client could queue
    a job against any uuid and get a failure minutes later from a worker
    instead of a 404 now. And it is what turns upload_id into the
    source_scan_url the evaluator needs; a job carrying only an unresolved id
    would make the worker do this lookup, where a wrong tenant has nobody left
    to catch it.
    """
    upload = core.uploads.get_upload(cur, upload_id=upload_id, college_id=college_id)
    if upload is None:
        raise UploadNotFoundError(
            f"No upload {upload_id} for this college. Either the id is wrong, it "
            f"belongs to another college, or this transaction has no RLS context "
            f"(booklet_uploads fails closed and silently — CLAUDE_CONTEXT.md §6)."
        )

    payload = {
        "upload_id": str(upload_id),
        "exam_id": str(exam_id),
        "student_id": str(student_id),
        # Resolved here so the worker never has to re-derive the tenant-scoped
        # lookup, and so the job record shows exactly which stored scan it was
        # pointed at even if the upload row is later removed.
        "source_scan_url": upload["blob_url"],
        "options": {
            "stub": stub,
            "stub_extraction": stub_extraction,
            "stub_llm": stub_llm,
            "method": method,
        },
    }

    return core.jobs.enqueue_job(
        cur,
        college_id=college_id,
        job_type=JOB_TYPE_BOOKLET_EVAL,
        payload=payload,
    )


# ────────────────────────────── run (worker) ────────────────────────────────

def run_booklet_evaluation(conn, payload: dict, *, on_progress=None,
                           persist: bool = True, dry_run: bool = False) -> dict:
    """Executes one booklet_eval job. Called by scripts/run_job_worker.py.

    This is the whole of the "wiring" — load the persisted regions, hand them
    to core/booklet_evaluator.evaluate_booklet, optionally append the results.
    Every decision about routing, merging, confidence and partial failure is
    made inside that module (§7D's four decisions); none of them is re-made
    here.

    NOTE — INGESTION IS NOT PART OF THIS PATH, and that is deliberate. The
    regions must already exist as answer_blocks rows, put there by
    scripts/ingest_booklet.py. §7C and §7D are separate passes precisely so
    segmentation can be verified before anything scores on top of it, and
    re-running ingestion inside an evaluation job would rewrite a student's
    blocks every time their booklet is re-scored. A booklet with no persisted
    regions therefore FAILS with a message naming the missing step, rather
    than succeeding with an empty report that reads like a zero.

    on_progress(stage, percent, message, **counts) is called between phases so
    the worker can write evaluation_jobs.progress. Progress is coarse by
    necessity: evaluate_booklet runs extraction and evaluation as bounded
    thread pools with no progress callback, so within-stage completion is
    genuinely unknown. Reporting a stage is honest; interpolating a percentage
    inside one would not be.
    """
    options = payload.get("options") or {}
    report_progress = on_progress or (lambda *a, **k: None)

    report_progress("loading", 0.05, "Loading this booklet's persisted regions.")
    with conn.cursor() as cur:
        tasks = core.booklet_evaluator.load_booklet_tasks(
            cur,
            student_id=payload["student_id"],
            exam_id=payload["exam_id"],
            source_scan_url=payload.get("source_scan_url"),
        )

    if not tasks:
        raise NoRegionsError(
            f"No answer_blocks rows found for student {payload['student_id']} / "
            f"exam {payload['exam_id']} / scan {payload.get('source_scan_url')!r}. "
            f"The booklet has to be INGESTED before it can be evaluated — run "
            f"scripts/ingest_booklet.py (CLAUDE_CONTEXT.md §7C); the API does not "
            f"ingest as part of evaluation, deliberately. Note also that "
            f"answer_blocks is RLS-protected and fails closed SILENTLY (§6), so "
            f"a missing tenant context looks exactly like this."
        )

    report_progress("evaluating", 0.25,
                    f"Scoring {len(tasks)} regions.", regions=len(tasks))
    report = core.booklet_evaluator.evaluate_booklet(
        tasks,
        stub=bool(options.get("stub")),
        stub_extraction=bool(options.get("stub_extraction")),
        stub_llm=bool(options.get("stub_llm")),
        method=options.get("method"),
        booklet={
            "upload_id": payload.get("upload_id"),
            "student_id": payload["student_id"],
            "exam_id": payload["exam_id"],
            "source_scan_url": payload.get("source_scan_url"),
        },
    )

    questions = len(report.get("questions", []))
    if persist:
        report_progress("persisting", 0.85,
                        f"Appending {questions} question results to the ledger.",
                        regions=len(tasks), questions=questions)
        # APPEND-ONLY. persist_question_results -> write_evaluation_result ->
        # record_evaluation, which flips the previous row's is_current and
        # INSERTs. Nothing in this path UPDATEs a score (rule 2).
        core.booklet_evaluator.persist_question_results(conn, report, dry_run=dry_run)

    report_progress("done", 1.0, "Finished.",
                    regions=len(tasks), questions=questions)
    return report


class NoRegionsError(RuntimeError):
    """A booklet_eval job whose booklet has no persisted regions.

    A distinct type because the fix is a specific missing step (ingestion),
    not a retry — a worker that retried this would fail identically forever.
    """


# ──────────────────────────────── list results ──────────────────────────────

def list_results(
    cur, *, college_id, exam_id=None, student_id=None, status: str | None = None,
    needs_review: bool | None = None, limit: int = 50, offset: int = 0,
) -> tuple[list[dict], int]:
    """A page of answer summaries plus the matching total. Straight through
    to core/results.py, which owns the SQL — same shape as
    api/services/questions.py::list_bank."""
    rows = core.results.list_results(
        cur, college_id=college_id, exam_id=exam_id, student_id=student_id,
        status=status, needs_review=needs_review, limit=limit, offset=offset,
    )
    total = core.results.count_results(
        cur, college_id=college_id, exam_id=exam_id, student_id=student_id,
        status=status, needs_review=needs_review,
    )
    return rows, total


# ─────────────────────────────── read results ───────────────────────────────

def get_answer_report(cur, *, answer_id, college_id) -> dict[str, Any]:
    """The full evaluation report for one answer, tenant-scoped.

    Reads the CURRENT ledger row (is_current) plus the answer's own context
    and its review history. The per-signal breakdown
    (semantic/keyword/llm/rubric), the per-region components and the
    confidence flags all come out of evaluation_results.metrics, exactly as
    core/booklet_evaluator._ledger_result put them there — this function
    reshapes, it does not recompute.

    Every query below carries `AND a.college_id = %s`. See core/jobs.py's
    header: the RLS policies only run for a non-superuser role (migration
    016), and these predicates are what isolate tenants regardless of which
    role PGUSER names.
    """
    cur.execute(
        """
        SELECT a.answer_id, a.question_id, a.student_id, a.exam_id, a.status,
               a.source_scan_url, a.submitted_at, q.content, q.marks_max
          FROM answers   a
          JOIN questions q ON q.question_id = a.question_id
         WHERE a.answer_id = %s AND a.college_id = %s
        """,
        (str(answer_id), str(college_id)),
    )
    row = cur.fetchone()
    if row is None:
        raise AnswerNotFoundError(
            f"No answer {answer_id} for this college. Either the id is wrong, it "
            f"belongs to another college, or this transaction has no RLS context "
            f"(answers fails closed and silently — CLAUDE_CONTEXT.md §6)."
        )

    (answer_id_, question_id, student_id, exam_id, status, source_scan_url,
     submitted_at, question_content, marks_max) = row

    cur.execute(
        """
        SELECT er.evaluation_id, er.score, er.explanation, er.evaluator_type,
               er.evaluator_model, er.metrics, er.evaluated_at,
               er.reference_answer_variant_id, er.reference_asset_id
          FROM evaluation_results er
          JOIN answers a ON a.answer_id = er.answer_id
         WHERE er.answer_id = %s AND a.college_id = %s AND er.is_current
         ORDER BY er.evaluated_at DESC
         LIMIT 1
        """,
        (str(answer_id), str(college_id)),
    )
    current = cur.fetchone()

    # The ledger history, newest first, CAPPED at NESTED_MAX rows — a
    # question re-scored often enough (a corrected reference, a model
    # upgrade) must not make this response grow without bound.
    # evaluation_results is append-only (rule 2), so this is a real audit
    # trail rather than a changelog someone maintains; the cap does not
    # discard the older rows, it just stops returning ALL of them in one
    # response. `total` is a real COUNT, not len(items) — see
    # core/pagination.py's docstring on why that has to be a separate query
    # once the SELECT itself is LIMIT-ed.
    cur.execute(
        """
        SELECT COUNT(*)
          FROM evaluation_results er
          JOIN answers a ON a.answer_id = er.answer_id
         WHERE er.answer_id = %s AND a.college_id = %s
        """,
        (str(answer_id), str(college_id)),
    )
    (history_total,) = cur.fetchone()

    cur.execute(
        """
        SELECT er.evaluation_id, er.score, er.evaluator_type, er.evaluator_model,
               er.is_current, er.evaluated_at
          FROM evaluation_results er
          JOIN answers a ON a.answer_id = er.answer_id
         WHERE er.answer_id = %s AND a.college_id = %s
         ORDER BY er.evaluated_at DESC, er.evaluation_id DESC
         LIMIT %s
        """,
        (str(answer_id), str(college_id), core.pagination.NESTED_MAX),
    )
    history = {
        "items": [
            {
                "evaluation_id": str(e_id),
                "score": float(score),
                "evaluator_type": e_type,
                "evaluator_model": e_model,
                "is_current": is_current,
                "evaluated_at": at,
            }
            for e_id, score, e_type, e_model, is_current, at in cur.fetchall()
        ],
        "total": int(history_total),
    }
    history["truncated"] = len(history["items"]) < history["total"]

    cur.execute(
        """
        SELECT COUNT(*)
          FROM answer_reviews ar
          JOIN answers a ON a.answer_id = ar.answer_id
         WHERE ar.answer_id = %s AND a.college_id = %s
        """,
        (str(answer_id), str(college_id)),
    )
    (reviews_total,) = cur.fetchone()

    cur.execute(
        """
        SELECT ar.review_id, ar.reviewer_id, ar.action, ar.final_marks,
               ar.comment, ar.reviewed_at
          FROM answer_reviews ar
          JOIN answers a ON a.answer_id = ar.answer_id
         WHERE ar.answer_id = %s AND a.college_id = %s
         ORDER BY ar.reviewed_at DESC, ar.review_id DESC
         LIMIT %s
        """,
        (str(answer_id), str(college_id), core.pagination.NESTED_MAX),
    )
    reviews_rows = [
        {
            "review_id": str(r_id),
            "reviewer_id": str(rev_id),
            "action": action,
            "final_marks": float(marks) if marks is not None else None,
            "comment": comment,
            "reviewed_at": at,
        }
        for r_id, rev_id, action, marks, comment, at in cur.fetchall()
    ]
    reviews = {
        "items": reviews_rows,
        "total": int(reviews_total),
        "truncated": len(reviews_rows) < int(reviews_total),
    }

    metrics = (current[5] if current else None) or {}

    # _final_marks reads the whole review log newest-first to find the most
    # recent one with an explicit mark. Capping `reviews` above does not
    # affect this: a review old enough to fall outside NESTED_MAX has
    # necessarily been superseded by every review still inside it, so the
    # newest-with-marks search over the FULL set and over the capped `items`
    # agree — but it is computed here over `reviews_rows` before any
    # truncation happens to be reused, not re-derived from the capped list.
    return {
        "answer_id": str(answer_id_),
        "question_id": str(question_id),
        "question_text": question_content,
        "student_id": str(student_id),
        "exam_id": str(exam_id),
        "status": status,
        "source_scan_url": source_scan_url,
        "submitted_at": submitted_at,
        "marks_max": float(marks_max) if marks_max is not None else None,
        "evaluation": _evaluation_block(current, metrics) if current else None,
        "signals": _signals(metrics),
        "components": core.pagination.cap(metrics.get("components") or []),
        "flags": metrics.get("flags") or [],
        "failures": metrics.get("failures") or [],
        "confidence": _confidence(metrics),
        "history": history,
        "reviews": reviews,
        # The mark that actually stands: a teacher's override wins over the AI
        # score. Computed here rather than stored, because storing it would
        # mean UPDATEing something whenever a review arrives, and both tables
        # this reads are append-only by design.
        "final_marks": _final_marks(current, reviews_rows),
    }


def _evaluation_block(current, metrics: dict) -> dict:
    (evaluation_id, score, explanation, evaluator_type, evaluator_model,
     _metrics, evaluated_at, variant_id, asset_id) = current
    return {
        "evaluation_id": str(evaluation_id),
        "score": float(score),
        "explanation": explanation,
        "evaluator_type": evaluator_type,
        "evaluator_model": evaluator_model,
        "evaluated_at": evaluated_at,
        "plugin": metrics.get("plugin"),
        "plugin_version": metrics.get("plugin_version"),
        # Exactly one of these is set — migration 012's polymorphic reference.
        "reference_answer_variant_id": str(variant_id) if variant_id else None,
        "reference_asset_id": str(asset_id) if asset_id else None,
    }


def _signals(metrics: dict) -> dict | None:
    """The text plugin's per-signal breakdown (semantic / keyword / llm /
    rubric), pulled up from wherever in the metrics tree it ended up.

    A booklet result nests it under the winning component, because the
    aggregate row is written by core/booklet_evaluator rather than by the text
    plugin (metrics["plugin"] names the aggregator — see _ledger_result). A
    single-answer result from scripts/evaluate_answer.py has it at the top
    level. Both are read here so a client sees one shape whichever produced
    the row; a diagram or table answer has no signals at all and gets null,
    not an empty dict pretending the signals were all zero.
    """
    if metrics.get("signals"):
        return metrics["signals"]

    for component in metrics.get("components") or []:
        signals = (component.get("signals")
                   or (component.get("metrics") or {}).get("signals"))
        if signals:
            return signals
    return None



def _confidence(metrics: dict) -> dict:
    """Evaluation confidence, plus what the regions contributed to it.

    Two shapes are being reconciled here. metrics["regions"] is a COUNTS dict
    ({total, evaluated, failed, flagged}) written by
    core/booklet_evaluator.aggregate, while the per-region identities live one
    level down, inside each component's own "regions" list. The API flattens
    them so a client gets both without having to know that layout — and they
    stay separate fields, because a count and a list of regions answer
    different questions.

    Region-level classification confidence is a DIFFERENT axis from evaluation
    confidence (migration 013's comment on classification_confidence spells
    this out): a region can be confidently a table and still be read badly, so
    the two are never merged into one number.
    """
    detail = metrics.get("confidence_detail") or {}
    counts = metrics.get("regions")
    regions: list[dict] = []
    for component in metrics.get("components") or []:
        for region in component.get("regions") or []:
            regions.append(region)

    return {
        "question": detail.get("question"),
        "booklet": detail.get("booklet"),
        "region_counts": counts if isinstance(counts, dict) else {},
        "regions": regions,
    }


def _final_marks(current, reviews: list) -> dict:
    """Which mark stands, and why.

    A teacher's most recent override with an explicit final_marks wins over
    the AI score. 'flagged' reviews carry no marks (the schema makes
    final_marks nullable for exactly that case) and so do not displace it.
    """
    ai_score = float(current[1]) if current else None
    for review in reviews:                       # newest first
        if review["final_marks"] is not None:
            return {
                "marks": review["final_marks"],
                "source": "sme_override",
                "review_id": review["review_id"],
                "ai_score": ai_score,
            }
    return {"marks": ai_score, "source": "ai" if ai_score is not None else None,
            "review_id": None, "ai_score": ai_score}


# ──────────────────────────────── override ──────────────────────────────────

def record_override(
    cur,
    *,
    answer_id,
    college_id,
    reviewer_id,
    action: str,
    final_marks: float | None,
    comment: str | None,
) -> dict[str, Any]:
    """Appends one answer_reviews row and moves the answer to 'sme_reviewed'.

    ═══════════════════════════════════════════════════════════════════════
    THIS FUNCTION DOES NOT TOUCH evaluation_results. NOT AN UPDATE, NOT AN
    INSERT, NOT AN is_current FLIP.
    ═══════════════════════════════════════════════════════════════════════

    evaluation_results is an append-only ledger of what the AI computed
    (PROJECT_CONTEXT.md rule 2). A teacher disagreeing with a score is a NEW
    FACT ABOUT THE ANSWER, not a correction to what the model produced: the
    model really did output 6.5, and overwriting that erases the evidence
    needed to tell whether the model is systematically wrong. So the override
    is recorded where the schema already put human judgement —
    answer_reviews — and GET /api/v1/results/{answer_id} reconciles the two
    at read time via _final_marks().

    Writing an override into evaluation_results would ALSO be wrong on its own
    terms: every row there carries a reference FK (migration 012's XOR) and
    metrics attributing the score to a plugin version, and a human review has
    neither. It would be a row that lies about its own provenance.

    The status transition and the review row are issued on ONE cursor and are
    committed together by the caller, so an answer can never end up marked
    'sme_reviewed' with no review to show for it.

    answer_reviews.college_id is NOT passed: migration 003's
    trg_answer_reviews_derive_and_check_college derives it from the answer and
    refuses a reviewer belonging to a different college. Letting the trigger
    own it means the API cannot get tenant attribution wrong here even if it
    tries.
    """
    cur.execute(
        "SELECT status FROM answers WHERE answer_id = %s AND college_id = %s",
        (str(answer_id), str(college_id)),
    )
    row = cur.fetchone()
    if row is None:
        raise AnswerNotFoundError(
            f"No answer {answer_id} for this college — refusing to record a review "
            f"against it. (answers is RLS-protected and fails closed silently, §6.)"
        )
    current_status = row[0]

    cur.execute(
        """
        INSERT INTO answer_reviews (answer_id, reviewer_id, action, final_marks, comment)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING review_id, answer_id, reviewer_id, action, final_marks, comment, reviewed_at
        """,
        (str(answer_id), str(reviewer_id), action, final_marks, comment),
    )
    (review_id, answer_id_, reviewer_id_, action_, final_marks_, comment_,
     reviewed_at) = cur.fetchone()

    status_changed = core.answer_evaluation.transition_to_sme_reviewed(
        cur, str(answer_id), current_status, str(reviewer_id)
    )

    return {
        "review_id": str(review_id),
        "answer_id": str(answer_id_),
        "reviewer_id": str(reviewer_id_),
        "action": action_,
        "final_marks": float(final_marks_) if final_marks_ is not None else None,
        "comment": comment_,
        "reviewed_at": reviewed_at,
        "previous_status": current_status,
        "status": "sme_reviewed" if status_changed else current_status,
        "status_changed": status_changed,
    }
