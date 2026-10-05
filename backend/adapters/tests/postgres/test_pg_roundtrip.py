"""Every domain type saved and loaded back is equal to what was saved."""

from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import DBAPIError

from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres.testing import Opener, World, new_college
from tarn_core.domain.blueprint import AllOf, ExamBlueprint, QuestionSlot, Section, Step
from tarn_core.domain.booklet import (
    LineReading,
    Region,
    RegionKind,
    Segment,
    SegmentSource,
    SegmentSpan,
)
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.content import (
    CriterionType,
    ListItem,
    ListParams,
    LlmParams,
    NumericParams,
    Question,
    RubricCriterion,
    SemanticParams,
    Subject,
)
from tarn_core.domain.review import Review
from tarn_core.domain.scoring import SentenceVector
from tarn_core.ids import (
    BlueprintId,
    CriterionId,
    QuestionId,
    RegionId,
    ResultSheetId,
    ReviewId,
    SegmentId,
    SubjectId,
)
from tarn_core.testing.builders import add_question, ipr_shaped_blueprint, meta

pytestmark = pytest.mark.integration


def test_content_roundtrip(session: Opener) -> None:
    college = new_college(session, "R")
    with session(college.id) as s:
        ipr = ipr_shaped_blueprint(s, college)  # OR group, sub-parts, any-N
        assert s.content.get(ExamBlueprint, ipr.id) == ipr

        subject = Subject(id=SubjectId(s.ids.new()), meta=meta(college), code="RT1", name="Round")
        s.content.save(subject)
        q = add_question(s.content, college, s.ids.new(), subject.id, Decimal("2.5"))
        stepped = ExamBlueprint(
            id=BlueprintId(s.ids.new()),
            meta=meta(college),
            subject_id=subject.id,
            title="Steps and negative marks",
            total_marks=Decimal("2.5"),
            mark_step=Decimal("0.25"),
            sections=(
                Section(
                    label="A",
                    title="Only section",
                    rule=AllOf(),
                    items=(
                        QuestionSlot(
                            label="1",
                            marks=Decimal("2.5"),
                            question_id=q.id,
                            steps=(
                                Step(label="setup", marks=Decimal("1")),
                                Step(label="result", marks=Decimal("1.5")),
                            ),
                            negative_marks=Decimal("0.5"),
                        ),
                    ),
                ),
            ),
        )
        s.content.save(stepped)
        assert s.content.get(ExamBlueprint, stepped.id) == stepped
        assert s.content.get(Subject, subject.id) == subject

        params = (
            (
                CriterionType.LIST,
                ListParams(
                    items=(ListItem(term="a", synonyms=("x", "y")), ListItem(term="b")),
                    required_count=1,
                ),
            ),
            (
                CriterionType.NUMERIC,
                NumericParams(expected=Decimal("9.81"), tolerance=Decimal("0.05"), unit="m/s2"),
            ),
            (CriterionType.SEMANTIC, SemanticParams(reference_statement="Gravity pulls.")),
            (CriterionType.LLM, LlmParams(instructions="Judge the reasoning.")),
        )
        for kind, p in params:
            c = RubricCriterion(
                id=CriterionId(s.ids.new()),
                meta=meta(college),
                question_id=q.id,
                label=kind.value,
                type=kind,
                weight=Decimal("0.625"),
                params=p,
            )
            s.content.save(c)
            assert s.content.get(RubricCriterion, c.id) == c
        q2 = replace(q, meta=replace(q.meta, version=2), text="Edited", category="theory")
        s.content.save(q2)
        assert s.content.versions(Question, q.id) == [q, q2]
        missing = QuestionId(s.ids.new())
        with pytest.raises(LookupError):
            s.content.get(Question, missing)


def test_college_data_roundtrip(session: Opener, world: World) -> None:
    a = world.a
    with session(a.id) as s:
        assert s.colleges.get(a.id) == a.college.college
        assert s.users.list(a.id) == [a.college.teacher, a.college.admin]
        assert s.students.list(a.id) == list(a.college.students)
        assert s.students.find_by_usn(a.id, a.college.students[1].usn) == a.college.students[1]
        assert s.students.find_by_usn(a.id, "NOPE") is None
        assert s.booklets.get(a.id, a.booklet.id) == a.booklet

        page = s.booklets.pages(a.id, a.booklet.id)[1]
        region = Region(
            id=RegionId(s.ids.new()),
            college_id=a.id,
            page_id=page.id,
            kind=RegionKind.TEXT_BLOCK,
            box=Box(x0=5, y0=6, x1=7, y1=8),
            readings=(
                LineReading(
                    engine=EngineRef(name="e1", version="1.0"),
                    text="hi",
                    box=Box(x0=5, y0=6, x1=7, y1=8),
                    confidence=0.125,
                    char_confidences=(0.5, 0.25),
                ),
                LineReading(
                    engine=EngineRef(name="e2", version="2.0"),
                    text="hl",
                    box=Box(x0=5, y0=6, x1=7, y1=8),
                    confidence=0.75,
                ),
            ),
            chosen=1,
            teacher_text="hi",
        )
        s.booklets.save_region(a.id, region)
        assert s.booklets.regions(a.id, page.id) == [region]
        fewer = replace(region, readings=region.readings[:1], chosen=0, teacher_text=None)
        s.booklets.save_region(a.id, fewer)  # re-save replaces the readings
        assert s.booklets.regions(a.id, page.id) == [fewer]

        segment = Segment(
            id=SegmentId(s.ids.new()),
            college_id=a.id,
            booklet_id=a.booklet.id,
            slot_label=None,
            spans=(
                SegmentSpan(page_id=page.id, box=Box(x0=0, y0=0, x1=10, y1=10)),
                SegmentSpan(page_id=page.id, box=Box(x0=0, y0=20, x1=10, y1=30)),
            ),
            source=SegmentSource.SIMILARITY,
            match_score=0.375,
        )
        s.booklets.save_segment(a.id, segment)
        assert segment in s.booklets.segments(a.id, a.booklet.id)

        assert s.booklets.get_answer(a.id, a.answers[1].id) == a.answers[1]
        diagrams = s.booklets.diagrams(a.id, a.booklet.id)
        assert len(diagrams) == 1 and diagrams[0].graph.edges[0].label == "go"
        assert diagrams[0].graph.nodes[1].box == Box(x0=1, y0=2, x1=3, y1=4)

        score = s.scores.scores(a.id, a.answers[1].id)[0]
        assert score.content_versions >= {score.question}
        assert len(score.criterion_scores) == 2
        manual = Review(
            id=ReviewId(s.ids.new()),
            college_id=a.id,
            answer_id=a.answers[1].id,
            answer_score_id=None,
            ai_mark=None,
            teacher_mark=Decimal("1.5"),
            reviewer=a.college.teacher.id,
            reviewed_at=s.clock.now(),
        )
        s.scores.save_review(a.id, manual)
        assert s.scores.reviews(a.id, a.answers[1].id) == [manual]

        sheets = s.sheets.versions(a.id, a.booklet.id)
        assert [x.version for x in sheets] == [1]
        assert sheets[0].lines[1].mark is None and sheets[0].pdf is not None
        with pytest.raises(ValueError):  # InvariantError: version 3 skips 2
            s.sheets.save(a.id, replace(sheets[0], id=ResultSheetId(s.ids.new()), version=3))
        v2 = replace(sheets[0], id=ResultSheetId(s.ids.new()), version=2)
        s.sheets.save(a.id, v2)
        assert s.sheets.versions(a.id, a.booklet.id) == [sheets[0], v2]


def _unit(*head: float) -> tuple[float, ...]:
    return tuple(head) + (0.0,) * (m.SCORING_DIMENSION - len(head))


def test_sentence_embeddings_use_pgvector(session: Opener, world: World) -> None:
    a, b = world.a, world.b
    t = m.sentence_embeddings
    embedder = EngineRef(name="synthetic-embedder", version="1")
    with session(a.id) as s:
        vectors = [
            SentenceVector(index=k, text_sha256=f"{k:064x}", vector=v)
            for k, v in enumerate((_unit(1.0), _unit(0.0, 1.0), _unit(0.7, 0.7)))
        ]
        s.scores.replace_vectors(a.id, a.answers[0].id, embedder, vectors)
        assert s.scores.vectors(a.id, a.answers[0].id, embedder) == vectors
        nearest = s.conn.execute(
            select(t.c.sentence_index)
            .where(t.c.answer_id == a.answers[0].id)
            .order_by(t.c.embedding.cosine_distance(list(_unit(1.0, 0.1))))
        ).scalars()
        assert list(nearest) == [0, 2, 1]
        # Another dimension (the trigram fallback) is not kept: the table is a cache.
        s.scores.replace_vectors(
            a.id,
            a.answers[0].id,
            embedder,
            [SentenceVector(index=0, text_sha256="0" * 64, vector=(1.0, 0.0, 0.0))],
        )
        assert s.scores.vectors(a.id, a.answers[0].id, embedder) == []
        with pytest.raises(DBAPIError), s.conn.begin_nested():  # dimension is fixed
            s.conn.execute(
                insert(t).values(
                    id=s.ids.new(),
                    college_id=a.id,
                    answer_id=a.answers[0].id,
                    sentence_index=9,
                    embedder_name="synthetic-embedder",
                    embedder_version="1",
                    embedding=[1.0, 0.0, 0.0],
                    text_sha256="0" * 64,
                )
            )
    with session(b.id) as s:
        rows = s.conn.execute(select(t.c.answer_id)).scalars().all()
        assert rows == [b.answers[0].id]  # B sees only its own embedding
