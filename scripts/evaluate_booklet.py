#!/usr/bin/env python3
"""
scripts/evaluate_booklet.py — one answer booklet in, a per-question evaluation
report out. The orchestration + aggregation half of the full-booklet pipeline
(Task 5); scripts/ingest_booklet.py is the half that produces the regions this
one scores.

BEHAVIOUR:
  1. Gets the booklet's regions, either by RE-RUNNING ingestion on a PDF
     (--pdf) or by reading the answer_blocks rows a previous ingestion
     persisted (--student-id/--exam-id, or --answer-id).
  2. Loads each question's reference — the current reference_answer_variants
     row for text, the linked content_assets row for a table or diagram —
     and its marks_max and keywords. A question with no usable reference
     becomes an explicit failure in the report, not a silent omission.
  3. Routes every region through core/plugins/registry.py, extracts, then
     evaluates, under a bounded thread pool. Groq calls are paced by
     core/llm.py's token bucket and retried with jittered backoff on 429s.
  4. Rolls the results up: per-region -> per-question -> per-page -> booklet
     total, with confidence propagated by weakest links
     (core/booklet_evaluator.py's module docstring has the full rules).
  5. Appends ONE evaluation_results row per question — not per region, since
     the ledger is keyed by answer and per-region rows would overwrite each
     other. Every region's own score survives inside metrics["components"].
  6. Writes the report as JSON to STDOUT (and to --output, if given), and a
     human summary to STDERR — so `evaluate_booklet.py ... > report.json`
     gives a clean JSON file and a readable terminal at the same time.

PARTIAL FAILURE IS EXPECTED, NOT FATAL. One region failing to extract, one
question with no reference answer, one LLM call exhausting its retries: each
is recorded with its stage and reported, and the other nineteen questions are
still scored. Nothing aborts a booklet except being unable to reach the
booklet at all.

WHAT "A BOOKLET ID" MEANS HERE. There is no booklets table and no
exams.paper_id column (CLAUDE_CONTEXT.md §7C's open schema gap), so a booklet
is addressed the only way the schema allows: one student's answers for one
exam, optionally narrowed by --source-scan-url when the same booklet has been
ingested more than once. --paper-id is optional and only cosmetic here — it
recovers the 'Q1'/'Q2a' slot labels for the report, since answer_blocks does
not record which marker put a region where.

Usage:
    # Fully offline demo — no DB, no network, no Groq quota. Real segmentation
    # of a real PDF, stub extraction and stub scoring against synthetic
    # references. This is the end-to-end smoke test.
    python3 scripts/evaluate_booklet.py --pdf media/booklets/sample_booklet.pdf \\
        --offline --stub --skip-denoise

    # Score a booklet already ingested by scripts/ingest_booklet.py:
    python3 scripts/evaluate_booklet.py \\
        --student-id <uuid> --exam-id <uuid> --paper-id <uuid> \\
        --stub-llm --dry-run

    # Ingest and score in one pass (ingestion commits; --dry-run is rejected
    # here, because rolling ingestion back would leave nothing to score):
    python3 scripts/evaluate_booklet.py --pdf booklet.pdf \\
        --student-id <uuid> --exam-id <uuid> --paper-id <uuid> --storage minio

    # Real scoring, gentler on the free tier:
    python3 scripts/evaluate_booklet.py --student-id <uuid> --exam-id <uuid> \\
        --max-concurrency 2 --output report.json
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import booklet_evaluator, booklet_ingest, booklet_segmenter  # noqa: E402
from core import db as db_mod                                          # noqa: E402
from core.plugins.persistence import RLSVisibilityError                # noqa: E402
from core.plugins.text_extraction import parse_weights                 # noqa: E402

#: Placeholder reference text used by --offline, where there is no database
#: to load a real reference answer from. Prefixed and flagged everywhere it
#: appears, the same way core/diagram_extractor.stub_extract() prefixes its
#: fake labels: a score computed against this is a WIRING check, never a
#: measurement of a student's answer.
OFFLINE_REFERENCE_TEXT = (
    "[OFFLINE] No reference answer was loaded — this run has no database "
    "connection. Any score below reflects the pipeline running end to end, "
    "not the correctness of the answer."
)
OFFLINE_REFERENCE_FLAG = "offline_synthetic_reference"
DEFAULT_OFFLINE_MARKS = 10.0


def _offline_reference(block_type: str, marks_max: float):
    """Build a synthetic reference for --offline runs. Returns (reference,
    kind) or (None, error) for a block type nothing can score.

    Tables and diagrams reuse each extractor's own stub graph/grid as the
    reference, so the comparison code runs for real over a real shape; text
    uses the placeholder above. Everything produced here carries
    reference_kind='synthetic', which core/booklet_evaluator.py's persistence
    step refuses to write to the ledger — a fabricated reference must never
    reach an append-only score history.
    """
    if block_type == "text":
        from core.plugins.text_extraction import TextReference
        return TextReference(reference_text=OFFLINE_REFERENCE_TEXT,
                             marks_max=marks_max, keywords=[]), None
    if block_type == "table":
        from core import table_extractor
        from core.plugins.table_extraction import TableReference
        return TableReference(table=table_extractor.stub_extract(None),
                              marks_max=marks_max), None
    if block_type == "diagram":
        from core import diagram_extractor
        from core.plugins.diagram_evaluation import DiagramReference
        return DiagramReference(graph=diagram_extractor.stub_extract(""),
                                marks_max=marks_max, glossary_terms=[]), None
    return None, (f"block_type={block_type!r} has no offline reference and no "
                  f"plugin registers for it")


def build_offline_tasks(regions: list, pages_by_number: dict, *,
                        marks_max: float = DEFAULT_OFFLINE_MARKS) -> tuple[list, list]:
    """Turn segmenter regions straight into BlockTasks, with no DB at all.

    Returns (tasks, unassigned_regions). The region's crop from the in-memory
    page image is the source, so real extraction still works in this mode if
    --stub-extraction is not passed — the page images are already in memory
    from ingestion, and re-fetching them from storage would be pure latency
    (core/booklet_ingest.ingest_booklet's docstring makes the same argument).
    """
    tasks, unassigned = [], []
    counters: dict = {}

    for region in regions:
        label = region.get("assigned_question")
        if not label:
            unassigned.append(region)
            continue

        page_number = region["page_number"]
        counters[page_number] = counters.get(page_number, 0) + 1
        reference, error = _offline_reference(region["block_type"], marks_max)

        source = None
        image = pages_by_number.get(page_number)
        if image is not None:
            x, y, w, h = region["bbox"]
            source = image.crop((x, y, x + w, y + h))

        tasks.append(booklet_evaluator.BlockTask(
            question_label=label,
            block_type=region["block_type"],
            source=source,
            reference=reference,
            reference_kind="synthetic" if reference is not None else None,
            reference_error=error,
            marks_max=marks_max,
            page_number=page_number,
            bbox=region["bbox"],
            sequence_order=counters[page_number],
            classification_label=region.get("layout_label"),
            classification_confidence=region.get("confidence"),
            # Everything in this mode is review-worthy by construction: the
            # reference is synthetic. Saying so on every region is what keeps
            # an offline demo from reading like a real result.
            needs_review=True,
            review_reasons=list(region.get("review_reasons") or []) + [OFFLINE_REFERENCE_FLAG],
        ))

    return tasks, unassigned


def _segment(pdf_path, *, storage_mode: str, dpi: int, skip_denoise: bool,
             min_confidence: float, stub_segmentation: bool) -> tuple[list, dict, dict]:
    """Rasterize + segment one PDF, returning (regions, pages_by_number, meta).
    Calls core/ modules directly rather than scripts/ingest_booklet.py, which
    does not hand back the in-memory page images this path needs to crop."""
    ingested = booklet_ingest.ingest_booklet(pdf_path, storage_mode=storage_mode,
                                             dpi=dpi, skip_denoise=skip_denoise)
    segmented = booklet_segmenter.segment_booklet(
        ingested["pages"], min_confidence=min_confidence, stub=stub_segmentation)
    meta = {
        "pdf": str(pdf_path),
        "source_pdf_url": ingested["source_pdf_url"],
        "page_count": ingested["page_count"],
        "markers": len(segmented["markers"]),
        "storage": storage_mode,
    }
    return (segmented["regions"],
            {page["page_number"]: page["image"] for page in ingested["pages"]},
            meta)


def evaluate_booklet_offline(pdf_path, *, storage_mode="dummy",
                             dpi=booklet_ingest.DEFAULT_DPI, skip_denoise=False,
                             min_confidence=booklet_segmenter.DEFAULT_MIN_CONFIDENCE,
                             stub_segmentation=False, marks_max=DEFAULT_OFFLINE_MARKS,
                             **evaluate_kwargs) -> dict:
    """Ingest + segment + score one PDF with no database whatsoever.

    Module-level rather than buried in main(), the same convention
    scripts/ingest_booklet.py and scripts/evaluate_table_answer.py follow, so
    a future API endpoint (or a test) can call it directly.
    """
    regions, pages_by_number, meta = _segment(
        pdf_path, storage_mode=storage_mode, dpi=dpi, skip_denoise=skip_denoise,
        min_confidence=min_confidence, stub_segmentation=stub_segmentation)
    tasks, unassigned = build_offline_tasks(regions, pages_by_number, marks_max=marks_max)

    report = booklet_evaluator.evaluate_booklet(
        tasks, unassigned_regions=unassigned,
        booklet={**meta, "mode": "offline", "synthetic_references": True,
                 "default_marks_max": marks_max},
        **evaluate_kwargs,
    )
    report["persistence"] = {"written": 0, "skipped": len(report["questions"]),
                             "failed": 0, "dry_run": False,
                             "reason": "offline run — no database connection"}
    return report


def evaluate_persisted_booklet(conn, *, student_id=None, exam_id=None, answer_ids=None,
                               source_scan_url=None, paper_id=None, persist=True,
                               dry_run=False, booklet_meta=None,
                               **evaluate_kwargs) -> dict:
    """Score a booklet whose regions are already answer_blocks rows.

    Takes a CONNECTION, not a cursor, because the ledger write is delegated
    to core/plugins/persistence.write_evaluation_result(), which owns the
    commit/rollback — the same reason scripts/evaluate_table_answer.py takes
    one. The connection's transaction must already have RLS set.
    """
    cur = conn.cursor()
    try:
        tasks = booklet_evaluator.load_booklet_tasks(
            cur, student_id=student_id, exam_id=exam_id, answer_ids=answer_ids,
            source_scan_url=source_scan_url, paper_id=paper_id)
    finally:
        cur.close()

    if not tasks:
        raise RLSVisibilityError(
            "no answer_blocks rows matched this booklet. answer_blocks is RLS-protected "
            "and fails closed SILENTLY, so this is EITHER a booklet that was never "
            "ingested (run scripts/ingest_booklet.py first) OR a transaction with no "
            "tenant context set (CLAUDE_CONTEXT.md §6)."
        )

    # migrations/019_answer_block_extractions.sql: written the moment each
    # region's raw text exists (evaluate_booklet's `on_extracted` hook, right
    # after extraction and before text regions are merged for scoring — see
    # core/booklet_evaluator.persist_extractions()'s docstring). Gated by
    # `persist`/`dry_run` exactly like the ledger write below.
    extraction_persistence: dict = {}

    def _persist_extractions(extractions: list) -> None:
        extraction_persistence.update(
            booklet_evaluator.persist_extractions(conn, extractions, dry_run=dry_run)
        )

    report = booklet_evaluator.evaluate_booklet(
        tasks,
        booklet={**(booklet_meta or {}), "mode": "persisted",
                 "student_id": student_id, "exam_id": exam_id, "paper_id": paper_id,
                 "source_scan_url": source_scan_url},
        on_extracted=_persist_extractions if persist else None,
        **evaluate_kwargs,
    )
    report["extraction_persistence"] = extraction_persistence or {
        "written": 0, "skipped": len(tasks), "failed": 0, "dry_run": dry_run,
        "reason": "--no-persist" if not persist else None,
    }

    if persist:
        # persist_question_results() re-issues app.is_platform_admin itself,
        # per question — see its own docstring for why relying on main()'s
        # single upfront SET LOCAL isn't enough once persist_extractions()
        # above has already committed (or rolled back) at least once.
        booklet_evaluator.persist_question_results(conn, report, dry_run=dry_run)
    else:
        report["persistence"] = {"written": 0, "skipped": len(report["questions"]),
                                 "failed": 0, "dry_run": dry_run,
                                 "reason": "--no-persist"}
    return report


# --- terminal styling -----------------------------------------------------
# Bare ANSI codes, no dependency — disabled outright when stderr isn't a
# terminal (piped to a file/CI log) so redirected output stays clean text.
_ANSI = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m",
         "red": "\033[31m", "green": "\033[32m", "yellow": "\033[33m",
         "cyan": "\033[36m", "gray": "\033[90m"}


def _colorer(stream):
    enabled = hasattr(stream, "isatty") and stream.isatty()

    def c(text, *styles):
        if not enabled:
            return str(text)
        prefix = "".join(_ANSI[s] for s in styles)
        return f"{prefix}{text}{_ANSI['reset']}"
    return c


def _score_style(fraction: float | None) -> tuple:
    """Color for a 0..1 fraction — good/borderline/poor, same cutoffs the
    review queue already uses conceptually (nothing scientific, just legible)."""
    if fraction is None:
        return ("dim",)
    if fraction >= 0.7:
        return ("green",)
    if fraction >= 0.4:
        return ("yellow",)
    return ("red",)


#: Verdict -> one-glyph, colorable status used by both the per-question table
#: and the match/mismatch summary, so a reader learns one vocabulary.
_VERDICT_GLYPH = {
    "matched": ("✓", ("green",)), "exact": ("✓", ("green",)),
    "fuzzy": ("~", ("yellow",)), "numeric_close": ("~", ("yellow",)),
    "missing": ("✗", ("red",)), "miss": ("✗", ("red",)),
    "not_extracted": ("?", ("dim",)), "extra": ("+", ("cyan",)),
    "wrong": ("✗", ("red",)),
}


def _glyph(status: str, c) -> str:
    symbol, styles = _VERDICT_GLYPH.get(status, ("?", ("dim",)))
    return c(symbol, *styles)


def _print_summary(report: dict, stream=sys.stderr) -> None:
    """Human-readable summary, on STDERR so stdout stays pure JSON."""
    c = _colorer(stream)

    def out(text=""):
        print(text, file=stream)

    def rule(char="─", width=72):
        out(c(char * width, "gray"))

    totals, confidence = report["totals"], report["confidence"]
    booklet = report.get("booklet", {})
    title = booklet.get('pdf') or booklet.get('source_scan_url') or booklet.get('mode', '')
    out(c(f"  Booklet evaluation — {title}", "bold", "cyan"))
    rule("═")
    if booklet.get("synthetic_references"):
        out(c("  !! SYNTHETIC REFERENCES (--offline): scores below check the "
              "pipeline, not the answers.", "yellow", "bold"))

    pct = totals["percentage"]
    score_text = f"{totals['score']}/{totals['max_score']}" + (f"  ({pct * 100:.1f}%)" if pct is not None else "")
    out(f"  {'Score':<12}{c(score_text, 'bold', *_score_style(pct))}")
    out(f"  {'Questions':<12}{totals['questions_scored']}/{totals['questions_total']} scored, "
        + c(f"{totals['questions_needing_review']} need review",
            "yellow" if totals["questions_needing_review"] else "dim"))
    out(f"  {'Regions':<12}{totals['regions_evaluated']}/{totals['regions_routable']} evaluated, "
        + c(f"{totals['regions_failed']} failed", "red" if totals["regions_failed"] else "dim")
        + f", {totals['regions_unassigned']} unassigned")
    out(f"  {'Confidence':<12}{c(confidence['booklet'], *_score_style(confidence['booklet']))}"
        f"  (weakest {confidence['weakest']} on {confidence['weakest_question']})")
    out()

    rule()
    out(c("  QUESTIONS", "bold"))
    rule()
    col = f"  {'question':<10} {'score':>12}  {'conf':>5}  {'pages':<8} {'plugin':<20} flags"
    out(c(col, "dim"))
    for row in report["questions"]:
        pct_row = row["score"] / row["marks_max"] if row["scored"] and row["marks_max"] else None
        score = (f"{row['score']}/{row['marks_max']}" if row["scored"] else f"—/{row['marks_max']}")
        plugin = row["primary"]["plugin"] if row["primary"] else "-"
        pages = ",".join(str(p) for p in row["pages"]) or "-"
        flags = c(", ".join(row["flags"]), "yellow") if row["flags"] else c("-", "dim")
        score_cell = c(f"{score:>12}", *_score_style(pct_row))
        conf_cell = c(f"{row['confidence']:>5.2f}", *_score_style(row['confidence']))
        out(f"  {row['question']:<10} {score_cell}  {conf_cell}  "
            f"{pages:<8} {plugin:<20} {flags}")

        for component in row["components"]:
            signals = component.get("signals")
            if not signals:
                continue
            for name, signal in signals.items():
                if signal["score"] is None:
                    out(c(f"      [{name:<8}] unavailable — {signal['explanation']}", "dim"))
                else:
                    weight = f" w={signal['weight']}" if signal["weight"] is not None else ""
                    frac = signal["score"] / component["max_score"] if component["max_score"] else None
                    signal_score = c(f"{signal['score']}/{component['max_score']}", *_score_style(frac))
                    out(f"      [{name:<8}] {signal_score}{weight} — {c(signal['model'], 'dim')}")
    out()

    rule()
    out(c("  PAGES", "bold"))
    rule()
    for page in report["pages"]:
        failed = c(f"{page['failed']} failed", "red" if page["failed"] else "dim")
        out(f"  page {page['page_number']}: {page['regions']} region(s), "
            f"{page['evaluated']} evaluated, {failed}, "
            f"{page['unassigned']} unassigned  "
            f"questions: {', '.join(page['questions']) or '-'}"
            + (f"  (spanning: {', '.join(page['spanning_questions'])})"
               if page["spanning_questions"] else ""))
    out()

    queue = report["review_queue"]
    rule()
    out(c(f"  NEEDS TEACHER REVIEW ({len(queue)})", "bold", "yellow" if queue else "bold"))
    rule()
    for item in queue:
        confidence_str = ("  -" if item["confidence"] is None else f"{item['confidence']:.2f}")
        out(f"  [{item['kind']:<18}] {str(item['question'] or '(unassigned)'):<10} "
            f"conf {confidence_str}  {c(', '.join(item['reasons']), 'yellow')}")
    if not queue:
        out(c("  (none)", "dim"))
    out()

    failures = report["failures"]
    rule()
    out(c(f"  FAILURES ({len(failures)})", "bold", "red" if failures else "bold"))
    rule()
    for failure in failures:
        out(f"  [{failure['stage']:<9}] {str(failure.get('question')):<10} "
            f"{c(failure['error_type'], 'red')}: {failure['message'][:120]}")
    if not failures:
        out(c("  (none)", "dim"))
    out()

    throttle = report["rate_limit"]["throttle"]
    if not throttle.get("disabled"):
        out(c(f"  Groq throttle: {throttle['acquired']} request(s) paced at "
              f"{throttle['rate_per_minute']}/min, {throttle['total_wait_seconds']}s spent "
              f"waiting; retries {report['rate_limit']['retries']}", "dim"))

    persistence = report.get("persistence", {})
    out(c(f"  Persistence: {persistence.get('written', 0)} written, "
          f"{persistence.get('skipped', 0)} skipped, {persistence.get('failed', 0)} failed"
          + (f" — {persistence['reason']}" if persistence.get("reason") else "")
          + ("  [dry-run: rolled back]" if persistence.get("dry_run") else ""), "dim"))
    out()

    _print_match_summary(report, stream)


def _print_match_summary(report: dict, stream=sys.stderr) -> None:
    """Final matched/unmatched breakdown across every scored region — the
    part a teacher actually reads to see WHAT was right or wrong, as opposed
    to the score tables above which only say HOW MUCH. Walks every
    component's own plugin-specific metrics (diagram node/edge comparison,
    table cell verdicts, text keyword/rubric hits) rather than re-deriving
    any of it, so this can never disagree with the score that produced it."""
    c = _colorer(stream)

    def out(text=""):
        print(text, file=stream)

    def rule(char="─", width=72):
        out(c(char * width, "gray"))

    rule("═")
    out(c("  MATCH SUMMARY — what matched vs. what didn't", "bold", "cyan"))
    rule("═")

    any_detail = False
    for row in report["questions"]:
        for component in row["components"]:
            block_type = component["block_type"]
            metrics = component.get("metrics") or {}

            if block_type == "diagram":
                nodes = metrics.get("node_validation") or []
                edges = metrics.get("edge_comparison") or []
                if not nodes and not edges:
                    continue
                any_detail = True
                matched = sum(1 for n in nodes if n["status"] == "matched")
                out(f"\n  {c(row['question'], 'bold')} — diagram  "
                    f"({matched}/{len(nodes)} labels matched)")
                for node in nodes:
                    label = node["reference_label"]
                    got = node.get("matched_label")
                    glyph = _glyph(node["status"], c)
                    if node["status"] == "matched":
                        out(f"      {glyph} {label}")
                    elif got:
                        out(f"      {glyph} {label}  (student drew: \"{got}\", "
                            f"similarity {node.get('similarity', 0):.2f} — too far to count)")
                    else:
                        out(f"      {glyph} {label}  (not found in student's diagram)")
                if edges:
                    matched_edges = sum(1 for e in edges if e["status"] == "matched")
                    out(f"      edges: {matched_edges}/{len(edges)} matched")
                    for edge in edges:
                        if edge["status"] != "matched":
                            out(f"      {_glyph(edge['status'], c)} {edge['from']} → {edge['to']}")
                extras = metrics.get("anomalies") or []
                for anomaly in extras:
                    text = anomaly.get("description", anomaly) if isinstance(anomaly, dict) else anomaly
                    out(f"      {c('+', 'cyan')} unexpected: {text}")

            elif block_type == "table":
                comparison = metrics.get("comparison") or {}
                verdicts = comparison.get("cell_verdicts") or []
                if not verdicts:
                    continue
                any_detail = True
                counts = comparison.get("verdict_counts", {})
                matched = counts.get("exact", 0) + counts.get("fuzzy", 0) + counts.get("numeric_close", 0)
                out(f"\n  {c(row['question'], 'bold')} — table  "
                    f"({matched}/{len(verdicts)} cells matched)")
                for cell in verdicts:
                    if cell["verdict"] in ("exact",):
                        continue  # exact matches are the expected case; only show what needs a look
                    glyph = _glyph(cell["verdict"], c)
                    where = f"row {cell['reference_row']}, col {cell['reference_col']}"
                    ref = cell["reference_text"] or "(empty)"
                    student = cell["student_text"] if cell["student_text"] is not None else "(missing)"
                    out(f"      {glyph} {where}: expected \"{ref}\" — got \"{student}\"")
                if matched == len(verdicts):
                    out(f"      {c('✓', 'green')} every cell matched exactly")

            elif block_type == "text":
                signals = metrics.get("signals") or {}
                keyword_metrics = (signals.get("keyword") or {}).get("metrics") or {}
                matched_kw = keyword_metrics.get("matched") or []
                missed_kw = keyword_metrics.get("missed") or []
                rubric_metrics = (signals.get("rubric") or {}).get("metrics") or {}
                breakdown = rubric_metrics.get("breakdown") if rubric_metrics.get("mode") == "structured" else None

                if not matched_kw and not missed_kw and not breakdown:
                    continue
                any_detail = True
                out(f"\n  {c(row['question'], 'bold')} — text")
                if matched_kw or missed_kw:
                    out(f"      keywords: {len(matched_kw)}/{len(matched_kw) + len(missed_kw)} matched")
                    for kw in matched_kw:
                        out(f"      {c('✓', 'green')} {kw['term']}")
                    for kw in missed_kw:
                        out(f"      {c('✗', 'red')} {kw['term']}")
                if breakdown:
                    hit = sum(1 for b in breakdown if b["matched"])
                    out(f"      rubric criteria: {hit}/{len(breakdown)} matched")
                    for item in breakdown:
                        glyph = c("✓", "green") if item["matched"] else c("✗", "red")
                        out(f"      {glyph} {item['criterion']}")

    if not any_detail:
        out(c("  (no per-item detail available for this run's components — "
              "stub/offline runs and low-level extraction failures skip it)", "dim"))
    out()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    source = ap.add_argument_group("booklet source")
    source.add_argument("--pdf", type=Path,
                        help="ingest this PDF first, then score it. Without "
                             "--offline the ingestion COMMITS (its regions have to "
                             "exist to be scored).")
    source.add_argument("--student-id", help="students.student_id the booklet belongs to")
    source.add_argument("--exam-id", help="exams.exam_id the booklet was written for")
    source.add_argument("--answer-id", action="append", dest="answer_ids",
                        help="score only these answers (repeatable)")
    source.add_argument("--source-scan-url",
                        help="narrow to one ingestion of this booklet, when the same "
                             "student/exam has been ingested more than once")
    source.add_argument("--paper-id",
                        help="generated_papers.paper_id, used to label questions "
                             "'Q1'/'Q2a' in the report instead of a short question id. "
                             "Required when --pdf is ingested (see ingest_booklet.py).")
    source.add_argument("--offline", action="store_true",
                        help="no database at all: segment --pdf and score its regions "
                             "against SYNTHETIC references. A pipeline check, not a "
                             "measurement — nothing is persisted.")

    run = ap.add_argument_group("run control")
    run.add_argument("--max-concurrency", type=int,
                     default=booklet_evaluator.DEFAULT_MAX_CONCURRENCY,
                     help=f"regions evaluated in parallel (default: "
                          f"{booklet_evaluator.DEFAULT_MAX_CONCURRENCY}). Groq calls are "
                          f"additionally paced by core/llm.py's token bucket; use 1 for "
                          f"a fully deterministic, serial run.")
    run.add_argument("--method", default="blended",
                     help="text scoring method (blended|embeddings|llm|keyword); "
                          "ignored by plugins that have one scoring path")
    run.add_argument("--weights", default=None,
                     help="override blend weights, e.g. 'llm=0.5,semantic=0.3'")
    run.add_argument("--min-confidence", type=float,
                     default=booklet_evaluator.DEFAULT_REVIEW_CONFIDENCE,
                     help="below this a question goes on the review list "
                          f"(default: {booklet_evaluator.DEFAULT_REVIEW_CONFIDENCE})")
    run.add_argument("--dry-run", action="store_true",
                     help="do every scoring write, then roll back")
    run.add_argument("--no-persist", action="store_true",
                     help="report only — read the blocks, score them, write nothing")
    run.add_argument("--stub", action="store_true",
                     help="deterministic fake extraction AND scoring — no model load, "
                          "no API call")
    run.add_argument("--stub-llm", action="store_true",
                     help="fake only the LLM signal; semantic/keyword/rubric run for real")
    run.add_argument("--stub-extraction", action="store_true",
                     help="skip real OCR/layout extraction (for dummy-storage test data)")
    run.add_argument("--stub-segmentation", action="store_true",
                     help="with --pdf: fixed fake regions instead of the layout model")

    ingest = ap.add_argument_group("ingestion (with --pdf)")
    ingest.add_argument("--storage", default="dummy", choices=["dummy", "minio"])
    ingest.add_argument("--dpi", type=int, default=booklet_ingest.DEFAULT_DPI)
    ingest.add_argument("--skip-denoise", action="store_true",
                        help="skip denoising (pure waste on a rendered PDF; ~1s/page)")
    ingest.add_argument("--default-marks", type=float, default=DEFAULT_OFFLINE_MARKS,
                        help=f"marks_max assumed per question in --offline mode, where "
                             f"there is no questions table to read it from "
                             f"(default: {DEFAULT_OFFLINE_MARKS})")

    out_group = ap.add_argument_group("output")
    out_group.add_argument("--output", type=Path, help="also write the JSON report here")
    out_group.add_argument("--quiet", action="store_true",
                           help="suppress the human summary on stderr")
    args = ap.parse_args()

    if args.pdf and not args.pdf.exists():
        print(f"ERROR: PDF not found: {args.pdf}", file=sys.stderr)
        return 1
    if args.offline and not args.pdf:
        print("ERROR: --offline needs --pdf — there is no database to read regions from.",
              file=sys.stderr)
        return 1
    if args.pdf and not args.offline:
        missing = [name for name, value in (("--student-id", args.student_id),
                                            ("--exam-id", args.exam_id),
                                            ("--paper-id", args.paper_id)) if not value]
        if missing:
            print(f"ERROR: {', '.join(missing)} required to ingest and persist a PDF "
                  f"(or use --offline for a no-DB run).", file=sys.stderr)
            return 1
        if args.dry_run:
            print("ERROR: --pdf with --dry-run would roll the ingestion back, leaving "
                  "nothing to score. Ingest with scripts/ingest_booklet.py first and "
                  "score with --student-id/--exam-id, or use --offline.", file=sys.stderr)
            return 1
    if not args.pdf and not (args.answer_ids or (args.student_id and args.exam_id)):
        print("ERROR: give --pdf, or --student-id with --exam-id, or --answer-id.",
              file=sys.stderr)
        return 1

    weights = None
    if args.weights:
        try:
            weights = parse_weights(args.weights)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    evaluate_kwargs = {
        "max_concurrency": args.max_concurrency,
        "stub": args.stub,
        "stub_extraction": args.stub_extraction,
        "stub_llm": args.stub_llm,
        "method": args.method,
        "weights": weights,
        "review_threshold": args.min_confidence,
    }

    try:
        if args.offline:
            report = evaluate_booklet_offline(
                args.pdf, storage_mode=args.storage, dpi=args.dpi,
                skip_denoise=args.skip_denoise, min_confidence=args.min_confidence,
                stub_segmentation=args.stub_segmentation, marks_max=args.default_marks,
                **evaluate_kwargs)
        else:
            booklet_meta, source_scan_url = {}, args.source_scan_url
            if args.pdf:
                from scripts.ingest_booklet import ingest_booklet_file

                ingestion = ingest_booklet_file(
                    args.pdf, student_id=args.student_id, exam_id=args.exam_id,
                    paper_id=args.paper_id, storage_mode=args.storage, dpi=args.dpi,
                    min_confidence=args.min_confidence, persist=True, dry_run=False,
                    stub=args.stub_segmentation, skip_denoise=args.skip_denoise)
                source_scan_url = ingestion["source_pdf_url"]
                booklet_meta = {"pdf": str(args.pdf),
                                "page_count": ingestion["page_count"],
                                "ingested_now": True,
                                "regions_persisted": ingestion["persisted"]["persisted"],
                                "regions_skipped": ingestion["persisted"]["skipped"]}

            conn = db_mod.get_connection()
            try:
                cur = conn.cursor()
                # A booklet run is a system-level job, not a tenant request —
                # the same posture every evaluation runner in this repo takes.
                cur.execute("SET LOCAL app.is_platform_admin = 'true'")
                cur.close()
                report = evaluate_persisted_booklet(
                    conn, student_id=args.student_id, exam_id=args.exam_id,
                    answer_ids=args.answer_ids, source_scan_url=source_scan_url,
                    paper_id=args.paper_id, persist=not args.no_persist,
                    dry_run=args.dry_run, booklet_meta=booklet_meta, **evaluate_kwargs)
            finally:
                conn.close()
    except (ValueError, RLSVisibilityError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(report, indent=2, default=str))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, default=str))
        print(f"\nReport written to {args.output}", file=sys.stderr)
    if not args.quiet:
        print(file=sys.stderr)
        _print_summary(report)

    # A booklet that scored nothing at all is a failed run, not a zero — the
    # exit code has to say so for a batch caller that never reads the JSON.
    return 0 if report["totals"]["questions_scored"] else 2


if __name__ == "__main__":
    sys.exit(main())
