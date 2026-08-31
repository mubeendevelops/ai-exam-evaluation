"""
core/plugins/base.py — the interface every evaluation module implements.

Same shape, one level up, as core/ocr_engines/base.py: core/plugins/registry.py
drives plugins dynamically through this interface and doesn't know or care
whether a given plugin reads handwriting, parses a table, or compares a graph
— it only asks supports(block_type) and then calls extract()/evaluate().
Adding a new evaluation module means writing one class here-conformant in
core/plugins/ and decorating it with @register; nothing else changes.

The two-phase split (extract, then evaluate) is deliberate and mirrors how
the diagram pipeline already works (core/diagram_extractor.extract_diagram_structure()
then core/diagram_evaluator.compare_diagrams()):

  - extract() turns a raw source (an image region, a scanned blob_url, a
    text answer) into a structured, plugin-specific representation. It is
    the expensive, model-loading half.
  - evaluate() compares that representation against a reference and produces
    a score. It is the half whose output lands in the append-only
    evaluation_results ledger (see core/plugins/persistence.py).

Keeping them apart means an extraction can be cached, re-scored against a
different reference, or inspected on its own — and it's what makes the
`stub=True` path useful in tests and in every script's --stub flag
(CLAUDE_CONTEXT.md §5 rule 6).
"""
from __future__ import annotations

import abc
import dataclasses
from typing import Any


@dataclasses.dataclass(frozen=True)
class ExtractionResult:
    """What a plugin read out of a source, before any scoring happens.

    Frozen because an extraction is a record of what was observed — a
    re-read produces a NEW result rather than mutating an old one, the same
    append-don't-overwrite discipline the DB layer follows
    (CLAUDE_CONTEXT.md §5 rule 2).
    """

    #: Plugin-specific payload. text_extraction → str; a table plugin →
    #: a row/column grid; a diagram plugin → the {"nodes", "edges"} dict
    #: documented in core/diagram_extractor.py. Callers must not introspect
    #: this without knowing which plugin produced it.
    content: Any

    #: How much the plugin trusts this extraction, 0.0-1.0. NOT calibrated
    #: across plugins — the same caveat core/ocr_engines/base.py carries for
    #: per-engine confidence applies here per-plugin.
    confidence: float

    #: Free-form structured detail about HOW the extraction went (engine
    #: used, region count, retries, ...). Merged into the evaluation's
    #: metrics on the way to evaluation_results.metrics JSONB (migration
    #: 010), so everything in here must be JSON-serializable.
    metrics: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class EvaluationResult:
    """One score produced by one plugin — the in-memory form of exactly one
    evaluation_results row (core/plugins/persistence.py writes it)."""

    score: float
    max_score: float
    confidence: float
    explanation: str

    #: Goes verbatim into evaluation_results.metrics (JSONB, migration 010),
    #: so every value must be JSON-serializable. Required keys, enforced by
    #: persistence.write_evaluation_result():
    #:   "plugin"          — plugin.name
    #:   "plugin_version"  — plugin.version
    #:   "evaluator_model" — the model/method that produced the score, also
    #:                       written to the evaluator_model column (migration 009)
    #:   "latency_ms"      — wall-clock time of the scoring call
    #: Plus, for any plugin scoring through core/llm.py, the token usage
    #: returned by _generate(..., return_metrics=True) (CLAUDE_CONTEXT.md §9).
    metrics: dict = dataclasses.field(default_factory=dict)


#: Keys every EvaluationResult.metrics must carry. Checked at write time
#: rather than at construction time so a plugin can build its metrics dict
#: incrementally, but never silently persist a row that can't be traced back
#: to the plugin version that produced it.
REQUIRED_METRIC_KEYS = ("plugin", "plugin_version", "evaluator_model", "latency_ms")


class EvaluationPlugin(abc.ABC):
    """One pluggable evaluation module.

    A plugin is stateful only in that it may lazily load its own models on
    first use and cache them (see core/ocr_engines/base.py's identical
    note) — the registry holds a single instance per plugin and reuses it,
    so __init__ must stay cheap and must not import or load anything heavy.
    """

    #: Short, stable identifier, unique across registered plugins. Used in
    #: logs, in evaluation_results.metrics["plugin"], and as the key for
    #: get_plugin(). e.g. "text_extraction".
    name: str

    #: Semver. Bump it whenever SCORING BEHAVIOR changes, so two rows in the
    #: append-only ledger scored months apart can be told apart: MAJOR for an
    #: incompatible content/metrics shape, MINOR for a scoring change that
    #: moves numbers, PATCH for fixes that don't. Stored in
    #: evaluation_results.metrics["plugin_version"].
    version: str

    @abc.abstractmethod
    def supports(self, block_type: str) -> bool:
        """Whether this plugin can handle an answer_blocks.block_type value
        ('text' | 'diagram' | 'table' | 'formula', migration 001). More than
        one plugin may claim the same block_type — registry.plugins_for()
        returns all of them and the caller chooses."""

    @abc.abstractmethod
    def extract(self, source, *, stub: bool = False) -> ExtractionResult:
        """Turns one raw source into a structured ExtractionResult. What
        `source` may be is plugin-specific and documented on each plugin.

        stub=True must return a deterministic fake WITHOUT loading a model,
        hitting storage, or calling an API — this is what backs every
        script's --stub/--stub-extraction flag (CLAUDE_CONTEXT.md §5 rule 6),
        and it must stay usable on test rows whose blob_url is a
        dummy-storage placeholder with no real bytes behind it."""

    @abc.abstractmethod
    def evaluate(self, extracted: ExtractionResult, reference, *,
                 stub: bool = False) -> EvaluationResult:
        """Scores an extraction against a reference. `reference` is
        plugin-specific: a reference_answer_variants text, a content_assets
        structured_data graph, etc.

        stub=True must return a deterministic fake score without calling an
        LLM or loading an embedding model."""
