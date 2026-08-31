"""
core/plugins/diagram_evaluation.py — diagram extraction + scoring as an
EvaluationPlugin, registered for block_type='diagram'.

A THIN WRAPPER, exactly like core/plugins/table_extraction.py is over
core/table_extractor.py + core/table_evaluator.py: the graph extraction
lives in core/diagram_extractor.py (OCR labels via core/ocr_fallback.py,
shape/edge geometry via core/diagram_shapes.py) and the comparison logic in
core/diagram_evaluator.py, both still usable directly (and unit-tested
directly, tests/test_diagram_evaluator.py) without the plugin layer. This
module only gives them the uniform front door core/plugins/registry.py
drives.

WHAT THIS MODULE OWNS, that neither core module should know about:
  - the shape of evaluation_results.metrics for a diagram row;
  - the one-line explanation stored in evaluation_results.explanation;
  - the OPTIONAL LLM anomaly-explanation pass (see below).

WHAT IT DELIBERATELY DOES NOT OWN: the score. Unlike
core/plugins/table_extraction.py, which multiplies a fraction by marks_max
here, core/diagram_evaluator.compare_diagrams() already takes marks_max and
returns an absolute `similarity_score`. That asymmetry is pre-existing
behaviour, not an oversight of this wrapper — wrapping it must not move a
number, so evaluate() passes marks_max through and reports the score it is
given.

METRICS SHAPE — the comparison dict is merged FLAT into
EvaluationResult.metrics, rather than nested under a "comparison" key the
way core/plugins/table_extraction.py nests its own. That is deliberate:
scripts/evaluate_diagram_answer.py has been storing the bare comparison
dict as evaluation_results.metrics since before plugins existed, and
evaluation_results is an append-only ledger (CLAUDE_CONTEXT.md §5 rule 2) —
rows scored before and after this wrapper must stay directly comparable by
the same JSON path, or the history the ledger exists to keep becomes
un-queryable at the exact commit that introduced the plugin. The four
core/plugins/base.REQUIRED_METRIC_KEYS plus "extraction" (and
"explanation_pass", when --explain ran) are added alongside it; none of
them collides with a key compare_diagrams() returns.

THE LLM PASS IS EXPLANATION-ONLY (plan.md §7 phase 2). explain=True adds a
teacher-readable narrative of an ALREADY-COMPUTED comparison; it cannot and
does not change EvaluationResult.score. Scoring stays NLP-only, so a diagram
re-scored tomorrow gets the same number it got today — see
core/diagram_evaluator.explain_comparison() for the full argument.
"""
from __future__ import annotations

import dataclasses
import time

from core import diagram_evaluator, diagram_extractor
from core.plugins.base import EvaluationPlugin, EvaluationResult, ExtractionResult
from core.plugins.registry import register


@dataclasses.dataclass(frozen=True)
class DiagramReference:
    """Everything evaluate() needs about the expected diagram — assembled by
    the caller (a script) from the DB, so evaluate() itself does no DB I/O.
    Same division of responsibility as
    core/plugins/table_extraction.TableReference and
    core/plugins/text_extraction.TextReference.

    `graph` is the reference diagram in core/diagram_extractor.py's
    documented {"nodes", "edges"} schema, as loaded from
    content_assets.structured_data by scripts/load_reference_diagram.py.

    `glossary_terms` is the glossary_terms rows the caller pre-loaded
    ([{"term_id", "canonical_term", "aliases"}, ...]) — passed straight
    through to compare_diagrams(), which has always taken them as a plain
    argument rather than querying for them itself.
    """

    graph: dict
    marks_max: float
    glossary_terms: list = dataclasses.field(default_factory=list)
    asset_id: str | None = None


@register
class DiagramEvaluationPlugin(EvaluationPlugin):
    """Extracts a graph from a diagram scan and scores it against a
    reference graph. See the module docstring for what is wrapped versus
    owned here."""

    name = "diagram_evaluation"
    version = "0.1.0"

    def supports(self, block_type: str) -> bool:
        """Handles answer_blocks.block_type='diagram'. 'text' belongs to
        core/plugins/text_extraction.py, which shares the same OCR stack but
        produces a string rather than a graph."""
        return block_type == "diagram"

    def extract(self, source, *, stub: bool = False) -> ExtractionResult:
        """`source` is a blob_url str (this repo's stable "bucket/key"
        form) or an already-open PIL Image — the same str-means-blob_url
        convention core/plugins/text_extraction.py and
        core/plugins/table_extraction.py use.

        ExtractionResult.content is the full {"nodes", "edges"} graph;
        .confidence is the mean of the nodes' own OCR confidences (nodes
        that reported none — stub nodes, and the "[unreadable]" placeholders
        core/diagram_extractor.py keeps deliberately — are excluded from
        that mean and counted separately in .metrics, since an unread label
        says nothing about how well the labels that WERE read were read).
        """
        started = time.perf_counter()

        if stub:
            graph = diagram_extractor.stub_extract(source)
            return ExtractionResult(
                content=graph, confidence=1.0,
                metrics=self._extraction_metrics(started, graph, mode="stub"),
            )

        graph = diagram_extractor.extract_diagram_structure(source)
        confidences = [n["confidence"] for n in graph["nodes"] if n.get("confidence")]
        return ExtractionResult(
            content=graph,
            confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            metrics=self._extraction_metrics(started, graph, mode="real"),
        )

    def _extraction_metrics(self, started, graph: dict, *, mode: str) -> dict:
        """Extraction-side metrics, merged into the evaluation's metrics on
        the way to evaluation_results.metrics JSONB (migration 010) — every
        value stays JSON-serializable.

        "engines" is the DISTINCT set of OCR engines that won at least one
        node label; the per-node winner is already on each node of the graph
        itself, so repeating it per node here would duplicate a whole column
        of the payload for no gain. Same provenance argument as
        core/plugins/table_extraction.py's identically-named key: it is what
        lets a bad score later be blamed on a specific engine rather than on
        the comparison rules."""
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        return {
            "plugin": self.name,
            "plugin_version": self.version,
            "mode": mode,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "unreadable_node_count": sum(1 for n in nodes if n.get("label") == "[unreadable]"),
            "needs_review_edge_count": sum(1 for e in edges if e.get("needs_review")),
            "shape_types": sorted({n["shape_type"] for n in nodes if n.get("shape_type")}),
            "engines": sorted({n["ocr_engine"] for n in nodes if n.get("ocr_engine")}),
            "extraction_warnings": graph.get("extraction_warnings", []),
        }

    def evaluate(self, extracted: ExtractionResult, reference: DiagramReference, *,
                 stub: bool = False, explain: bool = False,
                 stub_llm: bool = False) -> EvaluationResult:
        """Scores an extracted graph against `reference` (a DiagramReference).

        stub=True returns a deterministic fake without loading the embedding
        model, per the base-class contract.

        explain=True runs the OPTIONAL second pass
        (core/diagram_evaluator.explain_comparison) that asks the LLM to turn
        the structured comparison into a teacher-readable paragraph. It
        NEVER touches the score — the returned EvaluationResult.score is
        byte-identical with and without it. stub_llm=True fakes that pass
        without spending Groq quota, the same --stub-llm convention every
        other LLM-calling path in this repo uses.
        """
        started = time.perf_counter()
        student_graph = extracted.content or {}
        marks_max = reference.marks_max

        if stub:
            comparison = diagram_evaluator.stub_compare(
                student_graph, reference.graph, marks_max)
        else:
            comparison = diagram_evaluator.compare_diagrams(
                student_graph, reference.graph, reference.glossary_terms, marks_max)

        # The score is fixed HERE, before the LLM is ever consulted. Nothing
        # below may reassign it.
        score = comparison["similarity_score"]
        explanation = self._explanation(comparison)

        metrics = dict(comparison)
        metrics.update({
            "plugin": self.name,
            "plugin_version": self.version,
            "evaluator_model": comparison["model"]["matching_method"],
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "extraction": extracted.metrics,
        })

        if explain:
            narrative = diagram_evaluator.explain_comparison(
                comparison, stub=stub or stub_llm)
            metrics["explanation_pass"] = narrative
            explanation = self._with_narrative(explanation, narrative)

        return EvaluationResult(
            score=score, max_score=marks_max, confidence=extracted.confidence,
            explanation=explanation, metrics=metrics,
        )

    def _explanation(self, comparison: dict) -> str:
        """One-line human summary, stored in evaluation_results.explanation.
        Character-for-character the string
        scripts/evaluate_diagram_answer.py built inline before this plugin
        existed — an append-only ledger whose explanation column changes
        wording at a refactor makes two rows look like two different
        evaluations when only the plumbing moved."""
        return (
            f"Diagram comparison: {len(comparison.get('missing_information', []))} "
            f"missing item(s), {len(comparison.get('anomalies', []))} anomaly/anomalies."
        )

    def _with_narrative(self, explanation: str, narrative: dict) -> str:
        """Appends the LLM pass's paragraph to the deterministic summary,
        clearly attributed and clearly delimited.

        Appended rather than substituted: the deterministic sentence is what
        two rows of the ledger are compared on, and it must stay the leading
        text whether or not anyone opted into --explain. The narrative is
        additive detail for a human, and is also stored structurally in
        metrics["explanation_pass"] for anything that would rather read it
        from there."""
        return (
            f"{explanation}\n\n"
            f"[LLM explanation, severity={narrative['severity']}] "
            f"{narrative['explanation']}"
        )
