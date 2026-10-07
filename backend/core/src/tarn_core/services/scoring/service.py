"""Score one answer against its rubric with the configured scorers, recording every content
and scorer version used (rule 11). Similarity feeds a criterion's credit, never the mark.

Per answer: the text (struck-out lines left out), sentence vectors (stored in pgvector and
reused while the sentence is unchanged), one credit per criterion, the off-target guard, and
the flags (low OCR, borderline credit per criterion, off-target, blank, mark manually,
duplicate). A guidance-only key gives a "mark manually" score with no mark."""

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Decimal

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint, leaves
from tarn_core.domain.booklet import Region
from tarn_core.domain.common import ContentRef, EngineRef, JsonValue
from tarn_core.domain.content import (
    CriterionType,
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
from tarn_core.domain.diagram import AnswerDiagram
from tarn_core.domain.scoring import (
    DISAGREE,
    AnswerFlag,
    AnswerScore,
    CriterionReason,
    CriterionScore,
    LlmUsage,
    SecondOpinion,
    SentenceVector,
    answer_mark,
    scorers_disagree,
)
from tarn_core.errors import DomainError, InvariantError, ScorerUnavailableError
from tarn_core.ids import (
    AnswerId,
    AnswerScoreId,
    BookletId,
    CollegeId,
    ReferenceDiagramId,
    RegionId,
    SegmentId,
    UserId,
)
from tarn_core.ports.engines import (
    Embedder,
    Scorer,
    ScoringCalibrationStore,
    ScoringInput,
    SecondOpinionScorer,
    WordList,
)
from tarn_core.ports.repositories import (
    BookletRepository,
    CollegeRepository,
    ContentRepository,
    ScoreRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.scoring.assemble import AnswerText, candidate_texts
from tarn_core.services.scoring.changes import change_notice, content_changes
from tarn_core.services.scoring.guard import GuardResult, OffTargetGuard
from tarn_core.services.scoring.policy import ScoringPolicy
from tarn_core.services.scoring.scorers import ListScorer, NumericScorer, SemanticScorer
from tarn_core.services.scoring.text import tokens

UNSCORED = EngineRef(name="unscored", version="1")
"""Stands in for a scorer on criteria no configured scorer handles (diagram criteria when the
diagram scorer is not wired in, LLM criteria while the LLM is off): credit 0, flagged
``manual`` for the teacher."""

MANUAL = "manual"


class NoScorerError(DomainError):
    """No configured scorer supports a criterion's type."""


@dataclass(frozen=True, slots=True)
class ScoringContent:
    """The content one answer is scored against, and every version of it (``used``)."""

    blueprint: ExamBlueprint
    question: Question
    criteria: tuple[RubricCriterion, ...]
    keys: tuple[ReferenceAnswer, ...]
    glossaries: tuple[Glossary, ...]
    reference_diagrams: Mapping[ReferenceDiagramId, ReferenceDiagram]
    slot_label: str
    used: frozenset[ContentRef]


@dataclass(frozen=True, slots=True)
class _Attempt:
    text: AnswerText
    vectors: tuple[tuple[float, ...], ...]
    criteria: tuple[CriterionScore, ...]
    guard: GuardResult
    llm_usage: LlmUsage = field(default_factory=LlmUsage)
    llm_unavailable: int = 0
    """Criteria the LLM was asked about (college flag on) and could not judge."""

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
        llm: SecondOpinionScorer | None = None,
        colleges: CollegeRepository | None = None,
    ):
        """``embedder`` is the local sentence model shared by the semantic scorer and the
        guard; ``policy`` overrides the stored calibration (calibration runs); ``strict``
        raises ``NoScorerError`` instead of leaving unsupported criteria to the teacher.

        ``llm`` is the LLM scorer (P19), off by default: it is used only for a college whose
        ``llm_scoring`` flag is on (read through ``colleges``). It is not one of ``scorers``:
        it judges the criteria they scored, as a second opinion, and is the only scorer of
        ``llm`` criteria."""
        self._booklets = booklets
        self._scores = scores
        self._content = content
        self._scorers = tuple(scorers)
        self._rt = runtime
        self._embedder = embedder
        self._strict = strict
        self._llm = llm
        self._colleges = colleges
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
        diagrams: tuple[AnswerDiagram, ...] | None = None,
    ) -> AnswerScore:
        """Score and store. ``answer_text`` replaces the segments' text (tests, calibration);
        ``diagrams`` replaces the diagrams recognised in the answer's segments; ``actor_id``
        None = the pipeline."""
        answer = self._booklets.get_answer(college_id, answer_id)
        booklet = self._booklets.get(college_id, answer.booklet_id)
        content = self._load(booklet.blueprint, answer.slot_label)
        if answer_text is not None:
            texts = [AnswerText.from_text(answer_text)]
        else:
            texts = self._texts(college_id, answer.booklet_id, answer.segment_ids)
        if diagrams is None:
            segments = set(answer.segment_ids)
            diagrams = tuple(
                AnswerDiagram(graph=d.graph, diagram_id=str(d.id), version=d.version)
                for d in self._booklets.diagrams(college_id, answer.booklet_id)
                if d.segment_id in segments
            )
        score = self.evaluate(
            college_id, answer_id, content=content, texts=texts, diagrams=diagrams
        )
        previous = self._scores.scores(college_id, answer_id)
        if previous:
            changes = content_changes(previous[-1].content_versions, score.content_versions)
            if changes:
                score = replace(score, reasons=(*score.reasons, change_notice(changes)))
        self._scores.save_score(college_id, score)
        # A new suggestion: a screen showing the old one is now stale (D110).
        self._booklets.save_answer(
            college_id, replace(answer, version=answer.version + 1, rescore_pending=False)
        )
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
                "scorers": scorer_trail(score),
                "llm_usage": usage_json(score),
            },
        )
        return score

    def evaluate(
        self,
        college_id: CollegeId,
        answer_id: AnswerId,
        *,
        content: ScoringContent,
        texts: Sequence[AnswerText],
        diagrams: tuple[AnswerDiagram, ...] = (),
    ) -> AnswerScore:
        """The score without storing it (the score itself; vectors may be cached)."""
        c = content
        llm = self._llm_for(college_id)
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
        attempts = [
            self._attempt(college_id, answer_id, c, text, diagrams, llm, store=len(texts) == 1)
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
        disagreeing = sum(1 for cs in chosen.criteria if DISAGREE in cs.flags)
        if disagreeing:
            flags.append(AnswerFlag.SCORER_DISAGREEMENT)
            reasons.append(
                f"the LLM and the other scorer differ by more than one band on {disagreeing} "
                "criterion(s): both credits are shown, look at these yourself"
            )
        unavailable = sum(a.llm_unavailable for a in attempts)
        if unavailable:
            reasons.append(
                f"the LLM scorer was unavailable for {unavailable} criterion(s): they show the "
                "other scorer's credit only, or go to you"
            )
        llm_usage = sum((a.llm_usage for a in attempts), LlmUsage())

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
            llm_usage=llm_usage or None,
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
        llm: SecondOpinionScorer | None = None,
        colleges: CollegeRepository | None = None,
    ) -> "ScoringService":
        """The non-LLM scorers (list, numeric and, with an embedder, semantic) sharing one
        policy; ``extra`` scorers (diagrams in P14) come after them. ``llm`` (P19) is the LLM
        scorer, used for the colleges whose ``llm_scoring`` flag is on."""
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
            llm=llm,
            colleges=colleges,
        )

    def content_for(self, blueprint_ref: ContentRef, slot_label: str) -> ScoringContent:
        return self._load(blueprint_ref, slot_label)

    # --- internals ------------------------------------------------------------------------

    def _load(self, blueprint_ref: ContentRef, slot_label: str) -> ScoringContent:
        return scoring_content(self._content, blueprint_ref, slot_label)

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
        c: ScoringContent,
        text: AnswerText,
        diagrams: tuple[AnswerDiagram, ...],
        llm: SecondOpinionScorer | None,
        *,
        store: bool,
    ) -> _Attempt:
        vectors = self._vectors(college_id, answer_id, text, store=store)
        usable_keys = [k for k in c.keys if not k.guidance_only]
        reference_text = "\n".join(k.text for k in usable_keys)
        glossary_terms = tuple(t for g in c.glossaries for t in g.terms)

        def make_input(criterion: RubricCriterion) -> ScoringInput:
            return ScoringInput(
                criterion=criterion,
                answer_text=text.text,
                diagrams=diagrams,
                reference_diagrams=c.reference_diagrams,
                question_code=c.question.code,
                question_text=c.question.text,
                slot_label=c.slot_label,
                reference_text=reference_text,
                glossary=glossary_terms,
                sentences=text.sentences,
                sentence_vectors=vectors,
                embedder=None if self._embedder is None else self._embedder.ref,
            )

        results = []
        for criterion in c.criteria:
            scorer = next((s for s in self._scorers if s.supports(criterion.type)), None)
            if scorer is None:
                if self._strict:
                    raise NoScorerError(f"no scorer for {criterion.type} criteria")
                results.append(self._unscored(criterion))
                continue
            result = scorer.score(make_input(criterion))
            if result.criterion != criterion.ref or result.weight != criterion.weight:
                raise InvariantError(f"{scorer.ref.name} scored a different criterion version")
            results.append(result)
        usage = LlmUsage()
        unavailable = 0
        if llm is not None:
            results, usage, unavailable = self._ask_llm(llm, c.criteria, results, make_input)
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
        return _Attempt(
            text=text,
            vectors=vectors,
            criteria=tuple(results),
            guard=guard,
            llm_usage=usage,
            llm_unavailable=unavailable,
        )

    def _ask_llm(
        self,
        llm: SecondOpinionScorer,
        criteria: Sequence[RubricCriterion],
        results: Sequence[CriterionScore],
        make_input: Callable[[RubricCriterion], ScoringInput],
    ) -> tuple[list[CriterionScore], LlmUsage, int]:
        """The LLM's judgement of each criterion it can judge: the only score of an ``llm``
        criterion, a second opinion beside every other scorer's credit. After the first
        criterion it fails on, it is not asked again for this answer (bounded waiting): the
        rest keep their own scorer's credit, and ``llm`` criteria go to the teacher."""
        out: list[CriterionScore] = []
        usage = LlmUsage()
        unavailable = 0
        down = False
        for criterion, result in zip(criteria, results, strict=True):
            primary = criterion.type is CriterionType.LLM and result.scorer == UNSCORED
            second = (
                criterion.type is not CriterionType.LLM
                and result.scorer != UNSCORED
                and llm.supports(criterion.type)
            )
            if not (primary or second):
                out.append(result)
                continue
            if not down:
                try:
                    opinion, used = llm.opinion(make_input(criterion))
                except ScorerUnavailableError:
                    down = True
                else:
                    usage += used
                    out.append(
                        self._from_opinion(criterion, opinion)
                        if primary
                        else self._with_opinion(result, opinion)
                    )
                    continue
            unavailable += 1
            out.append(
                self._unscored(criterion, "the LLM scorer was unavailable") if primary else result
            )
        return out, usage, unavailable

    def _llm_for(self, college_id: CollegeId) -> SecondOpinionScorer | None:
        """The LLM scorer when this college has it switched on (off by default)."""
        if self._llm is None or self._colleges is None:
            return None
        return self._llm if self._colleges.get(college_id).llm_scoring else None

    @staticmethod
    def _with_opinion(result: CriterionScore, opinion: SecondOpinion) -> CriterionScore:
        """``result`` with the LLM's credit beside it, flagged when they differ by more than
        one band. The mark keeps using ``result``'s credit."""
        flags = result.flags
        if scorers_disagree(result.credit, opinion.credit):
            flags = (*flags, DISAGREE)
        return replace(result, second_opinion=opinion, flags=flags)

    @staticmethod
    def _from_opinion(criterion: RubricCriterion, opinion: SecondOpinion) -> CriterionScore:
        """An ``llm`` criterion: the model's credit is the suggestion."""
        return CriterionScore(
            criterion=criterion.ref,
            weight=criterion.weight,
            credit=opinion.credit,
            scorer=opinion.scorer,
            evidence=opinion.reason,
            reason=CriterionReason(summary=opinion.reason),
        )

    @staticmethod
    def _unscored(criterion: RubricCriterion, why: str | None = None) -> CriterionScore:
        note = (
            f"{why}: the teacher marks this criterion"
            if why
            else f"no {criterion.type} scorer yet: the teacher marks this criterion"
        )
        return CriterionScore(
            criterion=criterion.ref,
            weight=criterion.weight,
            credit=Decimal(0),
            scorer=UNSCORED,
            evidence=note,
            flags=(MANUAL,),
            reason=CriterionReason(summary=note),
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


def scoring_content(
    content: ContentRepository, blueprint_ref: ContentRef, slot_label: str
) -> ScoringContent:
    """What an answer to ``slot_label`` of this blueprint is scored against now: the latest
    live key, rubric, glossaries and reference diagrams of the question."""
    blueprint = content.get(ExamBlueprint, blueprint_ref.id, blueprint_ref.version)
    _, question_id, _ = blueprint.leaf(slot_label)
    question = content.get(Question, question_id)
    keys = tuple(content.for_question(ReferenceAnswer, question_id))
    criteria = tuple(content.for_question(RubricCriterion, question_id))
    glossaries = tuple(content.for_question(Glossary, question_id))
    reference_diagrams = {
        cr.params.reference_diagram_id: content.get(
            ReferenceDiagram, cr.params.reference_diagram_id
        )
        for cr in criteria
        if isinstance(cr.params, DiagramParams)
    }
    if criteria:
        Rubric(question=question, criteria=criteria)  # weights sum to the question's marks
    used: set[ContentRef] = {
        blueprint.ref,
        question.ref,
        *(cr.ref for cr in criteria),
        *(k.ref for k in keys),
        *(g.ref for g in glossaries),
        *(d.ref for d in reference_diagrams.values()),
    }
    return ScoringContent(
        blueprint=blueprint,
        question=question,
        criteria=criteria,
        keys=keys,
        glossaries=glossaries,
        reference_diagrams=reference_diagrams,
        slot_label=slot_label,
        used=frozenset(used),
    )


def scorer_trail(score: AnswerScore) -> list[JsonValue]:
    """Which scorer produced each criterion's suggestion (and the LLM's second opinion), for
    the audit log: names, versions and criterion ids only."""
    return [
        {
            "criterion": f"{c.criterion.id}@{c.criterion.version}",
            "scorer": f"{c.scorer.name} {c.scorer.version}",
            "second_opinion": None
            if c.second_opinion is None
            else f"{c.second_opinion.scorer.name} {c.second_opinion.scorer.version}",
            "disagree": DISAGREE in c.flags,
        }
        for c in score.criterion_scores
    ]


def usage_json(score: AnswerScore) -> JsonValue:
    """What the LLM cost for this score, as counts (no key contains 'token': the audit log
    refuses those words); None when the LLM was not used."""
    u = score.llm_usage
    if u is None:
        return None
    return {"calls": u.calls, "input": u.input_tokens, "output": u.output_tokens}


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
