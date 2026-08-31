"""
core/plugins/table_extraction.py — table extraction + scoring as an
EvaluationPlugin, registered for block_type='table'.

A THIN WRAPPER, exactly like core/plugins/text_extraction.py is over
core/ocr_fallback.py: the grid detection lives in core/table_extractor.py
and the alignment/verdict logic in core/table_evaluator.py, both usable
directly (and unit-tested directly) without the plugin layer. This module
only gives them the uniform front door core/plugins/registry.py drives, and
owns the two things neither core module should know about: how a score maps
onto a question's marks, and what goes in
evaluation_results.metrics.

The two-phase split lands naturally here (core/plugins/base.py's rationale):
extract() is the expensive half (fetches the scan, runs the OCR ensemble
over every cell); evaluate() is pure computation over two dicts, so a stored
extraction can be re-scored against a different reference without touching
the image again.

SCORING: core/table_evaluator.compare_tables() returns fractions on purpose
(see its module docstring); the single line that turns
`overall_accuracy` into marks lives here, in evaluate(). That is also why
this plugin can report structural_score and content_score in its metrics
without either core module ever needing a marks_max argument.
"""
from __future__ import annotations

import dataclasses
import time

from core import table_evaluator, table_extractor
from core.plugins.base import EvaluationPlugin, EvaluationResult, ExtractionResult
from core.plugins.registry import register

#: Reported as evaluator_model (migration 009) and stored in the ledger.
#: Names the METHOD, not a model file — there is no model here, which is
#: the point (see core/table_evaluator.py: rules + difflib, no LLM, no
#: embeddings). Mirrors core/plugins/text_extraction.py's
#: KEYWORD_MODEL_NAME/RUBRIC_MODEL_NAME convention.
TABLE_MODEL_NAME = "table_rules_fuzzy_v1"

#: An evaluation whose comparison set needs_review has its reported
#: confidence capped here, however clean the OCR looked. A grid with
#: unread cells can be confidently read and still be the wrong grid.
NEEDS_REVIEW_CONFIDENCE_CAP = 0.5


@dataclasses.dataclass(frozen=True)
class TableReference:
    """Everything evaluate() needs about the expected table — assembled by
    the caller (a script) from the DB, so evaluate() itself does no DB I/O.
    Same division of responsibility as
    core/plugins/text_extraction.TextReference and
    core/diagram_evaluator.compare_diagrams()'s glossary_terms argument.

    `table` is the reference grid in core/table_extractor.py's documented
    schema, as loaded from content_assets.structured_data by
    scripts/load_reference_table.py.
    """

    table: dict
    marks_max: float
    asset_id: str | None = None


@register
class TableExtractionPlugin(EvaluationPlugin):
    """Extracts a table grid from a scan and scores it against a reference
    grid. See the module docstring for what is wrapped versus owned here."""

    name = "table_extraction"
    version = "0.1.0"

    def supports(self, block_type: str) -> bool:
        return block_type == "table"

    def extract(self, source, *, stub: bool = False) -> ExtractionResult:
        """`source` is either a blob_url str (this repo's stable
        "bucket/key" form — fetched through core/table_extractor.py, which
        delegates to core/diagram_extractor._load_image) or an already-open
        PIL Image. Same str-means-blob_url convention as
        core/plugins/text_extraction.py's extract().

        ExtractionResult.content is the full grid dict;
        .confidence is the mean of the non-empty cells' OCR confidences
        (empty cells are excluded from that mean and counted separately in
        .metrics, since an unread cell says nothing about how well the
        cells that WERE read were read).
        """
        started = time.perf_counter()

        if stub:
            table = table_extractor.stub_extract(source)
            return ExtractionResult(
                content=table, confidence=1.0,
                metrics=self._extraction_metrics(started, table, mode="stub"),
            )

        table = table_extractor.extract_table_structure(source)
        confidences = [c["confidence"] for c in table["cells"] if c["text"]]
        return ExtractionResult(
            content=table,
            confidence=round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            metrics=self._extraction_metrics(started, table, mode="real"),
        )

    def _extraction_metrics(self, started, table: dict, *, mode: str) -> dict:
        """Extraction-side metrics, merged into the evaluation's metrics on
        the way to evaluation_results.metrics JSONB (migration 010) — every
        value stays JSON-serializable.

        "engines" is the DISTINCT set of engines that won at least one cell
        — the per-cell winner is already on each cell of the grid itself
        (ExtractionResult.content), so repeating it here would duplicate a
        whole column of the payload for no gain. It carries the same
        provenance core/diagram_extractor.py stores per node and
        core/plugins/text_extraction.py stores per region: it is what lets a
        bad score later be blamed on a specific engine rather than on the
        scoring rules."""
        cells = table.get("cells", [])
        return {
            "plugin": self.name,
            "plugin_version": self.version,
            "mode": mode,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "detection_method": table.get("detection_method"),
            "rows": table.get("rows"),
            "cols": table.get("cols"),
            "has_header": table.get("has_header"),
            "cell_count": len(cells),
            "empty_cell_count": sum(1 for c in cells if not c["text"]),
            "engines": sorted({c["ocr_engine"] for c in cells if c.get("ocr_engine")}),
            "extraction_warnings": table.get("extraction_warnings", []),
        }

    def evaluate(self, extracted: ExtractionResult, reference: TableReference, *,
                 stub: bool = False) -> EvaluationResult:
        """Scores an extracted grid against `reference` (a TableReference).

        stub=True returns a deterministic fake without running alignment,
        per the base-class contract. There is no stub_llm counterpart and
        no --method switch: this plugin has exactly one scoring path,
        because it deliberately has no model to swap out
        (core/table_evaluator.py's module docstring).
        """
        started = time.perf_counter()
        student_table = extracted.content or {}
        marks_max = reference.marks_max

        if stub:
            comparison = table_evaluator.stub_compare(student_table, reference.table)
        else:
            comparison = table_evaluator.compare_tables(student_table, reference.table)

        score = round(comparison["overall_accuracy"] * marks_max, 2)

        confidence = extracted.confidence
        if comparison.get("needs_review"):
            confidence = min(confidence, NEEDS_REVIEW_CONFIDENCE_CAP)

        metrics = {
            "plugin": self.name,
            "plugin_version": self.version,
            "evaluator_model": TABLE_MODEL_NAME if not stub else "stub",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "extraction": extracted.metrics,
            "comparison": comparison,
        }

        return EvaluationResult(
            score=score, max_score=marks_max, confidence=confidence,
            explanation=self._explanation(comparison, score, marks_max),
            metrics=metrics,
        )

    def _explanation(self, comparison: dict, score: float, marks_max: float) -> str:
        """One-line human summary, stored in evaluation_results.explanation.
        Leads with the structural/content split because that is the part a
        teacher acts on — the same value shows up very differently depending
        on whether the shape or the values went wrong."""
        counts = comparison.get("verdict_counts", {})
        scored = sum(counts.values())
        matched = counts.get("exact", 0) + counts.get("fuzzy", 0) + counts.get("numeric_close", 0)
        parts = [
            f"Table comparison: {score}/{marks_max}",
            f"structural {comparison.get('structural_score')}",
            f"content {comparison.get('content_score')}",
            f"{matched}/{scored} cells matched",
        ]
        missing_rows = len(comparison.get("missing_rows", []))
        extra_rows = len(comparison.get("extra_rows", []))
        if missing_rows or extra_rows:
            parts.append(f"{missing_rows} missing row(s), {extra_rows} extra row(s)")
        summary = "; ".join(parts) + "."
        if comparison.get("needs_review"):
            summary += " FLAGGED FOR REVIEW: " + " ".join(comparison.get("review_reasons", []))
        return summary
