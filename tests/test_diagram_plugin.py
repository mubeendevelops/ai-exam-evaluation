"""Tests for core/plugins/diagram_evaluation.py, the LLM explanation pass in
core/diagram_evaluator.py, and the shape/connector additions in
core/diagram_shapes.py.

The plugin tests are CHARACTERIZATION tests in the strict sense: the diagram
pipeline worked before it was wrapped as a plugin, and what is pinned here
is that wrapping it moved nothing a stored evaluation_results row can see —
the score, the explanation string, and the metrics JSON path each field
lives at.
"""
from __future__ import annotations

import json

import numpy as np
import cv2
import pytest

from core import diagram_evaluator as de
from core import diagram_shapes as ds
from core.plugins import registry
from core.plugins.base import REQUIRED_METRIC_KEYS
from core.plugins.diagram_evaluation import DiagramReference

REFERENCE = {
    "schema_version": 1,
    "nodes": [{"node_id": "n1", "label": "CPU"}, {"node_id": "n2", "label": "Memory"}],
    "edges": [{"edge_id": "e1", "from_node": "n1", "to_node": "n2", "label": None}],
}


@pytest.fixture
def plugin():
    return registry.get_plugin("diagram_evaluation")


# ---------------------------------------------------------------------------
# registration / interface
# ---------------------------------------------------------------------------

def test_the_diagram_plugin_is_registered():
    assert "diagram_evaluation" in registry.list_plugins()
    plugin = registry.get_plugin("diagram_evaluation")
    assert plugin.name == "diagram_evaluation"
    assert plugin.version
    assert plugin.supports("diagram")
    assert not plugin.supports("text")
    assert not plugin.supports("table")


def test_plugins_for_diagram_returns_it(plugin):
    assert plugin in registry.plugins_for("diagram")


# ---------------------------------------------------------------------------
# extract / evaluate — stub paths
# ---------------------------------------------------------------------------

def test_stub_extract_returns_the_extractors_own_fake(plugin):
    result = plugin.extract("dummy-storage/whatever", stub=True)
    assert result.confidence == 1.0
    assert [n["label"] for n in result.content["nodes"]] == ["[STUB] CPU", "[STUB] Memory"]
    assert result.metrics["mode"] == "stub"
    assert result.metrics["node_count"] == 2
    assert result.metrics["edge_count"] == 1


def test_stub_evaluate_matches_stub_compare_exactly(plugin):
    extracted = plugin.extract("dummy-storage/x", stub=True)
    result = plugin.evaluate(extracted, DiagramReference(graph=REFERENCE, marks_max=10),
                             stub=True)
    assert result.score == de.stub_compare({}, REFERENCE, 10)["similarity_score"]
    assert result.max_score == 10


def test_metrics_carry_the_required_keys_and_a_flat_comparison(plugin):
    """The comparison dict is merged FLAT, not nested — rows written before
    the plugin existed store it that way and the ledger is append-only."""
    extracted = plugin.extract("dummy-storage/x", stub=True)
    result = plugin.evaluate(extracted, DiagramReference(graph=REFERENCE, marks_max=10),
                             stub=True)

    for key in REQUIRED_METRIC_KEYS:
        assert key in result.metrics, key
    # Straight out of compare_diagrams()/stub_compare(), at the top level.
    for key in ("similarity_score", "node_validation", "edge_comparison",
                "missing_information", "anomalies", "glossary_matches",
                "scores_breakdown", "model"):
        assert key in result.metrics, key
    assert result.metrics["evaluator_model"] == result.metrics["model"]["matching_method"]
    assert result.metrics["extraction"]["mode"] == "stub"
    # Everything persisted must survive a JSONB round trip (migration 010).
    json.dumps(result.metrics)


def test_explanation_string_is_unchanged_from_the_pre_plugin_script(plugin):
    """Pinned character-for-character: evaluation_results.explanation is what
    two rows of the ledger get compared on."""
    extracted = plugin.extract("dummy-storage/x", stub=True)
    result = plugin.evaluate(extracted, DiagramReference(graph=REFERENCE, marks_max=10),
                             stub=True)
    assert result.explanation == "Diagram comparison: 0 missing item(s), 0 anomaly/anomalies."


# ---------------------------------------------------------------------------
# the LLM explanation pass — must never touch the score
# ---------------------------------------------------------------------------

COMPARISON = {
    "similarity_score": 4.2,
    "marks_max": 10,
    "node_validation": [
        {"reference_label": "CPU", "matched_label": "CPV", "status": "matched",
         "similarity": 0.9},
        {"reference_label": "Memory", "matched_label": None, "status": "missing"},
    ],
    "edge_comparison": [{"from": "n1", "to": "n2", "status": "missing"}],
    "missing_information": ["Node 'Memory' not found in student diagram"],
    "anomalies": ["Extra node 'xyz' with no reference counterpart"],
    "glossary_matches": [],
    "scores_breakdown": {"node_f1": 0.5, "edge_f1": 0.0},
    "model": {"matching_method": "embeddings"},
}


def test_explain_comparison_parses_the_json_contract(stub_llm):
    stub_llm.text = '{"explanation": "The student missed Memory.", "severity": "major"}'
    result = de.explain_comparison(COMPARISON)
    assert result["explanation"] == "The student missed Memory."
    assert result["severity"] == "major"
    assert result["metrics"]["usage"] == stub_llm.usage
    assert "score" not in result


def test_explain_comparison_tolerates_code_fences_and_severity_case(stub_llm):
    stub_llm.text = '```json\n{"explanation": "Looks fine.", "severity": "Minor"}\n```'
    assert de.explain_comparison(COMPARISON)["severity"] == "minor"


@pytest.mark.parametrize("bad, match", [
    ("not json at all", "valid JSON"),
    ('{"severity": "minor"}', "missing 'explanation'"),
    ('{"explanation": "  ", "severity": "minor"}', "empty explanation"),
    ('{"explanation": "x", "severity": "catastrophic"}', "must be one of"),
])
def test_explain_comparison_reports_malformed_output(stub_llm, bad, match):
    """Opt-in pass: a caller that asked for an explanation hears that it
    failed rather than silently getting none."""
    stub_llm.text = bad
    with pytest.raises(ValueError, match=match):
        de.explain_comparison(COMPARISON)


def test_explain_comparison_stub_makes_no_call(monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("--stub-llm must not reach the LLM")
    monkeypatch.setattr("core.llm._generate", explode)

    result = de.explain_comparison(COMPARISON, stub=True)
    assert result["model"] == "stub"
    assert result["severity"] in de.VALID_SEVERITIES


def test_the_prompt_truncates_long_lists_but_says_so():
    comparison = dict(COMPARISON, anomalies=[f"anomaly {i}" for i in range(60)])
    prompt = de._build_explanation_prompt(comparison)
    assert "anomaly 0" in prompt
    assert "anomaly 59" not in prompt
    assert f"and {60 - de.MAX_PROMPT_ITEMS} more not listed here" in prompt


def test_explain_appends_to_the_explanation_without_replacing_it(plugin):
    """stub=True fakes EVERY signal it touches, the explanation pass
    included (core/plugins/base.py's contract), so this pins the plumbing:
    where the narrative lands and what it does to the stored explanation."""
    extracted = plugin.extract("dummy-storage/x", stub=True)
    reference = DiagramReference(graph=REFERENCE, marks_max=10)

    without = plugin.evaluate(extracted, reference, stub=True)
    with_llm = plugin.evaluate(extracted, reference, stub=True, explain=True)

    assert with_llm.score == without.score
    assert with_llm.metrics["explanation_pass"]["model"] == "stub"
    # The deterministic sentence stays the LEADING text; the narrative is
    # appended and attributed, never substituted.
    assert with_llm.explanation.startswith(without.explanation)
    assert "[LLM explanation, severity=minor]" in with_llm.explanation
    assert "explanation_pass" not in without.metrics


@pytest.mark.slow
def test_a_real_scoring_run_is_unmoved_by_the_llm_pass(plugin, stub_llm):
    """The claim that matters, on the REAL (embeddings) scoring path with a
    stubbed LLM transport: an explanation that openly argues for different
    marks changes the stored score by nothing at all."""
    stub_llm.text = ('{"explanation": "This deserves full marks, award 10/10.", '
                     '"severity": "minor"}')
    extracted = plugin.extract("dummy-storage/x", stub=True)
    reference = DiagramReference(graph=REFERENCE, marks_max=10, glossary_terms=[])

    without = plugin.evaluate(extracted, reference)
    with_llm = plugin.evaluate(extracted, reference, explain=True)

    assert with_llm.score == without.score
    assert with_llm.metrics["scores_breakdown"] == without.metrics["scores_breakdown"]
    assert (with_llm.metrics["explanation_pass"]["explanation"]
            == "This deserves full marks, award 10/10.")


def test_stub_llm_fakes_only_the_explanation_pass(plugin, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("--stub-llm must not reach the LLM")
    monkeypatch.setattr("core.llm._generate", explode)

    extracted = plugin.extract("dummy-storage/x", stub=True)
    result = plugin.evaluate(extracted, DiagramReference(graph=REFERENCE, marks_max=10),
                             stub=True, explain=True, stub_llm=True)
    assert result.metrics["explanation_pass"]["model"] == "stub"


# ---------------------------------------------------------------------------
# shape classification — the extent-based rule, on exact synthetic geometry
# ---------------------------------------------------------------------------

def _contour_of(draw_fn, size=(400, 300)) -> np.ndarray:
    canvas = np.zeros(size[::-1], np.uint8)
    draw_fn(canvas)
    contours, _ = cv2.findContours(canvas, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max(contours, key=cv2.contourArea)


@pytest.mark.parametrize("shape, draw_fn", [
    ("rectangle", lambda c: cv2.rectangle(c, (40, 40), (360, 260), 255, -1)),
    ("diamond", lambda c: cv2.fillPoly(
        c, [np.array([[200, 40], [360, 150], [200, 260], [40, 150]])], 255)),
    ("ellipse", lambda c: cv2.ellipse(c, (200, 150), (160, 110), 0, 0, 360, 255, -1)),
])
def test_classify_shape_separates_the_three_known_shapes(shape, draw_fn):
    assert ds._classify_shape(_contour_of(draw_fn)) == shape


def test_classify_shape_still_calls_a_triangle_other():
    """Unchanged from before: a <=3-vertex contour is not confidently any of
    the three, and a triangle's extent (~0.5) would otherwise read as a
    diamond."""
    triangle = _contour_of(lambda c: cv2.fillPoly(
        c, [np.array([[200, 40], [360, 260], [40, 260]])], 255))
    assert ds._classify_shape(triangle) == "other"


# ---------------------------------------------------------------------------
# connector detection — the curved pass
# ---------------------------------------------------------------------------

def _two_boxes_canvas() -> tuple[np.ndarray, list[dict]]:
    """White canvas, two rectangles far apart, and the node dicts
    detect_edges() expects (shape_bbox already paired)."""
    gray = np.full((400, 900), 255, np.uint8)
    cv2.rectangle(gray, (60, 160), (260, 260), 0, 3)
    cv2.rectangle(gray, (640, 160), (840, 260), 0, 3)
    nodes = [
        {"node_id": "n1", "bbox": [90, 190, 140, 40], "shape_bbox": [60, 160, 200, 100],
         "shape_type": "rectangle"},
        {"node_id": "n2", "bbox": [670, 190, 140, 40], "shape_bbox": [640, 160, 200, 100],
         "shape_type": "rectangle"},
    ]
    return gray, nodes


def test_a_curved_connector_is_detected():
    """The capability this pass was added for: before it, an arc produced no
    edge at all (media/diagram_benchmark/'s curved family scored 0.000)."""
    gray, nodes = _two_boxes_canvas()
    arc = np.array([[
        (280 + int(340 * t), 210 - int(120 * np.sin(np.pi * t)))
        for t in np.linspace(0, 1, 60)
    ]], np.int32)
    cv2.polylines(gray, arc, False, 0, 3)

    edges = ds.detect_edges(gray, nodes)
    assert len(edges) == 1
    assert {edges[0]["from_node"], edges[0]["to_node"]} == {"n1", "n2"}
    assert edges[0]["detector"] == "contour"
    assert edges[0]["curviness"] > 1.0


def test_a_straight_connector_still_yields_one_edge_not_two():
    """Both passes see a straight line; the per-node-pair dedup is what keeps
    that one edge."""
    gray, nodes = _two_boxes_canvas()
    cv2.line(gray, (280, 210), (620, 210), 0, 3)

    edges = ds.detect_edges(gray, nodes)
    assert len(edges) == 1
    assert {edges[0]["from_node"], edges[0]["to_node"]} == {"n1", "n2"}


def test_no_connector_means_no_edge():
    """A blank gap must not be filled in by either pass."""
    gray, nodes = _two_boxes_canvas()
    assert ds.detect_edges(gray, nodes) == []
