"""Score one answer against its rubric with the configured scorers, recording every content
and scorer version used (rule 11). Similarity feeds a criterion's credit, never the mark."""

from collections.abc import Sequence

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.common import ContentRef
from tarn_core.domain.content import (
    DiagramParams,
    Glossary,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    Rubric,
    RubricCriterion,
)
from tarn_core.domain.diagram import DiagramGraph
from tarn_core.domain.scoring import AnswerScore, answer_mark
from tarn_core.errors import DomainError, InvariantError
from tarn_core.ids import AnswerId, AnswerScoreId, CollegeId, UserId
from tarn_core.ports.engines import Scorer, ScoringInput
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services._support import Runtime


class NoScorerError(DomainError):
    """No configured scorer supports a criterion's type."""


class ScoringService:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        scorers: Sequence[Scorer],
        runtime: Runtime,
    ):
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._scorers = tuple(scorers)
        self._rt = runtime

    def score_answer(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        answer_id: AnswerId,
        *,
        answer_text: str,
        diagrams: tuple[DiagramGraph, ...] = (),
    ) -> AnswerScore | None:
        """Returns None when the key is guidance only: the teacher marks it manually."""
        answer = self._booklets.get_answer(college_id, answer_id)
        booklet = self._booklets.get(college_id, answer.booklet_id)
        blueprint = self._content.get(
            ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
        )
        _, question_id, _ = blueprint.leaf(answer.slot_label)

        question = self._content.get(Question, question_id)
        keys = self._content.for_question(ReferenceAnswer, question_id)
        criteria = tuple(self._content.for_question(RubricCriterion, question_id))
        if not criteria and any(k.guidance_only for k in keys):
            Rubric(question=question, criteria=(), guidance_only=True)
            return None
        rubric = Rubric(question=question, criteria=criteria)

        glossaries = self._content.for_question(Glossary, question_id)
        glossary_terms = tuple(t for g in glossaries for t in g.terms)
        used: set[ContentRef] = {
            blueprint.ref,
            *rubric.content_refs,
            *(k.ref for k in keys),
            *(g.ref for g in glossaries),
        }
        reference_text = "\n".join(k.text for k in keys if not k.guidance_only)

        results = []
        for criterion in rubric.criteria:
            if isinstance(criterion.params, DiagramParams):
                diagram = self._content.get(ReferenceDiagram, criterion.params.reference_diagram_id)
                used.add(diagram.ref)
            scorer = next((s for s in self._scorers if s.supports(criterion.type)), None)
            if scorer is None:
                raise NoScorerError(f"no scorer for {criterion.type} criteria")
            result = scorer.score(
                ScoringInput(
                    criterion=criterion,
                    answer_text=answer_text,
                    diagrams=diagrams,
                    reference_text=reference_text,
                    glossary=glossary_terms,
                )
            )
            if result.criterion != criterion.ref or result.weight != criterion.weight:
                raise InvariantError(f"{scorer.ref.name} scored a different criterion version")
            results.append(result)

        criterion_scores = tuple(results)
        score = AnswerScore(
            id=self._rt.new_id(AnswerScoreId),
            college_id=college_id,
            answer_id=answer_id,
            question=question.ref,
            criterion_scores=criterion_scores,
            mark_step=blueprint.mark_step,
            mark=answer_mark(criterion_scores, blueprint.mark_step),
            content_versions=frozenset(used),
            created_at=self._rt.clock.now(),
        )
        self._scores.save_score(college_id, score)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.ANSWER_SCORED,
            booklet_id=booklet.id,
            answer_id=answer_id,
            after={"answer_score_id": str(score.id), "mark": str(score.mark)},
        )
        return score
