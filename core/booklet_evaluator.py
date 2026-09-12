"""Booklet orchestration, aggregation and reporting — Task 5, second half.

core/booklet_ingest.py turns a PDF into pages, core/booklet_segmenter.py cuts
pages into classified question-associated regions, core/booklet_persist.py
writes those regions as answer_blocks rows. NOTHING is scored by any of them.
This module is what scores them: it routes every region to a plugin, runs
extraction and evaluation under a bounded thread pool and a rate limit, rolls
the per-region scores up into a booklet report, and appends the per-question
result to the evaluation_results ledger.

WHY THIS IS A SEPARATE MODULE FROM THE PLUGINS. A plugin scores one artifact
against one reference (core/plugins/base.py). Everything a BOOKLET needs on
top of that — which plugin gets which region, what happens when one of twenty
regions fails, how several regions of one question combine, how confidence
survives that combination, and how one score per ANSWER is written from many
regions — is orchestration, and belongs in exactly one place rather than being
re-derived by every future caller (an API endpoint, a batch runner, a UI).

--------------------------------------------------------------------------
FOUR DECISIONS THIS MODULE OWNS
--------------------------------------------------------------------------

1. ONE LEDGER ROW PER QUESTION, NOT PER REGION.
   evaluation_results is keyed by ANSWER (migration 001), and
   core/answer_evaluation.record_evaluation() flips every previous is_current
   row for that answer before inserting. A booklet produces MANY blocks per
   answer, so writing one row per region would make each region silently
   overwrite the previous region's score — the last block written would
   become "the" score for the question. scripts/evaluate_pending_diagrams.py
   already refuses to score an answer with two diagram blocks for exactly
   this reason. Here the resolution is aggregation rather than refusal: the
   regions are combined into one per-question result, that single result is
   what lands in the ledger, and every region's own score, plugin, extraction
   and metrics is preserved inside metrics["components"] so nothing is lost.

2. EXTRACT-ALL, THEN EVALUATE-ALL, RATHER THAN A PER-REGION PIPELINE.
   The two phases have completely different cost shapes: extraction is
   CPU-bound and runs against ONE shared, lazily-loaded model instance per
   plugin (core/plugins/registry.py holds a single instance, and neither
   PaddleOCR nor the sentence-transformer is documented thread-safe), while
   evaluation is dominated by a network call to a rate-limited API. So
   extraction is serialized per plugin behind a lock, evaluation runs
   concurrently, and the split also lets a question's several TEXT regions be
   merged into one answer before scoring — see 3.

3. A QUESTION'S TEXT REGIONS ARE ONE ANSWER, NOT SEVERAL.
   A paragraph continuing onto the next page is two regions of one answer;
   scoring each half against the whole reference answer would mark a complete
   answer twice as incomplete. Text extractions for a question are therefore
   concatenated in reading order and evaluated ONCE. Tables and diagrams are
   not concatenable — two table regions are two tables — so each is scored on
   its own, the best-scoring one becomes the question's component, and the
   question is FLAGGED (multiple_table_regions) rather than having the
   ambiguity silently resolved.

4. CONFIDENCE PROPAGATES BY ITS WEAKEST LINKS.
   An unweighted mean of twenty region confidences hides the one region that
   was read badly, which is precisely the region a teacher needs to see. So:
   a merged text extraction takes the MINIMUM of its parts' confidences; a
   question takes the minimum of its component's evaluation confidence and
   every one of its regions' classification confidences; and the booklet
   takes a marks-weighted HARMONIC mean of the question confidences, which a
   single low value drags down in a way an arithmetic mean does not, further
   multiplied by region coverage so that regions lost to failures cost
   confidence instead of quietly vanishing. propagate_confidence() reports
   the arithmetic mean alongside it, so the difference is visible rather than
   asserted.

--------------------------------------------------------------------------
PARTIAL FAILURE IS THE NORMAL CASE
--------------------------------------------------------------------------
One region of twenty failing must never abort a booklet — a booklet is a
student's whole exam, and losing nineteen good scores because one region's
OCR blew up is the worst possible trade. Every stage (routing, reference
lookup, extraction, evaluation, and the ledger write itself) is caught per
unit of work, recorded as a structured failure with its stage and error type,
and reported both against the question it belongs to and in the report's
top-level `failures` list. A question whose every component failed is scored
`null`, NOT zero: an unscored answer and an answer worth no marks are
different facts, and the report keeps them apart (`totals.score` counts the
unscored question as 0 marks earned, while `totals.attempted_*` excludes it
entirely, so the gap between the two is the size of the problem).

That is the same posture the ingestion half already takes — see
core/booklet_segmenter.py's "ambiguity is flagged, never guessed" and
core/booklet_persist.py's per-region skip reasons.

--------------------------------------------------------------------------
RATE LIMITING
--------------------------------------------------------------------------
The throttle itself lives in core/llm.py (token bucket + 429 backoff with
jitter + a daily budget), NOT here, because every LLM call site benefits from
it and only some of them are booklets. What this module owns is the OTHER
half of the same problem: bounded concurrency. Twenty workers all politely
waiting on one token bucket still means twenty open HTTP connections and a
thundering herd on every retry, so the pool is small by default
(DEFAULT_MAX_CONCURRENCY) and the report carries core/llm.py's own throttle
statistics, which is what makes "the booklet took four minutes" attributable
to waiting for quota rather than to a slow model.

--------------------------------------------------------------------------
LAYERING
--------------------------------------------------------------------------
Sections below, in order: routing -> orchestration -> aggregation ->
persistence -> DB loading. The first three touch no database at all and can
be exercised entirely on hand-built BlockTask lists (tests/test_booklet_
evaluator.py does exactly that). The last two are the only ones that need a
connection, kept last and clearly marked, the same split
core/table_extractor.py (pure) and core/plugins/persistence.py (writes)
established.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import inspect
import threading
import time
from datetime import datetime, timezone
from typing import Any

from core import block_evaluation, llm

#: Bump when the REPORT SHAPE or the aggregation rules change — the report is
#: stored inside evaluation_results.metrics, an append-only ledger, so a
#: consumer reading rows scored months apart needs to know which rules
#: produced each one. Same discipline as EvaluationPlugin.version.
BOOKLET_EVALUATOR_NAME = "booklet_evaluator"
BOOKLET_EVALUATOR_VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = 1

#: Deliberately small. The Groq free tier is ~30 requests/minute and
#: core/llm.py paces to 25; more workers than this do not get their answers
#: any sooner, they only queue deeper on the bucket and hold more sockets
#: open. 4 keeps the pipeline full while a request is in flight.
DEFAULT_MAX_CONCURRENCY = 4

#: Below this, a question goes on the teacher-review list however clean its
#: score looks. Matches core/booklet_segmenter.DEFAULT_MIN_CONFIDENCE (the
#: segmentation-side threshold) and core/plugins/text_extraction.
#: LOW_CONFIDENCE_THRESHOLD (the scoring-side one) — three thresholds that
#: mean the same thing to a teacher should not disagree by default.
DEFAULT_REVIEW_CONFIDENCE = 0.60

#: Floor applied before the harmonic mean. A single genuinely-zero confidence
#: would otherwise send the whole booklet's confidence to exactly 0 and make
#: every other question's confidence unreadable; 0.01 keeps it dominated by
#: the weakest link without being annihilated by it.
CONFIDENCE_FLOOR = 0.01

#: Block types whose regions are one continuous answer split across regions
#: and pages, and are therefore merged before scoring. Everything else is one
#: artifact per region — see decision 3 in the module docstring.
MERGEABLE_BLOCK_TYPES = frozenset({"text"})

#: Separator used when merging a question's text regions. Blank-line rather
#: than newline: the parts are usually paragraphs (often from different
#: pages), and the OCR plugin already joins its own lines with "\n".
TEXT_MERGE_SEPARATOR = "\n\n"


# =========================================================================
# Task and outcome types
# =========================================================================

@dataclasses.dataclass
class BlockTask:
    """One region to be scored, with everything needed to score it.

    Built either from persisted answer_blocks rows (load_booklet_tasks below)
    or straight from core/booklet_segmenter.py's region dicts (the offline
    path in scripts/evaluate_booklet.py). Nothing downstream of construction
    touches a database, which is what lets the whole orchestration and
    aggregation half run with no DB at all.

    `source` is whatever the plugin's extract() accepts: a "bucket/key"
    blob_url str, a PIL Image of the already-cropped region, or a
    core.plugins.text_extraction.PlainText for a block whose text is already
    digital. `reference` is the plugin-specific reference object
    (TextReference / TableReference / DiagramReference).

    `reference_error` is set INSTEAD of `reference` when the question has
    nothing to score against (no current reference variant, no linked asset,
    or two of them). The task is still built and still appears in the report
    — a question that cannot be scored is a finding, not an absence.
    """

    question_label: str
    block_type: str
    source: Any = None
    reference: Any = None
    reference_kind: str | None = None       # 'variant' | 'asset' | 'synthetic'
    reference_id: str | None = None
    reference_error: str | None = None
    marks_max: float = 0.0
    block_id: str | None = None
    answer_id: str | None = None
    question_id: str | None = None
    page_number: int | None = None
    bbox: list | None = None
    sequence_order: int = 0
    classification_label: str | None = None
    classification_confidence: float | None = None
    needs_review: bool = False
    review_reasons: list = dataclasses.field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable identity for reporting. A block_id when the region was
        persisted; otherwise a page/position key, so the offline path (which
        never touches the DB and so has no block_id) still produces a report
        whose rows can be told apart."""
        if self.block_id:
            return self.block_id
        return f"p{self.page_number}:{self.sequence_order}:{self.block_type}"

    def describe(self) -> dict:
        """JSON-safe identity of this region, for report rows and failures.
        Never includes `source` — it may be a PIL Image."""
        return {
            "block_id": self.block_id,
            "key": self.key,
            "question": self.question_label,
            "block_type": self.block_type,
            "page_number": self.page_number,
            "bbox": self.bbox,
        }


@dataclasses.dataclass
class Extraction:
    """The result of extract() for one task — or the failure that replaced it."""

    task: BlockTask
    plugin_name: str | None = None
    plugin_version: str | None = None
    result: Any = None                      # core.plugins.base.ExtractionResult
    failure: dict | None = None
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.failure is None and self.result is not None


@dataclasses.dataclass
class Component:
    """One scorable unit of a question: a merged text answer, or a single
    table/diagram region. The thing a plugin's evaluate() is actually called
    on — see decision 3 in the module docstring."""

    question_label: str
    block_type: str
    plugin: Any
    extraction: Any                         # ExtractionResult (possibly merged)
    reference: Any
    reference_kind: str | None
    reference_id: str | None
    tasks: list = dataclasses.field(default_factory=list)
    merged_from: int = 1

    @property
    def block_ids(self) -> list:
        return [t.block_id for t in self.tasks if t.block_id]

    @property
    def pages(self) -> list:
        return sorted({t.page_number for t in self.tasks if t.page_number is not None})


@dataclasses.dataclass
class ComponentOutcome:
    component: Component
    result: Any = None                      # core.plugins.base.EvaluationResult
    failure: dict | None = None
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.failure is None and self.result is not None


def _failure(stage: str, error: BaseException | str, *, task: BlockTask | None = None,
             component: Component | None = None, **extra) -> dict:
    """One structured failure record.

    `stage` is where it happened ('route', 'reference', 'extract', 'evaluate',
    'persist'); `error_type` is the exception class name, or the stage's own
    name for the refusals this module raises itself. Both are kept because
    they answer different questions: the stage says which part of the
    pipeline to look at, the type says whether it is a missing dependency, a
    missing reference or a genuine crash.
    """
    if isinstance(error, BaseException):
        error_type, message = type(error).__name__, str(error)
    else:
        error_type, message = stage, str(error)

    record = {"stage": stage, "error_type": error_type, "message": message}
    if task is not None:
        record.update(task.describe())
    elif component is not None:
        record.update({
            "block_id": component.block_ids[0] if component.block_ids else None,
            "key": component.tasks[0].key if component.tasks else None,
            "question": component.question_label,
            "block_type": component.block_type,
            "page_number": component.pages[0] if component.pages else None,
        })
    record.update(extra)
    return record


# =========================================================================
# Routing
# =========================================================================

class NoPluginError(RuntimeError):
    """No registered plugin claims this block_type.

    Its own type because the fix is never "retry": either the block was
    classified as something nothing scores yet (core/booklet_segmenter.py
    maps some layout labels to 'formula', which is a valid
    answer_block_type with no plugin behind it — flagged there for this
    exact reason), or the plugin that should have claimed it failed to
    import and the registry warned about that separately.
    """


def select_plugin(block_type: str) -> tuple[Any, list]:
    """Pick the plugin for one block_type. Returns (plugin, candidate names).

    core/plugins/registry.plugins_for() deliberately returns EVERY plugin
    that claims a block type and leaves the choice to the caller — two
    diagram scorers being compared is a legitimate state. A booklet run is
    unattended, so the choice here is "the first in stable name order", and
    the full candidate list travels into the report and the ledger metrics so
    a score can always be attributed to the right module.
    """
    from core.plugins.registry import import_failures, plugins_for

    matched = plugins_for(block_type)
    if not matched:
        failures = import_failures()
        detail = (f" Plugin modules that failed to import: {failures}." if failures else "")
        raise NoPluginError(
            f"no registered evaluation plugin supports block_type={block_type!r}."
            f"{detail} The region keeps its classification and is reported for "
            f"human review rather than being dropped."
        )
    return matched[0], [p.name for p in matched]


# =========================================================================
# Orchestration
# =========================================================================

_PLUGIN_LOCKS: dict[str, threading.Lock] = {}
_PLUGIN_LOCKS_GUARD = threading.Lock()


def _plugin_lock(name: str) -> threading.Lock:
    """One lock per plugin, guarding its extract().

    core/plugins/registry.py hands out a SINGLE instance per plugin and every
    plugin lazily loads and caches its own models inside that instance
    (PaddleOCR, the OCR ensemble, sentence-transformers). None of them
    documents thread-safety, and a shared model being entered concurrently is
    the kind of bug that shows up as a wrong score rather than a crash. So
    extraction serializes per plugin — different plugins still overlap, and
    the phase that actually benefits from concurrency (evaluate(), which
    waits on the network) is not affected at all.
    """
    with _PLUGIN_LOCKS_GUARD:
        return _PLUGIN_LOCKS.setdefault(name, threading.Lock())


def _evaluate_kwargs(plugin, *, stub: bool, stub_llm: bool,
                     method: str | None, weights: dict | None) -> dict:
    """Build evaluate()'s keyword arguments for whichever plugin this is.

    Determined by INSPECTING the plugin's signature rather than by a name ->
    kwargs table, because the registry is dynamic: a plugin dropped into
    core/plugins/ must work here without this module being edited, which is
    the whole point of core/plugins/registry.py. Every plugin takes `stub`
    (the base-class contract); `stub_llm`, `method` and `weights` are offered
    only to the plugins that declare them.
    """
    parameters = inspect.signature(plugin.evaluate).parameters
    kwargs: dict = {"stub": stub}
    if "stub_llm" in parameters:
        kwargs["stub_llm"] = stub_llm
    if method and "method" in parameters:
        kwargs["method"] = method
    if weights and "weights" in parameters:
        kwargs["weights"] = weights
    return kwargs


def _run_bounded(items, worker, *, max_concurrency: int) -> list:
    """Map `worker` over `items` with a bounded pool, preserving input order.

    max_concurrency <= 1 runs inline with no threads at all, which is what
    makes a --max-concurrency 1 run byte-for-byte debuggable (and what the
    tests use). `worker` must never raise: every call site below catches its
    own exceptions and returns a failure record instead, so an escaped
    exception here would be a bug in this module, not a scoring failure.
    """
    if not items:
        return []
    if max_concurrency <= 1:
        return [worker(item) for item in items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        return list(pool.map(worker, items))


def extract_tasks(tasks: list, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
                  stub: bool = False, stub_extraction: bool = False) -> list:
    """Phase 1: run every task's plugin extract(). Never raises.

    Routing and reference errors are resolved here too, so that a task with
    no reference costs no extraction work — there is nothing to score it
    against either way, and OCR is the expensive half.
    """
    def worker(task: BlockTask) -> Extraction:
        try:
            plugin, candidates = select_plugin(task.block_type)
        except NoPluginError as exc:
            return Extraction(task=task, failure=_failure("route", exc, task=task))

        if task.reference is None:
            return Extraction(
                task=task, plugin_name=plugin.name, plugin_version=plugin.version,
                failure=_failure("reference",
                                 task.reference_error or
                                 "no reference available for this question",
                                 task=task),
            )

        if task.source is None and not (stub or stub_extraction):
            # Caught here rather than left to the plugin: extract(None) ends
            # up deep inside an OCR engine with an unrecognizable error, and
            # a block with neither content nor blob_url is a persistence
            # problem, not a scoring one.
            return Extraction(
                task=task, plugin_name=plugin.name, plugin_version=plugin.version,
                failure=_failure("extract",
                                 "answer_block has neither content nor blob_url — "
                                 "nothing to extract from (use --stub-extraction for "
                                 "dummy-storage test data)",
                                 task=task, plugin=plugin.name),
            )

        started = time.perf_counter()
        try:
            with _plugin_lock(plugin.name):
                result = plugin.extract(task.source, stub=stub or stub_extraction)
        except Exception as exc:            # noqa: BLE001 — one region must not abort a booklet
            return Extraction(
                task=task, plugin_name=plugin.name, plugin_version=plugin.version,
                failure=_failure("extract", exc, task=task,
                                 plugin=plugin.name, candidates=candidates),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        return Extraction(
            task=task, plugin_name=plugin.name, plugin_version=plugin.version,
            result=result, latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    return _run_bounded(tasks, worker, max_concurrency=max_concurrency)


def build_components(extractions: list) -> list:
    """Phase 2: group successful extractions into scorable components.

    Text regions of one question become ONE component with their contents
    concatenated in reading order (page, then sequence) and their confidence
    reduced to the MINIMUM of the parts — see decisions 3 and 4 in the module
    docstring. Every other block type stays one component per region.
    """
    from core.plugins.base import ExtractionResult
    from core.plugins.registry import get_plugin

    grouped: dict = {}
    for extraction in extractions:
        if not extraction.ok:
            continue
        task = extraction.task
        grouped.setdefault((task.question_label, task.block_type), []).append(extraction)

    components = []
    for (question_label, block_type), parts in grouped.items():
        parts.sort(key=lambda e: (e.task.page_number or 0, e.task.sequence_order))
        plugin = get_plugin(parts[0].plugin_name)
        first = parts[0].task

        if block_type in MERGEABLE_BLOCK_TYPES and len(parts) > 1:
            merged_text = TEXT_MERGE_SEPARATOR.join(
                str(part.result.content) for part in parts if part.result.content
            )
            merged = ExtractionResult(
                content=merged_text,
                # Weakest link: a question whose second page was read badly
                # is not two-thirds trustworthy, it is as trustworthy as its
                # worst-read region.
                confidence=round(min(part.result.confidence for part in parts), 4),
                metrics={
                    "plugin": plugin.name,
                    "plugin_version": plugin.version,
                    "mode": "merged",
                    "merged_from": len(parts),
                    "latency_ms": round(sum(part.latency_ms for part in parts), 2),
                    "parts": [
                        {**part.task.describe(),
                         "confidence": part.result.confidence,
                         "chars": len(str(part.result.content or "")),
                         "metrics": part.result.metrics}
                        for part in parts
                    ],
                },
            )
            components.append(Component(
                question_label=question_label, block_type=block_type, plugin=plugin,
                extraction=merged, reference=first.reference,
                reference_kind=first.reference_kind, reference_id=first.reference_id,
                tasks=[part.task for part in parts], merged_from=len(parts),
            ))
            continue

        for part in parts:
            components.append(Component(
                question_label=question_label, block_type=block_type, plugin=plugin,
                extraction=part.result, reference=part.task.reference,
                reference_kind=part.task.reference_kind,
                reference_id=part.task.reference_id, tasks=[part.task],
            ))

    components.sort(key=lambda c: (c.question_label, c.block_type,
                                   c.pages[0] if c.pages else 0))
    return components


def evaluate_components(components: list, *,
                        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
                        stub: bool = False, stub_llm: bool = False,
                        method: str | None = None, weights: dict | None = None) -> list:
    """Phase 3: score every component. Never raises.

    This is the phase the bounded pool exists for: evaluate() is where the
    LLM call happens, and it is paced by core/llm.py's token bucket rather
    than by the pool size — the pool only limits how many requests may be
    in flight or queued at once (see the module docstring's rate-limiting
    section).
    """
    def worker(component: Component) -> ComponentOutcome:
        started = time.perf_counter()
        try:
            kwargs = _evaluate_kwargs(component.plugin, stub=stub, stub_llm=stub_llm,
                                      method=method, weights=weights)
            result = component.plugin.evaluate(component.extraction,
                                               component.reference, **kwargs)
        except Exception as exc:            # noqa: BLE001 — see the module docstring
            return ComponentOutcome(
                component=component,
                failure=_failure("evaluate", exc, component=component,
                                 plugin=component.plugin.name),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        return ComponentOutcome(
            component=component, result=result,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    return _run_bounded(components, worker, max_concurrency=max_concurrency)


# =========================================================================
# Aggregation
# =========================================================================

def _signal_summary(metrics: dict) -> dict | None:
    """The text plugin's per-signal breakdown, trimmed for the report.

    core/plugins/text_extraction.py's blended scoring reports semantic, llm,
    keyword and rubric independently, with the effective weight each one got
    after renormalization. That breakdown is the whole reason a teacher can
    see WHY a score was given rather than only what it was, so it is
    surfaced at the top of every text component instead of being left buried
    in the raw metrics (which are still carried verbatim alongside it).
    Returns None for a plugin that has no such signals — nothing is invented
    for the plugins that legitimately have one scoring path.
    """
    signals = metrics.get("signals")
    if not isinstance(signals, dict):
        return None
    weights = metrics.get("weights") or {}
    summary = {}
    for name, signal in signals.items():
        if not isinstance(signal, dict):
            continue
        summary[name] = {
            "score": signal.get("score"),
            "model": signal.get("model"),
            "weight": weights.get(name),
            "available": signal.get("score") is not None,
            "explanation": signal.get("explanation"),
        }
    return summary or None


def _component_detail(metrics: dict) -> dict | None:
    """The non-text plugins' equivalent of _signal_summary: the few numbers
    from their comparison that a teacher acts on.

    Read by KEY rather than by plugin name, deliberately — a new plugin that
    reports a structural/content split gets the same treatment for free, and
    a plugin that reports neither simply contributes nothing here. Note the
    two shapes: core/plugins/table_extraction.py nests its comparison under
    "comparison", core/plugins/diagram_evaluation.py merges it flat (for the
    append-only-ledger reason its module docstring gives), so both are
    checked.
    """
    source = metrics.get("comparison") if isinstance(metrics.get("comparison"), dict) else metrics
    detail = {}
    for key in ("structural_score", "content_score", "content_score_on_aligned",
                "verdict_counts", "overall_accuracy", "scores_breakdown"):
        if key in source:
            detail[key] = source[key]
    for key in ("missing_information", "anomalies", "missing_rows", "extra_rows"):
        value = source.get(key)
        if isinstance(value, list):
            detail[f"{key}_count"] = len(value)
    return detail or None


def _component_row(outcome: ComponentOutcome) -> dict:
    """One scored component, as it appears in the report and in the ledger's
    metrics["components"]. JSON-safe throughout."""
    component, result = outcome.component, outcome.result
    return {
        "block_type": component.block_type,
        "plugin": component.plugin.name,
        "plugin_version": component.plugin.version,
        "evaluator_model": result.metrics.get("evaluator_model"),
        "reference_kind": component.reference_kind,
        "reference_id": component.reference_id,
        "score": result.score,
        "max_score": result.max_score,
        "confidence": round(float(result.confidence), 4),
        "regions": [task.describe() for task in component.tasks],
        "block_ids": component.block_ids,
        "pages": component.pages,
        "merged_from": component.merged_from,
        "extraction_confidence": round(float(component.extraction.confidence), 4),
        "explanation": result.explanation,
        "signals": _signal_summary(result.metrics),
        "detail": _component_detail(result.metrics),
        "latency_ms": outcome.latency_ms,
        "metrics": result.metrics,
    }


def _pick_primary(rows: list) -> tuple[dict, list]:
    """Choose the component that becomes the question's score, and the flags
    the choice raises.

    Two levels of ambiguity, both FLAGGED rather than silently resolved:
      * several regions of the same non-mergeable type (two table regions on
        one question) — the best-scoring one wins, since a student who drew
        the table twice is credited for the better attempt, and the question
        is flagged so a teacher can confirm that reading;
      * several different types scoring the same question (a text answer AND
        a diagram, each with its own reference) — the HIGHEST-CONFIDENCE one
        becomes the question's score, because the ledger holds one score per
        answer and there is no defensible way to add two independent
        judgements of the same marks_max together.
    """
    flags = []
    by_type: dict = {}
    for row in rows:
        by_type.setdefault(row["block_type"], []).append(row)

    per_type = []
    for block_type, candidates in sorted(by_type.items()):
        candidates.sort(key=lambda r: (-r["score"], r["block_ids"]))
        per_type.append(candidates[0])
        if len(candidates) > 1:
            flags.append(f"multiple_{block_type}_regions:{len(candidates)}")

    primary = max(per_type, key=lambda r: (r["confidence"], r["score"]))
    if len(per_type) > 1:
        flags.append("multiple_scored_components:" +
                     ",".join(sorted(row["block_type"] for row in per_type)))
    return primary, flags


def _question_explanation(label: str, row: dict) -> str:
    """The one-line human summary stored in evaluation_results.explanation
    for the aggregated question row. Leads with the number and the region
    count, because "6.5/10 from 3 regions" is the sentence a teacher reads
    first; the component's own explanation (the text plugin's per-signal
    breakdown, the table's structural/content split) follows verbatim."""
    if not row["scored"]:
        return (f"Booklet question {label}: NOT SCORED — "
                f"{len(row['failures'])} failure(s): "
                + "; ".join(f"{f['stage']}/{f['error_type']}" for f in row["failures"]) + ".")

    primary = row["primary"]
    parts = [
        f"Booklet question {label}: {row['score']}/{row['marks_max']}",
        f"from {row['regions']['evaluated']}/{row['regions']['total']} region(s)",
        f"via {primary['plugin']} ({primary['block_type']})",
        f"confidence {row['confidence']}",
    ]
    summary = ", ".join(parts) + ". " + (primary.get("explanation") or "")
    if row["flags"]:
        summary += " FLAGS: " + ", ".join(row["flags"]) + "."
    if row["failures"]:
        summary += f" {len(row['failures'])} region(s) failed and are excluded."
    return summary.strip()


def propagate_confidence(question_rows: list, *, regions_routable: int,
                         regions_evaluated: int) -> dict:
    """Booklet confidence from the question confidences — weakest links first.

    Marks-weighted HARMONIC mean, then multiplied by region coverage:

        harmonic = sum(w) / sum(w / max(confidence, CONFIDENCE_FLOOR))
        booklet  = harmonic * (regions_evaluated / regions_routable)

    The harmonic mean is dominated by its smallest terms — one question at
    0.2 pulls a booklet of nines down hard, where an arithmetic mean would
    absorb it — and weighting by marks_max says that a shaky read on a
    10-mark question matters more than a shaky read on a 2-mark one. The
    coverage factor is what stops failures from IMPROVING confidence: without
    it, a booklet whose only doubtful regions crashed would report higher
    confidence than one where they were scored badly.

    The arithmetic mean and the outright minimum are reported alongside, so
    the claim that this is weakest-link-dominated can be checked against the
    same numbers rather than taken on faith.
    """
    scored = [row for row in question_rows if row["scored"]]
    if not scored:
        return {"booklet": 0.0, "method": "no scored questions", "coverage": 0.0,
                "harmonic_weighted": 0.0, "arithmetic_mean": 0.0, "weakest": 0.0,
                "weakest_question": None}

    total_weight = sum(row["marks_max"] for row in scored) or float(len(scored))
    denominator = sum((row["marks_max"] or 1.0) / max(row["confidence"], CONFIDENCE_FLOOR)
                      for row in scored)
    harmonic = total_weight / denominator if denominator else 0.0
    arithmetic = sum(row["confidence"] for row in scored) / len(scored)
    weakest_row = min(scored, key=lambda row: row["confidence"])
    coverage = (regions_evaluated / regions_routable) if regions_routable else 0.0

    return {
        "booklet": round(harmonic * coverage, 4),
        "method": "marks-weighted harmonic mean of question confidence x region coverage",
        "harmonic_weighted": round(harmonic, 4),
        "arithmetic_mean": round(arithmetic, 4),
        "weakest": round(weakest_row["confidence"], 4),
        "weakest_question": weakest_row["question"],
        "coverage": round(coverage, 4),
        "regions_evaluated": regions_evaluated,
        "regions_routable": regions_routable,
    }


def _page_rows(tasks: list, evaluated_keys: set, failed_keys: set,
               question_pages: dict, unassigned_regions: list) -> list:
    """Per-page rollup.

    A page does NOT own a score: an answer routinely spans a page break, and
    splitting a question's marks across the pages its regions happened to
    land on would be invented precision. What a page owns is its regions and
    which questions they belong to, so `score_from_complete` sums only the
    questions whose EVERY region is on this page, and questions that span the
    break are listed separately instead of being counted twice or halved.
    """
    pages: dict = {}
    for task in tasks:
        page = pages.setdefault(task.page_number, {
            "page_number": task.page_number, "regions": 0, "evaluated": 0,
            "failed": 0, "flagged": 0, "unassigned": 0, "questions": set(),
        })
        page["regions"] += 1
        page["questions"].add(task.question_label)
        if task.key in evaluated_keys:
            page["evaluated"] += 1
        if task.key in failed_keys:
            page["failed"] += 1
        if task.needs_review:
            page["flagged"] += 1

    for region in unassigned_regions or []:
        page = pages.setdefault(region.get("page_number"), {
            "page_number": region.get("page_number"), "regions": 0, "evaluated": 0,
            "failed": 0, "flagged": 0, "unassigned": 0, "questions": set(),
        })
        page["regions"] += 1
        page["unassigned"] += 1
        page["flagged"] += 1

    rows = []
    for page in sorted(pages.values(), key=lambda p: (p["page_number"] is None,
                                                      p["page_number"] or 0)):
        labels = sorted(page.pop("questions"))
        # A question with NO page provenance at all (every answer_blocks row
        # written before migration 013 has page_number NULL) is not
        # "spanning" — it is simply unlocated, and calling it spanning would
        # invent a fact about a booklet that was never scanned as one.
        complete = [label for label in labels
                    if question_pages.get(label, set()) in ({page["page_number"]}, set())]
        spanning = [label for label in labels if label not in complete]
        rows.append({**page, "questions": labels,
                     "questions_complete_on_page": complete,
                     "spanning_questions": spanning})
    return rows


def aggregate(tasks: list, extractions: list, outcomes: list, *,
              review_threshold: float = DEFAULT_REVIEW_CONFIDENCE,
              unassigned_regions: list | None = None,
              booklet: dict | None = None,
              started_at: float | None = None) -> dict:
    """Roll per-region results up into the booklet report.

    per-region -> per-question -> per-page -> booklet total, with the
    confidence and ambiguity rules the module docstring lays out. Pure: no
    DB, no network, no plugin calls — everything it needs already happened.
    """
    unassigned_regions = unassigned_regions or []
    failures = [e.failure for e in extractions if e.failure]
    failures += [o.failure for o in outcomes if o.failure]

    evaluated_keys = {task.key for outcome in outcomes if outcome.ok
                      for task in outcome.component.tasks}
    failed_keys = {failure["key"] for failure in failures if failure.get("key")}

    tasks_by_question: dict = {}
    for task in tasks:
        tasks_by_question.setdefault(task.question_label, []).append(task)

    rows_by_question: dict = {}
    for outcome in outcomes:
        if outcome.ok:
            rows_by_question.setdefault(outcome.component.question_label, []).append(
                _component_row(outcome))

    question_rows = []
    for label, question_tasks in tasks_by_question.items():
        question_tasks.sort(key=lambda t: (t.page_number or 0, t.sequence_order))
        marks_max = next((t.marks_max for t in question_tasks if t.marks_max), 0.0)
        question_failures = [f for f in failures if f.get("question") == label]
        component_rows = rows_by_question.get(label, [])

        row = {
            "question": label,
            "question_id": question_tasks[0].question_id,
            "answer_id": question_tasks[0].answer_id,
            "marks_max": marks_max,
            "score": None,
            "percentage": None,
            "confidence": 0.0,
            "scored": bool(component_rows),
            "pages": sorted({t.page_number for t in question_tasks
                             if t.page_number is not None}),
            "regions": {
                "total": len(question_tasks),
                "evaluated": sum(1 for t in question_tasks if t.key in evaluated_keys),
                "failed": sum(1 for t in question_tasks if t.key in failed_keys),
                "flagged": sum(1 for t in question_tasks if t.needs_review),
            },
            "components": component_rows,
            "primary": None,
            "flags": [],
            "failures": question_failures,
        }

        if component_rows:
            primary, flags = _pick_primary(component_rows)
            for candidate in component_rows:
                candidate["primary"] = candidate is primary
            row["primary"] = primary
            row["flags"] = flags
            row["score"] = primary["score"]
            row["percentage"] = (round(primary["score"] / marks_max, 4)
                                 if marks_max else None)
            # Weakest link across BOTH axes: how well the scorer trusted its
            # own judgement, and how confidently the segmenter classified any
            # of the regions that made up the answer (a wrongly-typed region
            # is a wrong score no matter how confident the scorer was).
            classification = [t.classification_confidence for t in question_tasks
                              if t.classification_confidence is not None]
            row["confidence"] = round(min([primary["confidence"]] + classification), 4)
            row["persistence"] = {
                "answer_id": question_tasks[0].answer_id,
                "primary_block_id": primary["block_ids"][0] if primary["block_ids"] else None,
                "reference_kind": primary["reference_kind"],
                "reference_id": primary["reference_id"],
            }
        else:
            row["flags"].append("unscored")

        if row["regions"]["failed"]:
            row["flags"].append(f"partial_failure:{row['regions']['failed']}")
        segmentation_reasons = sorted({reason for t in question_tasks
                                       for reason in (t.review_reasons or [])})
        if segmentation_reasons:
            row["flags"].append("segmentation:" + ",".join(segmentation_reasons))
        if row["scored"] and row["confidence"] < review_threshold:
            row["flags"].append("low_confidence")

        row["needs_review"] = bool(row["flags"]) or not row["scored"]
        row["explanation"] = _question_explanation(label, row)
        question_rows.append(row)

    question_rows.sort(key=lambda r: (r["pages"][0] if r["pages"] else 0, r["question"]))

    scored_rows = [row for row in question_rows if row["scored"]]
    max_score = sum(row["marks_max"] for row in question_rows)
    score = sum(row["score"] for row in scored_rows)
    attempted_max = sum(row["marks_max"] for row in scored_rows)

    confidence = propagate_confidence(
        question_rows,
        regions_routable=len(tasks),
        regions_evaluated=len(evaluated_keys),
    )

    question_pages = {row["question"]: set(row["pages"]) for row in question_rows}
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generator": {"name": BOOKLET_EVALUATOR_NAME,
                      "version": BOOKLET_EVALUATOR_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "booklet": booklet or {},
        "totals": {
            "score": round(score, 2),
            "max_score": round(max_score, 2),
            # Against every question found, INCLUDING the ones nothing could
            # score — an unscored question is a zero to the student until a
            # human intervenes, and hiding that in a percentage over only the
            # scored ones is how a broken pipeline looks like a good result.
            "percentage": round(score / max_score, 4) if max_score else None,
            "attempted_score": round(score, 2),
            "attempted_max_score": round(attempted_max, 2),
            "attempted_percentage": (round(score / attempted_max, 4)
                                     if attempted_max else None),
            "questions_total": len(question_rows),
            "questions_scored": len(scored_rows),
            "questions_unscored": len(question_rows) - len(scored_rows),
            "questions_needing_review": sum(1 for row in question_rows
                                            if row["needs_review"]),
            "regions_total": len(tasks) + len(unassigned_regions),
            "regions_routable": len(tasks),
            "regions_evaluated": len(evaluated_keys),
            "regions_failed": len(failed_keys),
            "regions_unassigned": len(unassigned_regions),
        },
        "confidence": confidence,
        "questions": question_rows,
        "pages": _page_rows(tasks, evaluated_keys, failed_keys, question_pages,
                            unassigned_regions),
        "review_queue": _review_queue(question_rows, tasks, unassigned_regions,
                                      review_threshold),
        "failures": failures,
        "rate_limit": llm.rate_limit_stats(),
        "review_threshold": review_threshold,
    }
    if started_at is not None:
        # Orchestration only — ingestion and segmentation happen before this
        # module is ever called, and claiming their seconds here would make a
        # 40-second layout pass look like scoring cost.
        report["totals"]["evaluation_seconds"] = round(time.perf_counter() - started_at, 2)
    return report


def _review_queue(question_rows: list, tasks: list, unassigned_regions: list,
                  review_threshold: float) -> list:
    """Everything a teacher has to look at, lowest confidence first.

    Three kinds of entry, kept in ONE list because a reviewer's question is
    "what needs me", not "which subsystem was unsure": questions whose score
    is not trustworthy, regions the segmenter itself flagged
    (core/booklet_segmenter.py's needs_review), and regions that were never
    assigned to a question at all — those last are not persisted anywhere
    (core/booklet_persist.py: answer_blocks.answer_id is NOT NULL, so an
    orphan region has nowhere to live), so this report is the ONLY place they
    are ever surfaced.
    """
    queue = []
    for row in question_rows:
        if not row["needs_review"]:
            continue
        reasons = list(row["flags"])
        if not row["scored"]:
            reasons.append("no component could be scored")
        queue.append({
            "kind": "question",
            "question": row["question"],
            "pages": row["pages"],
            "confidence": row["confidence"],
            "score": row["score"],
            "marks_max": row["marks_max"],
            "reasons": reasons,
            "block_ids": [block_id for component in row["components"]
                          for block_id in component["block_ids"]],
        })

    for task in tasks:
        if not task.needs_review:
            continue
        queue.append({
            "kind": "region",
            "question": task.question_label,
            "pages": [task.page_number],
            "confidence": task.classification_confidence,
            "reasons": list(task.review_reasons or ["needs_review"]),
            "block_ids": [task.block_id] if task.block_id else [],
            "bbox": task.bbox,
            "block_type": task.block_type,
        })

    for region in unassigned_regions or []:
        queue.append({
            "kind": "unassigned_region",
            "question": None,
            "pages": [region.get("page_number")],
            "confidence": region.get("confidence"),
            "reasons": ([region["unassigned_reason"]] if region.get("unassigned_reason")
                        else list(region.get("review_reasons") or ["unassigned"])),
            "block_ids": [],
            "bbox": region.get("bbox"),
            "block_type": region.get("block_type"),
        })

    queue.sort(key=lambda item: (item["confidence"] if item["confidence"] is not None else -1.0))
    return queue


def evaluate_booklet(tasks: list, *,
                     max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
                     stub: bool = False, stub_extraction: bool = False,
                     stub_llm: bool = False, method: str | None = None,
                     weights: dict | None = None,
                     review_threshold: float = DEFAULT_REVIEW_CONFIDENCE,
                     unassigned_regions: list | None = None,
                     booklet: dict | None = None,
                     on_extracted: Any = None) -> dict:
    """Extract -> evaluate -> aggregate, over a whole booklet's regions.

    The one entry point a caller needs. Takes BlockTasks (from
    load_booklet_tasks(), or built directly from segmenter regions) and
    returns the report dict. Touches no database — persist_question_results()
    is a separate, optional step, so a report can be produced and inspected
    before anything is written.

    `on_extracted`, if given, is called ONCE with the raw `extractions` list
    (one Extraction per task, block_id and all — see build_components()'s
    docstring for why that per-region granularity is lost once text regions
    are merged) right after extraction finishes and before it is merged into
    components. This is the ONE place this function's purity is bent on
    purpose: a DB-aware caller (scripts/evaluate_booklet.py,
    api/services/evaluation.py) passes persist_extractions bound to its own
    connection here to write migrations/019_answer_block_extractions.sql
    rows, without this module importing psycopg2 or knowing what a
    connection is. Exceptions raised by the callback are NOT caught here —
    persist_extractions() is written to never raise (see its own docstring),
    so a raise reaching this point is a bug in the callback, not a per-region
    failure this module's partial-failure posture is meant to absorb.
    """
    started_at = time.perf_counter()
    extractions = extract_tasks(tasks, max_concurrency=max_concurrency,
                                stub=stub, stub_extraction=stub_extraction)
    if on_extracted is not None:
        on_extracted(extractions)
    components = build_components(extractions)
    outcomes = evaluate_components(components, max_concurrency=max_concurrency,
                                   stub=stub, stub_llm=stub_llm,
                                   method=method, weights=weights)
    report = aggregate(tasks, extractions, outcomes,
                       review_threshold=review_threshold,
                       unassigned_regions=unassigned_regions,
                       booklet=booklet, started_at=started_at)
    report["run"] = {
        "max_concurrency": max_concurrency,
        "stub": stub, "stub_extraction": stub_extraction, "stub_llm": stub_llm,
        "method": method,
        "extraction_latency_ms": round(sum(e.latency_ms for e in extractions), 2),
        "evaluation_latency_ms": round(sum(o.latency_ms for o in outcomes), 2),
    }
    return report


# =========================================================================
# Persistence  (the first of two sections that need a database)
# =========================================================================

def _ledger_result(report: dict, row: dict):
    """Build the single EvaluationResult that represents one question.

    Everything the individual components produced is carried in metrics —
    per-region scores, per-signal breakdowns, extraction provenance, the
    failures that were excluded — so the aggregate row loses nothing that a
    per-region row would have held. metrics["plugin"] is this module rather
    than a scoring plugin, which is honest: the number in `score` came from a
    plugin, but WHICH number it is came from the aggregation rules here, and
    a row in an append-only ledger has to name whatever chose it (see
    core/plugins/base.REQUIRED_METRIC_KEYS).
    """
    from core.plugins.base import EvaluationResult

    primary = row["primary"]
    return EvaluationResult(
        score=row["score"],
        max_score=row["marks_max"],
        confidence=row["confidence"],
        explanation=row["explanation"],
        metrics={
            "plugin": BOOKLET_EVALUATOR_NAME,
            "plugin_version": BOOKLET_EVALUATOR_VERSION,
            "evaluator_model": f"booklet({primary['plugin']}:{primary['evaluator_model']})",
            "latency_ms": round(sum(c["latency_ms"] for c in row["components"]), 2),
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "booklet": report.get("booklet", {}),
            "question": row["question"],
            "confidence_detail": {"question": row["confidence"],
                                  "booklet": report["confidence"]["booklet"]},
            "flags": row["flags"],
            "regions": row["regions"],
            "pages": row["pages"],
            "components": row["components"],
            "failures": row["failures"],
            "run": report.get("run", {}),
        },
    )


def persist_extractions(conn, extractions: list, *, dry_run: bool = False) -> dict:
    """Appends one answer_block_extractions row per successfully-extracted,
    persisted region — every Extraction whose task carries a block_id (an
    offline run's tasks have none, since nothing was ever persisted) and
    whose extract() succeeded (extraction.ok).

    THE TEXT COMES FROM extraction.result.content, PER TASK — deliberately
    BEFORE build_components() merges a question's text regions into one
    Component. That merge is what §7D decision 3 needs for SCORING (a
    paragraph split across two pages is one answer, not two), but it is also
    exactly what throws away which words came from which region — merging
    first and writing per-region rows after would mean inventing a split
    that was never actually read that way. So this is called from
    evaluate_booklet()'s `on_extracted` hook, straight off extract_tasks()'s
    output, at the one point every region's own, unmerged text still exists.

    The write itself goes through core/answer_evaluation.record_extraction(),
    the same is_current-flip-then-INSERT shape record_evaluation() already
    uses for evaluation_results (see that function's docstring) — mirrored
    rather than reinvented, per migrations/019_answer_block_extractions.sql's
    own header.

    ONE TRANSACTION FOR THE WHOLE BATCH — a SAVEPOINT per region, not a
    COMMIT per region (contrast persist_question_results below, which commits
    per QUESTION). The caller sets this connection's RLS GUC ONCE, as the
    first statement of its current transaction (`SET LOCAL
    app.is_platform_admin = 'true'` for this system job — the same contract
    core/booklet_persist.py and persist_question_results document), and SET
    LOCAL lasts only until that transaction's next commit or rollback.
    Committing per region here would drop that context after the FIRST row
    and then fail every subsequent one — invisibly, since RLS fails closed
    and silently (CLAUDE_CONTEXT.md §6) — which is exactly the trap
    scripts/evaluate_pending.py avoids by re-issuing SET LOCAL inside ITS
    per-row loop. Extraction rows have no reason to be split across
    transactions the way question results do (there is no per-question
    ledger semantics here, just N independent region writes), so a SAVEPOINT
    per region gives the identical "one bad region does not cost the others"
    guarantee without paying for that RLS re-establishment. Callers that also
    call persist_question_results on the SAME connection afterward must
    re-issue the RLS GUC first — this function's own commit (or rollback, if
    dry_run) ends the transaction it was set on.

    Never raises: a region whose INSERT fails rolls back to its own
    SAVEPOINT and is recorded in the returned failures list, exactly the
    posture extract_tasks/evaluate_components already take for their own
    per-region failures.

    TEXT ONLY — a diagram/table region's extraction.result.content is a
    structured dict (a graph or a cell grid, core/diagram_extractor.py /
    core/table_extractor.py), not a string, and `text` is a TEXT column: an
    unconditional write here would either crash (psycopg2 cannot adapt a
    dict) or, worse, silently stringify a graph into something that reads
    like OCR text but isn't. This table and the Results screen it feeds are
    about what was READ off a region, which for a diagram/table region isn't
    "text" in that sense at all (RegionCard.tsx shows their structural
    comparison instead — see core/answer_regions.py). So a non-str, non-None
    content is `skipped`, not `failed`: it is a legitimate kind of region
    this table simply doesn't describe, not a write that went wrong.
    """
    from core import answer_evaluation

    written, skipped, failed = 0, 0, 0
    failures: list = []

    cur = conn.cursor()
    try:
        for index, extraction in enumerate(extractions):
            task = extraction.task
            if not task.block_id or not extraction.ok:
                skipped += 1
                continue

            result = extraction.result
            if result.content is not None and not isinstance(result.content, str):
                skipped += 1
                continue
            metrics = result.metrics or {}
            savepoint = f"sp_extraction_{index}"
            cur.execute(f"SAVEPOINT {savepoint}")
            try:
                answer_evaluation.record_extraction(
                    cur,
                    block_id=task.block_id,
                    text=result.content,
                    ocr_confidence=(float(result.confidence)
                                    if result.confidence is not None else None),
                    plugin=extraction.plugin_name or "unknown",
                    plugin_version=extraction.plugin_version,
                    mode=metrics.get("mode"),
                    engines=metrics.get("engines"),
                )
            except Exception as exc:            # noqa: BLE001 — one region must not abort the batch
                cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                failed += 1
                failures.append(_failure("persist_extraction", exc, task=task))
            else:
                cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                written += 1
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if dry_run:
        conn.rollback()
    else:
        conn.commit()

    return {"written": written, "skipped": skipped, "failed": failed,
            "dry_run": dry_run, "failures": failures}


def persist_question_results(conn, report: dict, *, dry_run: bool = False) -> dict:
    """Append one evaluation_results row per scored question. Mutates
    `report` in place, adding a "persistence" block to every question row and
    a "persistence" summary to the report.

    ONE ROW PER QUESTION, not per region — decision 1 in the module
    docstring. The write itself goes through
    core/plugins/persistence.write_evaluation_result(), which owns the
    validation (migration 012's reference XOR, the required metric keys) and
    the commit/rollback including the dry_run rollback, exactly as
    scripts/evaluate_table_answer.py does.

    The caller owns the connection and this is always a system job (§6),
    never a tenant request, exactly like scripts/evaluate_pending.py and
    scripts/evaluate_pending_diagrams.py.

    RE-ISSUES `SET LOCAL app.is_platform_admin = 'true'` AT THE TOP OF EVERY
    QUESTION'S ITERATION, not just once for the caller's first statement.
    Reason: write_evaluation_result() commits (or rolls back, for dry_run —
    and a failed write also rolls back, inside its own except block) PER
    QUESTION, and SET LOCAL lasts only until that transaction's next commit
    or rollback. A caller that set it once before the loop and expected it to
    survive N commits would find question 2 onward silently RLS-invisible —
    exactly the trap scripts/evaluate_pending.py's own per-row loop already
    re-issues SET LOCAL to avoid (see that script), reproduced here 2026-09-07
    once a second write (persist_extractions(), migrations/019) ahead of this
    loop made a previously rare "only one question in the report" case common
    enough to actually observe the first question fail too. RLS still fails
    closed and SILENTLY otherwise (CLAUDE_CONTEXT.md §6) — a booklet whose
    caller runs as anything other than platform admin must not call this.

    Commits PER QUESTION, the same granularity scripts/evaluate_pending.py
    and scripts/evaluate_pending_diagrams.py commit at: a booklet is not one
    atomic result, and one question's write failing must leave the other
    nineteen persisted rather than rolling the whole exam back.
    """
    from core import answer_evaluation
    from core.plugins import persistence

    written, skipped, failed = 0, 0, 0

    for row in report["questions"]:
        outcome = {"evaluation_id": None, "written": False, "skip_reason": None}
        row["persistence"] = {**row.get("persistence", {}), **outcome}
        target = row.get("persistence", {})

        if not row["scored"]:
            target["skip_reason"] = "not scored — see this question's failures"
            skipped += 1
            continue
        if target.get("reference_kind") not in ("variant", "asset"):
            target["skip_reason"] = (
                f"reference_kind={target.get('reference_kind')!r} is not a persistable "
                f"reference (evaluation_results is polymorphic over a reference "
                f"variant XOR asset — migration 012)")
            skipped += 1
            continue
        if not target.get("primary_block_id") or not target.get("answer_id"):
            target["skip_reason"] = (
                "region was never persisted as an answer_blocks row, so there is no "
                "answer to attach a score to (offline run, or an unassigned region)")
            skipped += 1
            continue

        try:
            cur = conn.cursor()
            try:
                # See the docstring: this MUST be re-issued every iteration,
                # not just relied on from before the loop — the previous
                # question's write (or this one's own retry) may have
                # already committed or rolled back, ending the transaction
                # SET LOCAL was set on.
                cur.execute("SET LOCAL app.is_platform_admin = 'true'")
                cur.execute("SELECT status FROM answers WHERE answer_id = %s",
                            (target["answer_id"],))
                answer_row = cur.fetchone()
                if answer_row is None:
                    raise persistence.RLSVisibilityError(
                        f"answer {target['answer_id']!r} returned zero rows — either the "
                        f"id is wrong or this transaction has no tenant context (RLS "
                        f"fails closed and silently, CLAUDE_CONTEXT.md §6)."
                    )
                # Issued BEFORE the ledger write, on the same transaction,
                # because write_evaluation_result() owns the commit: the
                # status change and the score land together or not at all.
                target["status_changed"] = answer_evaluation.transition_to_ai_scored(
                    cur, target["answer_id"], answer_row[0])
            finally:
                cur.close()

            reference_kwargs = (
                {"reference_answer_variant_id": target["reference_id"]}
                if target["reference_kind"] == "variant"
                else {"reference_asset_id": target["reference_id"]}
            )
            target["evaluation_id"] = persistence.write_evaluation_result(
                conn,
                answer_block_id=target["primary_block_id"],
                result=_ledger_result(report, row),
                dry_run=dry_run,
                **reference_kwargs,
            )
            target["written"] = True
            written += 1
        except Exception as exc:            # noqa: BLE001 — one question must not abort the booklet
            conn.rollback()
            failure = _failure("persist", exc, question=row["question"],
                               block_id=target.get("primary_block_id"),
                               key=target.get("primary_block_id"),
                               block_type=row["primary"]["block_type"],
                               page_number=row["pages"][0] if row["pages"] else None)
            row["failures"].append(failure)
            report["failures"].append(failure)
            target["skip_reason"] = f"{type(exc).__name__}: {exc}"
            failed += 1

    report["persistence"] = {
        "written": written, "skipped": skipped, "failed": failed, "dry_run": dry_run,
    }
    return report["persistence"]


# =========================================================================
# DB loading  (the only DB-READING section — everything above is pure)
# =========================================================================

def load_question_labels(cur, paper_id: str) -> dict:
    """question_id -> slot_label ('Q1', 'Q2a') for one generated paper.

    The inverse of core/booklet_persist.resolve_marker_to_question(), and it
    exists for the same schema reason: answer_blocks records WHERE a region
    came from but not which marker put it there, and `exams` has no paper_id
    column to recover the paper from (CLAUDE_CONTEXT.md §7C's open schema
    gap), so the paper has to be passed in. Without it the report falls back
    to a short question-id label, which is correct but harder to read against
    the physical booklet.
    """
    cur.execute(
        """
        SELECT pq.question_id, sl.slot_label
          FROM paper_questions pq
          JOIN paper_sections  ps ON ps.paper_section_id = pq.paper_section_id
          JOIN pattern_slots   sl ON sl.slot_id          = pq.slot_id
         WHERE ps.paper_id = %s
        """,
        (paper_id,),
    )
    return {str(question_id): label for question_id, label in cur.fetchall()}


def _load_keywords(cur, question_id: str) -> list:
    """Same join scripts/evaluate_answer.py uses — the question's weighted
    keywords, for the text plugin's keyword-coverage signal."""
    cur.execute(
        """
        SELECT k.term, qk.weight
          FROM question_keywords qk
          JOIN keywords k ON k.keyword_id = qk.keyword_id
         WHERE qk.question_id = %s
        """,
        (question_id,),
    )
    return [{"term": term, "weight": float(weight)} for term, weight in cur.fetchall()]


def _load_text_reference(cur, question_id: str, marks_max: float):
    """The current reference answer variant for a question, as a
    TextReference. Returns (reference, reference_id, error)."""
    from core.plugins.text_extraction import TextReference

    cur.execute(
        """
        SELECT variant_id, content
          FROM reference_answer_variants
         WHERE question_id = %s AND is_current = TRUE
        """,
        (question_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None, None, (
            f"question {question_id} has no current reference_answer_variants row — "
            f"nothing to score a text answer against")
    variant_id, content = row
    return (
        TextReference(reference_text=content, marks_max=marks_max,
                      keywords=_load_keywords(cur, question_id),
                      question_id=str(question_id), variant_id=str(variant_id)),
        str(variant_id),
        None,
    )


def _load_asset_reference(cur, question_id: str, block_type: str, marks_max: float,
                          glossary: list):
    """The reference content_assets row for a table or diagram question, as
    the matching plugin's reference dataclass. Returns
    (reference, reference_id, error).

    AMBIGUITY IS AN ERROR, NOT A CHOICE — two linked assets of the same type
    means "which one is the reference" is a content decision nobody recorded,
    and picking one would be a guess with a mark attached. Same refusal
    scripts/evaluate_pending_diagrams.py makes.
    """
    cur.execute(
        """
        SELECT ca.asset_id, ca.structured_data
          FROM question_asset_links qal
          JOIN content_assets ca ON ca.asset_id = qal.asset_id
         WHERE qal.question_id = %s
           AND qal.role        = 'question_source'
           AND ca.asset_type   = %s
           AND ca.structured_data IS NOT NULL
         ORDER BY ca.asset_id
        """,
        (question_id, block_type),
    )
    rows = cur.fetchall()
    if not rows:
        return None, None, (
            f"question {question_id} has no {block_type} asset linked via "
            f"question_asset_links(role='question_source') — nothing to score against")
    if len(rows) > 1:
        return None, None, (
            f"question {question_id} has {len(rows)} linked {block_type} assets; which "
            f"one is the reference is a content decision, not something to guess at")

    asset_id, structured_data = rows[0]
    reference = block_evaluation.asset_reference(
        block_type, structured_data, marks_max=marks_max,
        asset_id=str(asset_id), glossary=glossary)
    return reference, str(asset_id), None


def load_booklet_tasks(cur, *, student_id: str | None = None, exam_id: str | None = None,
                       answer_ids: list | None = None, source_scan_url: str | None = None,
                       paper_id: str | None = None) -> list:
    """Load one booklet's persisted regions as BlockTasks, references included.

    "A booklet id" does not exist in this schema — there is no booklets table
    and no exams.paper_id column (CLAUDE_CONTEXT.md §7C), so a booklet is
    identified the only way the data allows: the answers a student wrote for
    one exam, optionally narrowed to a single source_scan_url when the same
    student's booklet has been ingested more than once. `answer_ids` is the
    escape hatch for scoring an explicit subset.

    Every question's reference is loaded ONCE and cached across its regions.
    A question with no usable reference still produces tasks, carrying
    `reference_error` — so it appears in the report as an explicit failure
    rather than as a question that quietly went missing.
    """
    filters, params = [], []
    if student_id and exam_id:
        filters.append("a.student_id = %s AND a.exam_id = %s")
        params += [student_id, exam_id]
    if answer_ids:
        filters.append("a.answer_id = ANY(%s::uuid[])")
        params.append(list(answer_ids))
    if source_scan_url:
        filters.append("a.source_scan_url = %s")
        params.append(source_scan_url)
    if not filters:
        raise ValueError(
            "load_booklet_tasks needs --student-id with --exam-id, or --answer-id: "
            "without a filter this would score every answer in the database.")

    cur.execute(
        f"""
        SELECT ab.block_id, ab.answer_id, a.question_id, ab.block_type, ab.blob_url,
               ab.content, ab.page_number, ab.region_bbox, ab.classification_label,
               ab.classification_confidence, ab.needs_review, ab.sequence_order,
               q.marks_max, a.source_scan_url
          FROM answers       a
          JOIN answer_blocks ab ON ab.answer_id   = a.answer_id
          JOIN questions     q  ON q.question_id  = a.question_id
         WHERE {' AND '.join(filters)}
         ORDER BY ab.page_number NULLS LAST, ab.sequence_order, ab.block_id
        """,
        params,
    )
    rows = cur.fetchall()

    labels = load_question_labels(cur, paper_id) if paper_id else {}
    glossary_cache: list | None = None
    reference_cache: dict = {}
    tasks = []

    for (block_id, answer_id, question_id, block_type, blob_url, content, page_number,
         region_bbox, classification_label, classification_confidence, needs_review,
         sequence_order, marks_max, _scan_url) in rows:
        question_id = str(question_id)
        marks_max = float(marks_max) if marks_max is not None else 0.0
        cache_key = (question_id, block_type)

        if cache_key not in reference_cache:
            if block_type == "text":
                reference_cache[cache_key] = _load_text_reference(cur, question_id, marks_max)
            elif block_type in ("table", "diagram"):
                if block_type == "diagram" and glossary_cache is None:
                    # Unscoped by topic: scripts/evaluate_diagram_answer.py takes
                    # an optional --topic-id, but a booklet spans a whole paper
                    # and therefore several topics, so scoping it here would be
                    # the wrong default.
                    glossary_cache = block_evaluation.load_glossary(cur)
                reference_cache[cache_key] = _load_asset_reference(
                    cur, question_id, block_type, marks_max, glossary_cache or [])
            else:
                reference_cache[cache_key] = (None, None, (
                    f"block_type={block_type!r} has no reference source in this schema "
                    f"(and no plugin registers for it) — flagged for human review"))
        reference, reference_id, reference_error = reference_cache[cache_key]

        # A str is ALWAYS a blob_url in this repo (CLAUDE_CONTEXT.md §10), so
        # already-digital text has to be wrapped — the same convention
        # core/plugins/text_extraction.py's extract() documents.
        if block_type == "text" and content:
            from core.plugins.text_extraction import PlainText
            source = PlainText(content)
        else:
            source = blob_url

        tasks.append(BlockTask(
            question_label=labels.get(question_id, f"question:{question_id[:8]}"),
            block_type=block_type,
            source=source,
            reference=reference,
            reference_kind=("variant" if block_type == "text" else "asset") if reference else None,
            reference_id=reference_id,
            reference_error=reference_error,
            marks_max=marks_max,
            block_id=str(block_id),
            answer_id=str(answer_id),
            question_id=question_id,
            page_number=page_number,
            bbox=region_bbox,
            sequence_order=int(sequence_order or 0),
            classification_label=classification_label,
            classification_confidence=(float(classification_confidence)
                                       if classification_confidence is not None else None),
            needs_review=bool(needs_review),
            review_reasons=["needs_review_at_ingestion"] if needs_review else [],
        ))

    return tasks
