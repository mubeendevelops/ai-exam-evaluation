"""Value types and their invariants."""

from decimal import Decimal
from uuid import UUID

import pytest

from tarn_core.domain.audit import AuditAction, AuditEvent
from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import (
    BlobKey,
    Box,
    ContentKind,
    ContentRef,
    EngineRef,
    college_blob_key,
    global_blob_key,
)
from tarn_core.domain.diagram import DiagramEdge, DiagramGraph, DiagramNode, NodeShape
from tarn_core.domain.scoring import CriterionScore, round_to_step
from tarn_core.domain.tenancy import Student, normalise_usn
from tarn_core.errors import InvariantError
from tarn_core.ids import AuditEventId, BookletId, CollegeId, StudentId, UserId
from tarn_core.testing import FixedClock

COLLEGE = CollegeId(UUID(int=1))
ENGINE = EngineRef(name="trocr", version="base-1")


def test_box_needs_positive_size() -> None:
    assert Box(x0=0, y0=0, x1=10, y1=5).area == 50
    with pytest.raises(InvariantError):
        Box(x0=5, y0=0, x1=5, y1=5)
    with pytest.raises(InvariantError):
        Box(x0=-1, y0=0, x1=5, y1=5)


def test_blob_keys_are_scoped_to_a_college_or_global() -> None:
    key = college_blob_key(COLLEGE, "booklet", "b1", "page-1.png")
    assert key.value == f"college/{COLLEGE}/booklet/b1/page-1.png"
    assert key.college_id == COLLEGE
    assert global_blob_key("diagrams", "d.png").college_id is None
    for bad in ("tmp/x.png", "college/not-a-uuid/x", "global/../x", "college//x", "global"):
        with pytest.raises(InvariantError):
            BlobKey(bad)


def test_content_ref_versions_start_at_one() -> None:
    with pytest.raises(InvariantError):
        ContentRef(kind=ContentKind.QUESTION, id=UUID(int=1), version=0)


def test_line_reading_confidences_in_unit_interval() -> None:
    box = Box(x0=0, y0=0, x1=10, y1=10)
    LineReading(engine=ENGINE, text="ab", box=box, confidence=0.5, char_confidences=(0.4, 1.0))
    with pytest.raises(InvariantError):
        LineReading(engine=ENGINE, text="ab", box=box, confidence=1.5)
    with pytest.raises(InvariantError):
        LineReading(engine=ENGINE, text="ab", box=box, confidence=0.5, char_confidences=(0.4,))


def test_diagram_edges_must_join_known_nodes() -> None:
    nodes = (
        DiagramNode(id="r1", shape=NodeShape.TERMINAL, label="start"),
        DiagramNode(id="r2", shape=NodeShape.PROCESS, label="x = 1"),
    )
    graph = DiagramGraph(
        nodes=nodes, edges=(DiagramEdge(id="e1", source="r1", target="r2", label="go"),)
    )
    assert graph.labels == ("start", "x = 1", "go")
    with pytest.raises(InvariantError):
        DiagramGraph(nodes=nodes, edges=(DiagramEdge(id="e1", source="r1", target="r9"),))
    with pytest.raises(InvariantError):
        DiagramGraph(nodes=(*nodes, nodes[0]))


def test_usn_is_normalised() -> None:
    assert normalise_usn(" tst26a 0001 ") == "TST26A0001"
    with pytest.raises(InvariantError):
        Student(id=StudentId(UUID(int=2)), college_id=COLLEGE, name="S", usn="tst26a0001")


def test_credit_is_between_zero_and_one() -> None:
    ref = ContentRef(kind=ContentKind.RUBRIC_CRITERION, id=UUID(int=3), version=1)
    score = CriterionScore(criterion=ref, weight=Decimal(4), credit=Decimal("0.5"), scorer=ENGINE)
    assert score.marks == Decimal(2)
    with pytest.raises(InvariantError):
        CriterionScore(criterion=ref, weight=Decimal(4), credit=Decimal("1.1"), scorer=ENGINE)


def test_round_to_half_mark() -> None:
    step = Decimal("0.5")
    assert round_to_step(Decimal("3.24"), step) == Decimal("3.0")
    assert round_to_step(Decimal("3.25"), step) == Decimal("3.5")
    assert round_to_step(Decimal("3.75"), step) == Decimal("4.0")


def test_deletion_record_holds_no_content() -> None:
    common = {
        "id": AuditEventId(UUID(int=4)),
        "college_id": COLLEGE,
        "actor_id": UserId(UUID(int=5)),
        "at": FixedClock().now(),
        "action": AuditAction.BOOKLET_DELETED,
        "booklet_id": BookletId(UUID(int=6)),
    }
    AuditEvent(**common)  # type: ignore[arg-type]
    with pytest.raises(InvariantError):
        AuditEvent(**common, before={"mark": "5"})  # type: ignore[arg-type]
