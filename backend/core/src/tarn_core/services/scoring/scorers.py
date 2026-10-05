"""The non-LLM criterion scorers (design.md "Text scoring (R5)"), each behind the ``Scorer``
port. Similarity feeds a criterion's credit; it is never the mark (C25).

* ``ListScorer``: items with synonyms, OCR-tolerant, counted up to the required N.
* ``NumericScorer``: one step, a number within tolerance of the expected value.
* ``SemanticScorer``: best match between the reference statement and the student's sentences
  (or two neighbouring sentences: OCR and handwriting split sentences), banded to 0, 1/2, 1.

Reasons name key-side wording and sentence positions, never copy the student's text."""

import math
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import CriterionType, ListParams, NumericParams, SemanticParams
from tarn_core.domain.scoring import CHECK, CriterionReason, CriterionScore
from tarn_core.errors import InvariantError
from tarn_core.ports.engines import Embedder, ScoringInput
from tarn_core.services.scoring.policy import ScoringPolicy
from tarn_core.services.scoring.text import (
    PhraseHit,
    Token,
    extract_numbers,
    find_phrase,
    sentence_tokens,
    split_sentences,
    word_matches,
)

type Vector = tuple[float, ...]

_CREDIT_PLACES = Decimal("0.0001")


def _sentences(item: ScoringInput) -> tuple[str, ...]:
    if item.sentences:
        return item.sentences
    return tuple(split_sentences(item.answer_text.splitlines()))


def _params[P](item: ScoringInput, kind: type[P]) -> P:
    params = item.criterion.params
    if not isinstance(params, kind):
        raise InvariantError(f"{item.criterion.type} criterion given to the wrong scorer")
    return params


def _score(
    item: ScoringInput,
    scorer: EngineRef,
    credit: Decimal,
    reason: CriterionReason,
    *,
    flags: tuple[str, ...] = (),
    similarity: float | None = None,
) -> CriterionScore:
    return CriterionScore(
        criterion=item.criterion.ref,
        weight=item.criterion.weight,
        credit=credit,
        scorer=scorer,
        evidence=reason.summary,
        flags=flags,
        similarity=similarity,
        reason=reason,
    )


class ListScorer:
    """Credit = distinct items written / required N, capped at 1. An item counts once however
    often it is written, and one stretch of the answer counts for one item only."""

    ref = EngineRef(name="list", version="1")

    def supports(self, criterion_type: CriterionType) -> bool:
        return criterion_type is CriterionType.LIST

    def score(self, item: ScoringInput) -> CriterionScore:
        params = _params(item, ListParams)
        words = sentence_tokens(_sentences(item))
        used: set[int] = set()
        matched: list[str] = []
        missing: list[str] = []
        sentences: list[int] = []
        for entry in params.items:
            hit = self._first_free_hit((entry.term, *entry.synonyms), words, used)
            if hit is None:
                missing.append(entry.term)
                continue
            used.update(range(hit.start, hit.end))
            matched.append(entry.term)
            sentences.append(hit.sentence)
        counted = min(len(matched), params.required_count)
        credit = (Decimal(counted) / Decimal(params.required_count)).quantize(
            _CREDIT_PLACES, rounding=ROUND_HALF_UP
        )
        summary = f"{len(matched)} of {params.required_count} required items written"
        if len(matched) > params.required_count:
            summary += f" ({params.required_count} counted)"
        return _score(
            item,
            self.ref,
            credit,
            CriterionReason(
                summary=summary,
                matched=tuple(matched),
                missing=tuple(missing),
                sentences=tuple(sorted(set(sentences))),
            ),
        )

    @staticmethod
    def _first_free_hit(
        phrases: Sequence[str], words: Sequence[Token], used: set[int]
    ) -> PhraseHit | None:
        best: PhraseHit | None = None
        for phrase in phrases:
            for hit in find_phrase(phrase, words):
                if used.isdisjoint(range(hit.start, hit.end)):
                    if best is None or hit.start < best.start:
                        best = hit
                    break
        return best


class NumericScorer:
    """Credit 1 when any number written is within ``expected ± tolerance``, else 0. A missing
    unit does not cost the step but flags it "check" for the teacher."""

    ref = EngineRef(name="numeric", version="1")

    def supports(self, criterion_type: CriterionType) -> bool:
        return criterion_type is CriterionType.NUMERIC

    def score(self, item: ScoringInput) -> CriterionScore:
        params = _params(item, NumericParams)
        expected = f"{params.expected}" + (f" ± {params.tolerance}" if params.tolerance else "")
        if params.unit:
            expected += f" {params.unit}"
        numbers = extract_numbers(_sentences(item))
        within = [q for q in numbers if abs(q.value - params.expected) <= params.tolerance]
        if not within:
            nearest = min(numbers, key=lambda q: abs(q.value - params.expected), default=None)
            return _score(
                item,
                self.ref,
                Decimal(0),
                CriterionReason(
                    summary="expected value not found"
                    if nearest is None
                    else "no number within tolerance",
                    found=None if nearest is None else f"{nearest.value.normalize()}",
                    expected=expected,
                    sentences=() if nearest is None else (nearest.sentence,),
                ),
            )
        unit = params.unit.casefold()
        with_unit = [q for q in within if not unit or (q.unit and word_matches(unit, q.unit))]
        chosen = (with_unit or within)[0]
        flags: tuple[str, ...] = ()
        summary = "expected value found"
        if unit and not with_unit:
            flags = (CHECK,)
            summary += f"; unit '{params.unit}' not written with it"
        return _score(
            item,
            self.ref,
            Decimal(1),
            CriterionReason(
                summary=summary,
                found=f"{chosen.value.normalize()}",
                expected=expected,
                sentences=(chosen.sentence,),
            ),
            flags=flags,
        )


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (na * nb)))


def windows(vectors: Sequence[Vector]) -> list[tuple[tuple[int, ...], Vector]]:
    """Each sentence, and each pair of neighbouring sentences (the normalised sum)."""
    units: list[tuple[tuple[int, ...], Vector]] = [((i,), v) for i, v in enumerate(vectors)]
    for i in range(len(vectors) - 1):
        a, b = vectors[i], vectors[i + 1]
        na, nb = _norm(a) or 1.0, _norm(b) or 1.0
        summed = tuple(x / na + y / nb for x, y in zip(a, b, strict=True))
        units.append(((i, i + 1), summed))
    return units


def _norm(v: Vector) -> float:
    return math.sqrt(sum(x * x for x in v))


def best_match(target: Vector, vectors: Sequence[Vector]) -> tuple[float, tuple[int, ...]]:
    """The highest cosine between ``target`` and any sentence or neighbouring pair."""
    best: float = 0.0
    where: tuple[int, ...] = ()
    for positions, vector in windows(vectors):
        similarity = cosine(target, vector)
        if similarity > best:
            best, where = similarity, positions
    return best, where


class SemanticScorer:
    """Embeds the reference statement (cached per statement) and the answer's sentences
    (precomputed once per answer when ``ScoringInput.embedder`` is this embedder)."""

    def __init__(self, embedder: Embedder, policy: ScoringPolicy | None = None) -> None:
        self._embedder = embedder
        self.policy = policy or ScoringPolicy()
        self._statements: dict[str, Vector] = {}

    @property
    def ref(self) -> EngineRef:
        e = self._embedder.ref
        return EngineRef(name="semantic", version=f"1+{e.name}@{e.version}")

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    def supports(self, criterion_type: CriterionType) -> bool:
        return criterion_type is CriterionType.SEMANTIC

    def statement_vector(self, statement: str) -> Vector:
        if statement not in self._statements:
            self._statements[statement] = tuple(self._embedder.embed([statement])[0])
        return self._statements[statement]

    def score(self, item: ScoringInput) -> CriterionScore:
        params = _params(item, SemanticParams)
        sentences = _sentences(item)
        if item.embedder == self._embedder.ref and len(item.sentence_vectors) == len(sentences):
            vectors: Sequence[Vector] = item.sentence_vectors
        else:
            vectors = [tuple(v) for v in self._embedder.embed(list(sentences))] if sentences else []
        if not vectors:
            return _score(
                item,
                self.ref,
                Decimal(0),
                CriterionReason(summary="nothing written", missing=(params.reference_statement,)),
                similarity=0.0,
            )
        similarity, where = best_match(self.statement_vector(params.reference_statement), vectors)
        credit, near = self.policy.band(similarity)
        label = {1.0: "full", 0.5: "half"}.get(credit, "no")
        statement = (params.reference_statement,)
        return _score(
            item,
            self.ref,
            Decimal(str(credit)),
            CriterionReason(
                summary=f"best match {similarity:.2f}: {label} credit"
                + (" (near a band edge)" if near else ""),
                matched=statement if credit else (),
                missing=() if credit else statement,
                sentences=where,
            ),
            flags=(CHECK,) if near else (),
            similarity=round(similarity, 4),
        )
