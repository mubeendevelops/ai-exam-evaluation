"""Diagrams on PostgreSQL (P14): reference kind and recognition state, student drawings tied
to their diagram region (one per region), the R6 document stored with the criterion score,
the stage and the teacher's edit under ``tarn_app`` row-level security."""

from dataclasses import replace
from typing import Any, cast

import psycopg
import pytest

from tarn_adapters.postgres.testing import Opener, TestDatabase, World
from tarn_core.domain.booklet import BookletStatus
from tarn_core.domain.content import ReferenceDiagram
from tarn_core.domain.diagram import DiagramKind, RecognitionState
from tarn_core.errors import NotFoundError
from tarn_core.ids import StudentDiagramId
from tarn_core.ports.jobs import JOB_SCORE_BOOKLET
from tarn_core.services.diagrams.editor import EditOp, GraphEdit
from tarn_core.services.diagrams.scorer import DiagramScorer
from tarn_core.services.diagrams.service import (
    BookletDiagrams,
    ReferenceDiagrams,
    StudentDiagrams,
)
from tarn_core.services.question_bank import QuestionBankService
from tarn_core.services.scoring import BookletScorer, ScoringService
from tarn_core.testing import ScriptedDiagramRecognizer
from tarn_core.testing.diagrams import diagram_world, flowchart_detection

pytestmark = pytest.mark.integration


def _statuses(detail: object) -> list[object]:
    """The edge statuses of an R6 document."""
    doc = cast(dict[str, Any], detail)
    return [e["status"] for e in doc["edges"]]


def _scoring(s: object) -> ScoringService:
    return ScoringService.standard(
        booklets=s.booklets,  # type: ignore[attr-defined]
        scores=s.scores,  # type: ignore[attr-defined]
        content=s.content,  # type: ignore[attr-defined]
        runtime=s.runtime,  # type: ignore[attr-defined]
        embedder=None,
        extra=[DiagramScorer()],
    )


def test_diagrams_from_reference_to_score_and_edit(
    session: Opener, world: World, test_database: TestDatabase
) -> None:
    cid = world.a.id
    teacher = world.a.college.teacher.id
    recognizer = ScriptedDiagramRecognizer(flowchart_detection(reverse_second=True))
    with session(cid) as s:
        dw = diagram_world(s, college=world.a.college)
        bank = QuestionBankService(
            content=s.content, users=s.users, colleges=s.colleges, blobs=s.blobs, runtime=s.runtime
        )
        ref = ReferenceDiagrams(
            content=s.content,
            blobs=s.blobs,
            runtime=s.runtime,
            recognizers={DiagramKind.FLOWCHART: ScriptedDiagramRecognizer(flowchart_detection())},
            labels=None,
            glossary=bank,
        ).recognize(cid, teacher, dw.reference.id)
        answer = dw.answer()
        booklet = s.booklets.get(cid, dw.booklet.id)
        s.booklets.save(
            cid, replace(booklet, status=BookletStatus.SEGMENTED, version=booklet.version + 1)
        )
    # the reference's kind and state survive the round trip
    with session(cid) as s:
        stored = s.content.get(ReferenceDiagram, ref.id)
        assert stored.recognition is RecognitionState.RECOGNISED
        assert stored.kind is DiagramKind.FLOWCHART
        assert stored.graph == ref.graph
        assert [d.recognition for d in s.content.versions(ReferenceDiagram, ref.id)] == [
            RecognitionState.PENDING,
            RecognitionState.RECOGNISED,
        ]
    # the stage: one transaction per region, then scoring is queued
    done = False
    while not done:
        with session(cid) as s:
            done = BookletDiagrams(
                booklets=s.booklets,
                content=s.content,
                blobs=s.blobs,
                runtime=s.runtime,
                jobs=s.jobs,
                recognizers={DiagramKind.FLOWCHART: recognizer},
            ).step(cid, dw.booklet.id)
    with session(cid) as s:
        (drawing,) = s.booklets.diagrams(cid, dw.booklet.id)
        assert drawing.region_id is not None and drawing.kind is DiagramKind.FLOWCHART
        assert [n.label for n in drawing.graph.nodes] == ["start", "read n", "stop"]
        score = _scoring(s).score_answer(cid, None, answer.id)
    with session(cid) as s:
        stored_score = s.scores.scores(cid, answer.id)[-1]
        assert stored_score == score
        diagram = next(c for c in stored_score.criterion_scores if c.scorer == DiagramScorer.ref)
        assert isinstance(diagram.detail, dict)
        assert _statuses(diagram.detail) == ["present", "reversed"]
    with test_database.app(cid) as conn:
        kinds = [
            r[0]
            for r in conn.execute("SELECT kind FROM jobs WHERE kind = %s", (JOB_SCORE_BOOKLET,))
        ]
        assert kinds == [JOB_SCORE_BOOKLET]
    # the teacher reverses the arrow back: new version, the answer is re-scored
    with session(cid) as s:
        scorer = BookletScorer(booklets=s.booklets, scoring=_scoring(s), runtime=s.runtime)
        StudentDiagrams(booklets=s.booklets, runtime=s.runtime, rescore=scorer).edit(
            cid,
            teacher,
            dw.booklet.id,
            drawing.id,
            expected_version=1,
            edits=[GraphEdit(op=EditOp.REVERSE_EDGE, id="e2")],
        )
    with session(cid) as s:
        latest = s.scores.scores(cid, answer.id)[-1]
        after = next(c for c in latest.criterion_scores if c.scorer == DiagramScorer.ref)
        assert isinstance(after.detail, dict)
        assert _statuses(after.detail) == ["present", "present"]
        assert (after.similarity or 0) > (diagram.similarity or 0)
        assert s.booklets.diagrams(cid, dw.booklet.id)[0].version == 2
    # one drawing per region
    with session(cid) as s, pytest.raises(Exception):  # noqa: B017  (the unique index)
        s.booklets.save_diagram(cid, replace(drawing, id=StudentDiagramId(s.ids.new())))
    # another college sees none of it
    with session(world.b.id) as s:
        assert s.booklets.diagrams(world.b.id, dw.booklet.id) == []
        with pytest.raises(NotFoundError):
            StudentDiagrams(booklets=s.booklets, runtime=s.runtime, rescore=scorer).edit(
                world.b.id,
                world.b.college.teacher.id,
                dw.booklet.id,
                drawing.id,
                expected_version=2,
                edits=[GraphEdit(op=EditOp.REMOVE_EDGE, id="e1")],
            )


def test_detail_must_be_an_object(test_database: TestDatabase, world: World) -> None:
    with test_database.owner() as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE criterion_scores SET detail = '[1]'::jsonb WHERE college_id = %s",
            (str(world.a.id),),
        )
