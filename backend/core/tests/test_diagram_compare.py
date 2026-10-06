"""Diagram comparison (P14) on hand-built graphs: matching, edges, anomalies, similarity."""

import itertools
import random
from collections.abc import Sequence

import pytest

from tarn_core.domain.diagram import (
    DiagramEdge,
    DiagramGraph,
    DiagramNode,
    FreeLabel,
    NodeShape,
)
from tarn_core.errors import InvariantError
from tarn_core.services.diagrams.compare import (
    AnomalyType,
    EdgeStatus,
    GraphComparator,
    shape_cost,
)
from tarn_core.services.diagrams.hungarian import assign
from tarn_core.services.diagrams.labels import (
    Glossary,
    LabelMatch,
    label_distance,
    normalise_label,
)
from tarn_core.services.diagrams.policy import DiagramPolicy

T, P, D, IO, C = (
    NodeShape.TERMINAL,
    NodeShape.PROCESS,
    NodeShape.DECISION,
    NodeShape.IO,
    NodeShape.CIRCLE,
)


def graph(
    nodes: Sequence[tuple[str, NodeShape, str]],
    edges: Sequence[tuple[str | None, ...]] = (),
    *,
    directed: bool = True,
    free: tuple[str, ...] = (),
) -> DiagramGraph:
    """Edges as (source, target) or (source, target, label)."""
    return DiagramGraph(
        nodes=tuple(DiagramNode(id=i, shape=s, label=label) for i, s, label in nodes),
        edges=tuple(
            DiagramEdge(
                id=f"e{k}",
                source=e[0],
                target=e[1],
                label=(e[2] or "") if len(e) > 2 else "",
                directed=directed,
            )
            for k, e in enumerate(edges, 1)
        ),
        free_labels=tuple(FreeLabel(text=t) for t in free),
    )


# The reference: start -> read n -> is n > 0? -(yes)-> print n -> stop; no -> stop
REF = graph(
    [
        ("r1", T, "start"),
        ("r2", IO, "read n"),
        ("r3", D, "is n > 0?"),
        ("r4", IO, "print n"),
        ("r5", T, "stop"),
    ],
    [("r1", "r2"), ("r2", "r3"), ("r3", "r4", "yes"), ("r4", "r5"), ("r3", "r5", "no")],
)


def student() -> DiagramGraph:
    return graph(
        [
            ("s1", T, "Start"),
            ("s2", IO, "read  n"),
            ("s3", D, "is n>0 ?"),
            ("s4", IO, "print n"),
            ("s5", T, "stop"),
        ],
        [("s1", "s2"), ("s2", "s3"), ("s3", "s4", "yes"), ("s4", "s5"), ("s3", "s5", "no")],
    )


COMPARE = GraphComparator()


def test_identical_graphs_score_one() -> None:
    c = COMPARE.compare(REF, student(), [])
    assert c.similarity == 1.0
    assert c.sub_scores.nodes == 1.0 and c.sub_scores.edges == 1.0 and c.sub_scores.labels == 1.0
    assert [r.matched_student for r in c.reference_nodes] == ["s1", "s2", "s3", "s4", "s5"]
    assert all(e.status is EdgeStatus.PRESENT for e in c.edges)
    assert c.anomalies == () and c.missing_nodes == () and c.missing_labels == ()
    assert all(n.label_match is LabelMatch.EXACT for n in c.nodes)


def test_missing_node_is_reported_with_its_edges_and_label() -> None:
    s = graph(
        [("s1", T, "start"), ("s2", IO, "read n"), ("s3", D, "is n > 0?"), ("s5", T, "stop")],
        [("s1", "s2"), ("s2", "s3"), ("s3", "s5", "no")],
    )
    c = COMPARE.compare(REF, s, [])
    assert c.missing_nodes == ("r4",)
    assert set(c.missing_edges) == {("r3", "r4"), ("r4", "r5")}
    assert c.missing_labels == ("print n", "yes")  # the "yes" arrow went with it
    assert c.anomalies == ()
    # GED = node r4 (1) + edges r3-r4, r4-r5 (2) over 5 + 4 + 5 + 3 = 17
    assert c.edit_distance == 3.0
    assert c.similarity == pytest.approx(1 - 3 / 17, abs=1e-4)
    assert c.sub_scores.nodes == pytest.approx(2 * 4 / 9, abs=1e-4)
    assert c.sub_scores.edges == pytest.approx(2 * 3 / 8, abs=1e-4)


def test_extra_node_is_an_anomaly_and_disconnected_when_isolated() -> None:
    s = graph(
        [
            ("s1", T, "start"),
            ("s2", IO, "read n"),
            ("s3", D, "is n > 0?"),
            ("s4", IO, "print n"),
            ("s5", T, "stop"),
            ("s9", P, "print x"),
        ],
        [("s1", "s2"), ("s2", "s3"), ("s3", "s4", "yes"), ("s4", "s5"), ("s3", "s5", "no")],
    )
    c = COMPARE.compare(REF, s, [])
    kinds = [(a.type, a.student) for a in c.anomalies]
    assert (AnomalyType.EXTRA_NODE, ("s9",)) in kinds
    assert (AnomalyType.DISCONNECTED, ("s9",)) in kinds
    assert c.missing_nodes == ()
    assert c.similarity == pytest.approx(1 - 1 / (5 + 5 + 6 + 5), abs=1e-4)


def test_extra_connected_node_is_not_disconnected() -> None:
    s = graph(
        [
            ("s1", T, "start"),
            ("s2", IO, "read n"),
            ("s3", D, "is n > 0?"),
            ("s4", IO, "print n"),
            ("s5", T, "stop"),
            ("s9", P, "x = x + 1"),
        ],
        [
            ("s1", "s2"),
            ("s2", "s3"),
            ("s3", "s4", "yes"),
            ("s4", "s9"),
            ("s9", "s5"),
            ("s3", "s5", "no"),
        ],
    )
    c = COMPARE.compare(REF, s, [])
    types = {a.type for a in c.anomalies}
    assert AnomalyType.EXTRA_NODE in types and AnomalyType.EXTRA_EDGE in types
    assert AnomalyType.DISCONNECTED not in types
    statuses = {e.ref: e.status for e in c.edges if e.ref is not None}
    assert statuses[("r4", "r5")] is EdgeStatus.MISSING


def test_reversed_edge() -> None:
    s = graph(
        [
            ("s1", T, "start"),
            ("s2", IO, "read n"),
            ("s3", D, "is n > 0?"),
            ("s4", IO, "print n"),
            ("s5", T, "stop"),
        ],
        [("s1", "s2"), ("s3", "s2"), ("s3", "s4", "yes"), ("s4", "s5"), ("s3", "s5", "no")],
    )
    c = COMPARE.compare(REF, s, [])
    statuses = {e.ref: e.status for e in c.edges if e.ref is not None}
    assert statuses[("r2", "r3")] is EdgeStatus.REVERSED
    reversed_ = [a for a in c.anomalies if a.type is AnomalyType.REVERSED_EDGE]
    assert [(a.student, a.ref, a.edge) for a in reversed_] == [(("s3", "s2"), ("r2", "r3"), "e2")]
    assert c.missing_edges == ()
    assert c.edit_distance == 1.0
    assert c.sub_scores.edges == pytest.approx(2 * 4.5 / 10)


def test_lines_without_heads_are_never_reversed() -> None:
    ref = graph(
        [("a", C, "1"), ("b", C, "2"), ("c", C, "3")], [("a", "b"), ("a", "c")], directed=False
    )
    stu = graph(
        [("x", C, "1"), ("y", C, "2"), ("z", C, "3")], [("y", "x"), ("x", "z")], directed=False
    )
    c = COMPARE.compare(ref, stu, [])
    assert c.similarity == 1.0
    assert all(e.status is EdgeStatus.PRESENT for e in c.edges)


def test_disconnected_part() -> None:
    s = graph(
        [
            ("s1", T, "start"),
            ("s2", IO, "read n"),
            ("s3", D, "is n > 0?"),
            ("s4", IO, "print n"),
            ("s5", T, "stop"),
        ],
        [("s1", "s2"), ("s2", "s3"), ("s4", "s5")],
    )
    c = COMPARE.compare(REF, s, [])
    parts = [a.student for a in c.anomalies if a.type is AnomalyType.DISCONNECTED]
    assert parts == [("s4", "s5")]
    assert set(c.missing_edges) == {("r3", "r4"), ("r3", "r5")}


def test_dangling_arrows() -> None:
    s = graph(
        [("s1", T, "start"), ("s2", IO, "read n")],
        [("s1", "s2"), ("s2", None), (None, None)],
    )
    c = COMPARE.compare(REF, s, [])
    dangling = {a.student: a.end for a in c.anomalies if a.type is AnomalyType.DANGLING_ARROW}
    assert dangling == {("e2",): "head", ("e3",): "both"}
    extra = [e for e in c.edges if e.status is EdgeStatus.EXTRA]
    assert {e.student_edge for e in extra} == {"e2", "e3"}


def test_misspelt_label_snaps_to_glossary_and_matches_close() -> None:
    s = graph([("s1", T, "strat"), ("s2", IO, "raed n")], [("s1", "s2")])
    ref = graph([("r1", T, "start"), ("r2", IO, "read n")], [("r1", "r2")])
    c = COMPARE.compare(ref, s, ["start", "read n"])
    by_id = {n.id: n for n in c.nodes}
    assert by_id["s1"].glossary_term == "start" and by_id["s1"].glossary_match is LabelMatch.CLOSE
    assert by_id["s1"].label_match is LabelMatch.CLOSE
    assert c.sub_scores.labels > 0.5
    assert c.missing_labels == ()


def test_wrong_label_and_shape_on_a_matched_node() -> None:
    ref = graph([("r1", T, "start"), ("r2", P, "sum = a + b")], [("r1", "r2")])
    stu = graph([("s1", T, "start"), ("s2", D, "sum = a + c")], [("s1", "s2")])
    c = COMPARE.compare(ref, stu, [])
    r2 = c.reference_nodes[1]
    assert r2.matched_student == "s2"
    assert r2.shape_match is False
    assert r2.label_match is LabelMatch.CLOSE


def test_unrelated_node_is_not_forced_into_a_match() -> None:
    ref = graph([("r1", T, "start"), ("r2", P, "compute interest")], [("r1", "r2")])
    stu = graph([("s1", T, "start"), ("s2", D, "zzz qqq")], [("s1", "s2")])
    c = COMPARE.compare(ref, stu, [])
    assert c.reference_nodes[1].matched_student is None
    assert c.missing_nodes == ("r2",)
    assert any(a.type is AnomalyType.EXTRA_NODE for a in c.anomalies)


def test_acceptance_threshold_matters() -> None:
    ref = graph([("r1", T, "start"), ("r2", P, "compute interest")], [("r1", "r2")])
    stu = graph([("s1", T, "start"), ("s2", D, "zzz qqq")], [("s1", "s2")])
    loose = GraphComparator(DiagramPolicy(accept=1.0)).compare(ref, stu, [])
    assert loose.reference_nodes[1].matched_student == "s2"


def test_empty_student_scores_zero_and_empty_graphs_one() -> None:
    c = COMPARE.compare(REF, DiagramGraph(nodes=()), [])
    assert c.similarity == 0.0
    assert len(c.missing_nodes) == 5
    assert COMPARE.compare(DiagramGraph(nodes=()), DiagramGraph(nodes=()), []).similarity == 1.0


def test_unlabelled_tree_matches_by_shape_and_structure() -> None:
    ref = graph(
        [("a", C, ""), ("b", C, ""), ("c", C, ""), ("d", C, "")],
        [("a", "b"), ("a", "c"), ("c", "d")],
        directed=False,
    )
    stu = graph(
        [("w", C, ""), ("x", C, ""), ("y", C, ""), ("z", C, "")],
        [("w", "x"), ("w", "y"), ("y", "z")],
        directed=False,
    )
    assert COMPARE.compare(ref, stu, []).similarity == 1.0


def test_free_label_outside_the_reference_is_an_extra_label() -> None:
    s = graph([("s1", T, "start")], [], free=("my notes",))
    ref = graph([("r1", T, "start")])
    c = COMPARE.compare(ref, s, [])
    assert [(a.type, a.label) for a in c.anomalies] == [(AnomalyType.EXTRA_LABEL, "my notes")]


def test_similarity_bounds_on_random_graphs() -> None:
    rng = random.Random(7)  # noqa: S311  (test data)
    shapes = list(NodeShape)
    for _ in range(60):
        g = []
        for prefix in "rs":
            n = rng.randint(0, 6)
            nodes = [
                (f"{prefix}{i}", rng.choice(shapes), rng.choice(["a", "b", "", "ab"]))
                for i in range(n)
            ]
            ids = [x[0] for x in nodes]
            edges = (
                [(rng.choice(ids), rng.choice(ids)) for _ in range(rng.randint(0, 6))]
                if ids
                else []
            )
            g.append(graph(nodes, edges))
        c = COMPARE.compare(g[0], g[1], [])
        assert 0.0 <= c.similarity <= 1.0
        for v in (c.sub_scores.nodes, c.sub_scores.edges, c.sub_scores.labels):
            assert 0.0 <= v <= 1.0
        assert COMPARE.compare(g[0], g[0], []).similarity == 1.0


# --- parts ------------------------------------------------------------------------------------


def test_hungarian_matches_brute_force() -> None:
    rng = random.Random(3)  # noqa: S311  (test data)
    for n in range(1, 7):
        for _ in range(20):
            cost = [[rng.choice([0.0, 0.5, 1.0, rng.random()]) for _ in range(n)] for _ in range(n)]
            got = assign(cost)
            assert sorted(got) == list(range(n))
            best = min(
                sum(cost[i][p[i]] for i in range(n)) for p in itertools.permutations(range(n))
            )
            assert sum(cost[i][got[i]] for i in range(n)) == pytest.approx(best)


def test_hungarian_respects_forbidden_cells() -> None:
    inf = float("inf")
    assert assign([[inf, 1.0], [1.0, inf]]) == [1, 0]
    assert assign([]) == []
    with pytest.raises(ValueError, match="square"):
        assign([[1.0, 2.0]])


def test_normalise_and_snap() -> None:
    assert normalise_label("  Is  N > 0 ? ") == "is n>0?"
    assert normalise_label("x ≥ 1.") == "x>=1"
    assert label_distance("", "") == 0.0 and label_distance("a", "") == 1.0
    g = Glossary(["Start", "read n", "start"], close=0.25)
    assert g.terms == ("Start", "read n")
    assert g.snap("START").match is LabelMatch.EXACT
    snapped = g.snap("reed n")
    assert snapped.term == "read n" and snapped.match is LabelMatch.CLOSE
    assert g.snap("banana").match is LabelMatch.NONE
    assert g.snap("").match is LabelMatch.NONE


def test_shape_costs() -> None:
    assert shape_cost(P, NodeShape.BLOCK) == 0.0
    assert shape_cost(P, NodeShape.OTHER) == 0.5
    assert shape_cost(P, D) == 1.0


def test_policy_weights_must_sum_to_one() -> None:
    with pytest.raises(InvariantError):
        DiagramPolicy(label_weight=0.9)
