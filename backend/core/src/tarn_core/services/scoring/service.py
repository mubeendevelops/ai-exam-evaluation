"""Score one answer against its rubric with the configured scorers, recording every content
and scorer version used (rule 11). Similarity feeds a criterion's credit, never the mark.

Per answer: the text (struck-out lines left out), sentence vectors (stored in pgvector and
reused while the sentence is unchanged), one credit per criterion, the off-target guard, and
the flags (low OCR, borderline credit per criterion, off-target, blank, mark manually,
duplicate). A guidance-only key gives a "mark manually" score with no mark."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import Region
from tarn_core.domain.common import ContentRef, EngineRef
from tarn_core.domain.content import (
    DiagramParams,
    Glossary,
    ListParams,
    NumericParams,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    Rubric,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.domain.diagram import DiagramGraph
from tarn_core.domain.scoring import (
    AnswerFlag,
    AnswerScore,
    CriterionReason,
    CriterionScore,
    SentenceVector,
    answer_mark,
)
from tarn_core.errors import DomainError, InvariantError
from tarn_core.ids import AnswerId, AnswerScoreId, BookletId, CollegeId, RegionId, SegmentId, UserId
from tarn_core.ports.engines import (
    Embedder,
    Scorer,
    ScoringCalibrationStore,
    ScoringInput,
    WordList,
)
from tarn_core.ports.repositories import BookletRepository, ContentRepository, ScoreRepository
from tarn_core.services._support import Runtime
from tarn_core.services.scoring.assemble import AnswerText, candidate_texts
from tarn_core.services.scoring.guard import GuardResult, OffTargetGuard
from tarn_core.services.scoring.policy import ScoringPolicy
from tarn_core.services.scoring.scorers import ListScorer, NumericScorer, SemanticScorer
from tarn_core.services.scoring.text import tokens

UNSCORED = EngineRef(name="unscored", version="1")
"""Stands in for a scorer on criteria no configured scorer handles yet (diagrams before P14,
LLM criteria while the LLM is off): credit 0, flagged ``manual`` for the teacher."""

MANUAL = "manual"


class NoScorerError(DomainError):
    """No configured scorer supports a criterion's type."""


@dataclass(frozen=True, slots=True)
class _Content:
    blueprint: ExamBlueprint
    question: Question
    criteria: tuple[RubricCriterion, ...]
    keys: tuple[ReferenceAnswer, ...]
    glossaries: tuple[Glossary, ...]
    used: frozenset[ContentRef]


@dataclass(frozen=True, slots=True)
class _Attempt:
    text: AnswerText
    vectors: tuple[tuple[float, ...], ...]
    criteria: tuple[CriterionScore, ...]
    guard: GuardResult

    @property
    def total(self) -> Decimal:
        return sum((c.marks for c in self.criteria), Decimal(0))


class ScoringService:
    def __init__(
        self,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        scorers: Sequence[Scorer],
        runtime: Runtime,
        embedder: Embedder | None = None,
        calibrations: ScoringCalibrationStore | None = None,
        word_list: WordList | None = None,
        policy: ScoringPolicy | None = None,
        strict: bool = False,
    ):
        """``embedder`` is the local sentence model shared by the semantic scorer and the
        guard; ``policy`` overrides the stored calibration (calibration runs); ``strict``
        raises ``NoScorerError`` instead of leaving unsupported criteria to the teacher."""
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._scorers = tuple(scorers)
        self._rt = runtime
        self._embedder = embedder
        self._strict = strict
        if policy is None:
            calibration = (
                None
                if calibrations is None or embedder is None
                else calibrations.latest(embedder.ref.name)
            )
            policy = ScoringPolicy.from_calibration(calibration)
        self.policy = policy
        self._guard = OffTargetGuard(embedder, policy, word_list=word_list)
        self._vocabulary: dict[ContentRef, frozenset[str]] = {}

    def score_answer(
        self,
        college_id: CollegeId,
        actor_id: UserId | None,
        answer_id: AnswerId,
        *,
        answer_text: str | None = None,
        diagrams: tuple[DiagramGraph, ...] = (),
    ) -> AnswerScore:
        """Score and store. ``answer_text`` replaces the segments' text (tests, calibration);
        ``actor_id`` None = the pipeline."""
        answer = self._booklets.get_answer(college_id, answer_id)
        booklet = self._booklets.get(college_id, answer.booklet_id)
        content = self._load(booklet.blueprint, answer.slot_label)
        if answer_text is not None:
            texts = [AnswerText.from_text(answer_text)]
        else:
            texts = self._texts(college_id, answer.booklet_id, answer.segment_ids)
        score = self.evaluate(
            college_id, answer_id, content=content, texts=texts, diagrams=diagrams
        )
        self._scores.save_score(college_id, score)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.ANSWER_SCORED,
            booklet_id=booklet.id,
            answer_id=answer_id,
            after={
                "answer_score_id": str(score.id),
                "mark": None if score.mark is None else str(score.mark),
                "flags": [f.value for f in score.flags],
            },
        )
        return score

    def evaluate(
        self,
        college_id: CollegeId,
        answer_id: AnswerId,
        *,
        content: _Content,
        texts: Sequence[AnswerText],
        diagrams: tuple[DiagramGraph, ...] = (),
    ) -> AnswerScore:
        """The score without storing it (the score itself; vectors may be cached)."""
        c = content
        used = set(c.used)
        if self.policy.calibration is not None:
            used.add(self.policy.calibration)
        if not c.criteria:
            return AnswerScore(
                id=self._rt.new_id(AnswerScoreId),
                college_id=college_id,
                answer_id=answer_id,
                question=c.question.ref,
                criterion_scores=(),
                mark_step=c.blueprint.mark_step,
                mark=None,
                content_versions=frozenset(used),
                created_at=self._rt.clock.now(),
                flags=(AnswerFlag.MARK_MANUALLY,),
                reasons=("the key is guidance only: mark this answer manually",),
            )
        for criterion in c.criteria:
            if isinstance(criterion.params, DiagramParams):
                diagram_id = criterion.params.reference_diagram_id
                used.add(self._content.get(ReferenceDiagram, diagram_id).ref)

        attempts = [
            self._attempt(college_id, answer_id, c, text, diagrams, store=len(texts) == 1)
            for text in texts
        ]
        best = max(range(len(attempts)), key=lambda k: (attempts[k].total, -k))
        chosen = attempts[best]
        if len(attempts) > 1 and self._embedder is not None:
            self._store_vectors(college_id, answer_id, chosen.text, chosen.vectors)

        flags: list[AnswerFlag] = []
        reasons: list[str] = []
        if chosen.text.words < self.policy.blank_words:
            flags.append(AnswerFlag.BLANK)
            reasons.append("blank or nearly blank: 0 suggested, please confirm")
        if chosen.text.lines and chosen.text.low_ocr_share() >= self.policy.low_ocr_share:
            flags.append(AnswerFlag.LOW_OCR)
            reasons.append(
                f"{chosen.text.low_ocr_lines} of {chosen.text.lines} lines were hard to read"
            )
        if chosen.guard.off_target and AnswerFlag.BLANK not in flags:
            flags.append(AnswerFlag.OFF_TARGET)
            reasons.extend(chosen.guard.reasons)
        if len(attempts) > 1:
            flags.append(AnswerFlag.DUPLICATE)
            reasons.append(
                f"answered {len(attempts)} times: copy {best + 1} scored highest and is "
                "suggested; every copy is kept for the teacher"
            )
        if chosen.text.struck_out:
            reasons.append(f"{chosen.text.struck_out} struck-out line(s) left out")

        criteria = chosen.criteria
        return AnswerScore(
            id=self._rt.new_id(AnswerScoreId),
            college_id=college_id,
            answer_id=answer_id,
            question=c.question.ref,
            criterion_scores=criteria,
            mark_step=c.blueprint.mark_step,
            mark=answer_mark(criteria, c.blueprint.mark_step),
            content_versions=frozenset(used),
            created_at=self._rt.clock.now(),
            flags=tuple(flags),
            relevance=chosen.guard.relevance,
            embedder=None if self._embedder is None else self._embedder.ref,
            reasons=tuple(reasons),
        )

    @classmethod
    def standard(
        cls,
        *,
        booklets: BookletRepository,
        scores: ScoreRepository,
        content: ContentRepository,
        runtime: Runtime,
        embedder: Embedder | None,
        calibrations: ScoringCalibrationStore | None = None,
        word_list: WordList | None = None,
        policy: ScoringPolicy | None = None,
        extra: Sequence[Scorer] = (),
    ) -> "ScoringService":
        """The non-LLM scorers (list, numeric and, with an embedder, semantic) sharing one
        policy; ``extra`` scorers (diagrams in P14, the LLM in P19) come after them."""
        if policy is None:
            calibration = (
                None
                if calibrations is None or embedder is None
                else calibrations.latest(embedder.ref.name)
            )
            policy = ScoringPolicy.from_calibration(calibration)
        scorers: list[Scorer] = [ListScorer(), NumericScorer()]
        if embedder is not None:
            scorers.append(SemanticScorer(embedder, policy))
        return cls(
            booklets=booklets,
            scores=scores,
            content=content,
            scorers=[*scorers, *extra],
            runtime=runtime,
            embedder=embedder,
            word_list=word_list,
            policy=policy,
        )

    def content_for(self, blueprint_ref: ContentRef, slot_label: str) -> _Content:
        return self._load(blueprint_ref, slot_label)

    # --- internals ------------------------------------------------------------------------

    def _load(self, blueprint_ref: ContentRef, slot_label: str) -> _Content:
        blueprint = self._content.get(ExamBlueprint, blueprint_ref.id, blueprint_ref.version)
        _, question_id, _ = blueprint.leaf(slot_label)
        question = self._content.get(Question, question_id)
        keys = tuple(self._content.for_question(ReferenceAnswer, question_id))
        criteria = tuple(self._content.for_question(RubricCriterion, question_id))
        glossaries = tuple(self._content.for_question(Glossary, question_id))
        if criteria:
            Rubric(question=question, criteria=criteria)  # weights sum to the question's marks
        used: set[ContentRef] = {
            blueprint.ref,
            question.ref,
            *(cr.ref for cr in criteria),
            *(k.ref for k in keys),
            *(g.ref for g in glossaries),
        }
        return _Content(
            blueprint=blueprint,
            question=question,
            criteria=criteria,
            keys=keys,
            glossaries=glossaries,
            used=frozenset(used),
        )

    def _texts(
        self, college_id: CollegeId, booklet_id: BookletId, segment_ids: Sequence[SegmentId]
    ) -> list[AnswerText]:
        wanted = set(segment_ids)
        segments = [s for s in self._booklets.segments(college_id, booklet_id) if s.id in wanted]
        regions: dict[RegionId, Region] = {}
        for page_id in {p for s in segments for p in s.page_ids}:
            for region in self._booklets.regions(college_id, page_id):
                regions[region.id] = region
        return candidate_texts(segments, regions)

    def _attempt(
        self,
        college_id: CollegeId,
        answer_id: AnswerId,
        c: _Content,
        text: AnswerText,
        diagrams: tuple[DiagramGraph, ...],
        *,
        store: bool,
    ) -> _Attempt:
        vectors = self._vectors(college_id, answer_id, text, store=store)
        usable_keys = [k for k in c.keys if not k.guidance_only]
        reference_text = "\n".join(k.text for k in usable_keys)
        glossary_terms = tuple(t for g in c.glossaries for t in g.terms)
        results = []
        for criterion in c.criteria:
            scorer = next((s for s in self._scorers if s.supports(criterion.type)), None)
            if scorer is None:
                if self._strict:
                    raise NoScorerError(f"no scorer for {criterion.type} criteria")
                results.append(self._unscored(criterion))
                continue
            result = scorer.score(
                ScoringInput(
                    criterion=criterion,
                    answer_text=text.text,
                    diagrams=diagrams,
                    reference_text=reference_text,
                    glossary=glossary_terms,
                    sentences=text.sentences,
                    sentence_vectors=vectors,
                    embedder=None if self._embedder is None else self._embedder.ref,
                )
            )
            if result.criterion != criterion.ref or result.weight != criterion.weight:
                raise InvariantError(f"{scorer.ref.name} scored a different criterion version")
            results.append(result)
        statements = [
            cr.params.reference_statement
            for cr in c.criteria
            if isinstance(cr.params, SemanticParams)
        ]
        guard = self._guard.check(
            text.sentences,
            vectors,
            key_texts=[c.question.text, *(k.text for k in usable_keys), *statements],
            off_target_terms=[t for g in c.glossaries for t in g.off_target_terms],
            vocabulary=self._exam_vocabulary(c.blueprint),
        )
        return _Attempt(text=text, vectors=vectors, criteria=tuple(results), guard=guard)

    @staticmethod
    def _unscored(criterion: RubricCriterion) -> CriterionScore:
        return CriterionScore(
            criterion=criterion.ref,
            weight=criterion.weight,
            credit=Decimal(0),
            scorer=UNSCORED,
            evidence=f"no {criterion.type} scorer yet: the teacher marks this criterion",
            flags=(MANUAL,),
            reason=CriterionReason(
                summary=f"no {criterion.type} scorer yet: the teacher marks this criterion"
            ),
        )

    def _vectors(
        self, college_id: CollegeId, answer_id: AnswerId, text: AnswerText, *, store: bool
    ) -> tuple[tuple[float, ...], ...]:
        if self._embedder is None or not text.sentences:
            return ()
        ref = self._embedder.ref
        hashes = [_sha(s) for s in text.sentences]
        stored = {v.index: v for v in self._scores.vectors(college_id, answer_id, ref)}
        if len(stored) == len(hashes) and all(
            stored.get(i) is not None and stored[i].text_sha256 == h for i, h in enumerate(hashes)
        ):
            return tuple(stored[i].vector for i in range(len(hashes)))
        vectors = tuple(tuple(v) for v in self._embedder.embed(list(text.sentences)))
        if store:
            self._store_vectors(college_id, answer_id, text, vectors)
        return vectors

    def _store_vectors(
        self,
        college_id: CollegeId,
        answer_id: AnswerId,
        text: AnswerText,
        vectors: Sequence[tuple[float, ...]],
    ) -> None:
        if self._embedder is None or len(vectors) != len(text.sentences):
            return
        self._scores.replace_vectors(
            college_id,
            answer_id,
            self._embedder.ref,
            [
                SentenceVector(index=i, text_sha256=_sha(s), vector=v)
                for i, (s, v) in enumerate(zip(text.sentences, vectors, strict=True))
            ],
        )

    def _exam_vocabulary(self, blueprint: ExamBlueprint) -> frozenset[str]:
        """Lower-case words of every question, key, criterion and glossary on the paper."""
        if blueprint.ref not in self._vocabulary:
            words: set[str] = set()
            for slot in blueprint.slots():
                for _, question_id, _ in leaves(slot):
                    if question_id is None:
                        continue
                    words.update(tokens(self._content.get(Question, question_id).text))
                    for key in self._content.for_question(ReferenceAnswer, question_id):
                        words.update(tokens(key.text))
                    for g in self._content.for_question(Glossary, question_id):
                        for term in (*g.terms, *g.off_target_terms):
                            words.update(tokens(term))
                    for cr in self._content.for_question(RubricCriterion, question_id):
                        words.update(tokens(cr.label))
                        words.update(_criterion_words(cr))
            self._vocabulary[blueprint.ref] = frozenset(words)
        return self._vocabulary[blueprint.ref]


def _criterion_words(criterion: RubricCriterion) -> set[str]:
    params = criterion.params
    if isinstance(params, ListParams):
        return {w for item in params.items for t in (item.term, *item.synonyms) for w in tokens(t)}
    if isinstance(params, SemanticParams):
        return set(tokens(params.reference_statement))
    if isinstance(params, NumericParams):
        return set(tokens(params.unit))
    return set()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
