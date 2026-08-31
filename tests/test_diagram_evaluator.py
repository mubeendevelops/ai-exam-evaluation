"""Characterization tests for core/diagram_evaluator.py."""
from __future__ import annotations

import copy
import json
import pathlib

import pytest

from core import diagram_evaluator as de

REFERENCE_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "media" / "diagrams" / "water_cycle_reference.json"
)


@pytest.fixture
def reference_graph():
    return json.loads(REFERENCE_PATH.read_text())


# ---------------------------------------------------------------------------
# compare_diagrams — perfect match
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_compare_diagrams_perfect_match(reference_graph):
    extracted = copy.deepcopy(reference_graph)
    result = de.compare_diagrams(extracted, reference_graph, [], 10)

    assert result["similarity_score"] == 10.0
    breakdown = result["scores_breakdown"]
    assert breakdown["node_f1"] == 1.0
    assert breakdown["edge_f1"] == 1.0
    assert breakdown["node_precision"] == 1.0
    assert breakdown["node_recall"] == 1.0
    assert breakdown["edge_precision"] == 1.0
    assert breakdown["edge_recall"] == 1.0
    assert result["missing_information"] == []
    assert result["anomalies"] == []
    assert len(result["node_validation"]) == 5
    for row in result["node_validation"]:
        assert row["status"] == "matched"
        assert row["similarity"] == 1.0
    for row in result["edge_comparison"]:
        assert row["status"] == "matched"


# ---------------------------------------------------------------------------
# compare_diagrams — degraded graph: drop n5 (Collection), add extra node
# n6 (Runoff), reverse edge n1->n2.
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_compare_diagrams_degraded_graph(reference_graph):
    degraded_nodes = [n for n in reference_graph["nodes"] if n["node_id"] != "n5"]
    degraded_nodes.append({"node_id": "n6", "label": "Runoff"})

    degraded_edges = []
    for e in reference_graph["edges"]:
        if e["edge_id"] == "e1":
            degraded_edges.append({**e, "from_node": e["to_node"], "to_node": e["from_node"]})
        elif e["edge_id"] == "e4":
            continue  # dropped along with n5
        else:
            degraded_edges.append(e)

    extracted = {"schema_version": 1, "nodes": degraded_nodes, "edges": degraded_edges}
    result = de.compare_diagrams(extracted, reference_graph, [], 10)

    breakdown = result["scores_breakdown"]
    # Observed exact values for this exact perturbation.
    assert breakdown["node_precision"] == pytest.approx(0.8)
    assert breakdown["node_recall"] == pytest.approx(0.8)
    assert breakdown["node_f1"] == pytest.approx(0.8)
    assert breakdown["edge_precision"] == pytest.approx(1.0)
    assert breakdown["edge_recall"] == pytest.approx(0.75)
    assert breakdown["edge_f1"] == pytest.approx(0.8571, abs=1e-4)

    expected_combined = 0.6 * breakdown["node_f1"] + 0.4 * breakdown["edge_f1"]
    assert result["similarity_score"] == pytest.approx(round(expected_combined * 10, 2))
    assert result["similarity_score"] == 8.23

    reversed_rows = [r for r in result["edge_comparison"] if r["status"] == "direction_reversed"]
    assert len(reversed_rows) == 1
    assert reversed_rows[0] == {"from": "n1", "to": "n2", "status": "direction_reversed"}

    assert "Extra node 'Runoff' with no reference counterpart" in result["anomalies"]
    assert "Node 'Collection' not found in student diagram" in result["missing_information"]


# ---------------------------------------------------------------------------
# compare_diagrams — empty extracted graph (precision-branch coverage)
# ---------------------------------------------------------------------------

def test_compare_diagrams_empty_extracted_graph(reference_graph):
    empty = {"schema_version": 1, "nodes": [], "edges": []}
    result = de.compare_diagrams(empty, reference_graph, [], 10)

    breakdown = result["scores_breakdown"]
    # ext_nodes empty & ref_nodes non-empty -> precision branch "0.0"
    assert breakdown["node_precision"] == 0.0
    assert breakdown["node_recall"] == 0.0
    assert breakdown["edge_precision"] == 0.0
    assert breakdown["edge_recall"] == 0.0
    assert result["similarity_score"] == 0.0
    assert all(row["status"] == "missing" for row in result["node_validation"])


def test_compare_diagrams_both_graphs_empty():
    empty = {"schema_version": 1, "nodes": [], "edges": []}
    result = de.compare_diagrams(empty, empty, [], 10)

    breakdown = result["scores_breakdown"]
    # ref_nodes empty -> "1.0 if not ref_nodes else 0.0" / recall-empty branches
    assert breakdown["node_precision"] == 1.0
    assert breakdown["node_recall"] == 1.0
    assert breakdown["edge_precision"] == 1.0
    assert breakdown["edge_recall"] == 1.0
    assert result["similarity_score"] == 10.0


def test_compare_diagrams_extracted_only_reference_empty():
    ref_empty = {"schema_version": 1, "nodes": [], "edges": []}
    extracted = {"schema_version": 1, "nodes": [{"node_id": "x1", "label": "Foo"}], "edges": []}
    result = de.compare_diagrams(extracted, ref_empty, [], 10)

    breakdown = result["scores_breakdown"]
    # ext_nodes non-empty, ref_nodes empty -> precision = matched/ext = 0.0,
    # recall = 1.0 (ref empty -> vacuously satisfied).
    assert breakdown["node_precision"] == 0.0
    assert breakdown["node_recall"] == 1.0
    assert result["similarity_score"] == 4.0  # 0.6*0 + 0.4*1 = 0.4 -> 4.0/10


# ---------------------------------------------------------------------------
# match_glossary — literal glossary dicts mirroring the 8 seeded
# glossary_terms rows (CPU / Memory subset used here).
# ---------------------------------------------------------------------------

GLOSSARY = [
    {"term_id": "cpu-id", "canonical_term": "CPU",
     "aliases": ["Central Processing Unit", "processor"]},
    {"term_id": "mem-id", "canonical_term": "Memory",
     "aliases": ["RAM", "main memory"]},
]


def test_match_glossary_exact_hit():
    result = de.match_glossary("CPU", GLOSSARY)
    assert result == {"term_id": "cpu-id", "canonical_term": "CPU", "match_type": "exact"}


def test_match_glossary_alias_hit():
    result = de.match_glossary("processor", GLOSSARY)
    assert result == {"term_id": "cpu-id", "canonical_term": "CPU", "match_type": "exact"}


def test_match_glossary_short_label_edit_distance():
    # "CPV" is a real single-character misread of "CPU"; edit distance 1
    # against the short (<=6 char) canonical term wins via the short-label
    # path before the SequenceMatcher ratio (which alone would be 0.667,
    # below GLOSSARY_FUZZY_THRESHOLD).
    result = de.match_glossary("CPV", GLOSSARY)
    assert result == {"term_id": "cpu-id", "canonical_term": "CPU", "match_type": "fuzzy"}


def test_match_glossary_ratio_path():
    # "Memroy" (transposed o/r) has edit distance 2 from "Memory" — beyond
    # SHORT_LABEL_MAX_EDITS — so it's accepted via the SequenceMatcher ratio
    # path instead (observed ratio 0.8333, above GLOSSARY_FUZZY_THRESHOLD).
    assert de._edit_distance("memroy", "memory") == 2
    result = de.match_glossary("Memroy", GLOSSARY)
    assert result == {"term_id": "mem-id", "canonical_term": "Memory", "match_type": "fuzzy"}


def test_match_glossary_miss_returns_none():
    assert de.match_glossary("Xylophone", GLOSSARY) is None


def test_match_glossary_empty_label_returns_none():
    assert de.match_glossary("   ", GLOSSARY) is None


# ---------------------------------------------------------------------------
# DB integration — proves the platform-admin RLS fixture actually works by
# reading the 8 seeded glossary_terms rows through db_conn.
# ---------------------------------------------------------------------------

@pytest.mark.db
def test_glossary_terms_seeded_rows(db_conn):
    cur = db_conn.cursor()
    cur.execute("SELECT canonical_term, aliases FROM glossary_terms ORDER BY canonical_term")
    rows = cur.fetchall()
    cur.close()

    assert len(rows) == 8
    by_term = {term: aliases for term, aliases in rows}
    assert "CPU" in by_term
    assert set(by_term["CPU"]) == {"Central Processing Unit", "processor"}
    assert "Memory" in by_term
    assert set(by_term["Memory"]) == {"RAM", "main memory"}
