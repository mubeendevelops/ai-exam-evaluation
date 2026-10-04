"""The selector (design.md "Choosing the best reading", C22):

    S(r_e) = w(e,c) · p̂_e(conf) + α · agree(r_e) + β · lex(r_e)

* p̂ — the engine's raw confidence through its isotonic calibration for the line's class
  (identity until fitted);
* agree — 1 minus the mean normalised edit distance to the other competing readings of the
  line (0 when the reading is the only one);
* lex — share of the reading's words found in the lexicon (question glossaries, key
  vocabulary, an English word list);
* w — the engine's weight for the class (1 until fitted).

The highest S wins (ties: higher p̂, then the engine's place in the class's configured set).
Every reading is kept with its score. A line is flagged for the teacher when the winner's S,
divided by the best S the line could have had (``w_best + α·[≥ 2 readings] + β·[any words]``),
is below the threshold: a line read by fewer engines is not flagged for that alone."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import ContentRef
from tarn_core.domain.ocr import ContentClass, EngineCalibration, ReadingScore, SelectorSettings
from tarn_core.ports.engines import WordList
from tarn_core.services.ocr.text import normalise, normalised_distance, words

NUMERIC_CHARACTERS = frozenset("0123456789.,+-−×÷*/=%()^:<>")

type Calibrations = Mapping[tuple[str, ContentClass], EngineCalibration]


class Lexicon:
    """Words the selector counts as known: content terms first, then the English word list."""

    def __init__(self, terms: frozenset[str] = frozenset(), word_list: WordList | None = None):
        self._terms = frozenset(w for term in terms for w in words(term))
        self._words = word_list

    def __contains__(self, word: str) -> bool:
        word = word.casefold()
        if word in self._terms:
            return True
        return self._words is not None and self._words.contains(word)

    def fit(self, text: str) -> float | None:
        """Share of the text's words that are known; None when it has no words."""
        found = words(text)
        if not found:
            return None
        return sum(1 for w in found if w in self) / len(found)


@dataclass(frozen=True, slots=True, kw_only=True)
class LineChoice:
    scores: tuple[ReadingScore, ...]
    chosen: int | None
    line_score: float | None
    flagged: bool
    calibrations: tuple[ContentRef, ...]


def _agreement(index: int, texts: Sequence[str], others: Sequence[int]) -> float:
    peers = [j for j in others if j != index]
    if not peers:
        return 0.0
    return 1.0 - sum(normalised_distance(texts[index], texts[j]) for j in peers) / len(peers)


def classify_line(readings: Sequence[LineReading], settings: SelectorSettings) -> ContentClass:
    """Numeric when most characters are digits and signs (and there is a digit); print when
    the print engines agree confidently; otherwise cursive (handwriting). Placeholder rules
    until the benchmark (P11) measures them."""
    pooled = "".join(ch for r in readings for ch in r.text if not ch.isspace())
    if not pooled:
        return ContentClass.CURSIVE
    numeric = sum(1 for ch in pooled if ch in NUMERIC_CHARACTERS)
    if numeric / len(pooled) >= settings.numeric_share and any(ch.isdigit() for ch in pooled):
        return ContentClass.NUMERIC
    printed = [r for r in readings if r.engine.name in settings.print_engines and r.text.strip()]
    if len(printed) >= 2:
        texts = [r.text for r in printed]
        pairs = [(i, j) for i in range(len(texts)) for j in range(i + 1, len(texts))]
        agreement = 1 - sum(normalised_distance(texts[i], texts[j]) for i, j in pairs) / len(pairs)
        confidence = sum(r.confidence for r in printed) / len(printed)
        if agreement >= settings.print_agreement and confidence >= settings.print_confidence:
            return ContentClass.PRINT
    return ContentClass.CURSIVE


def select(
    readings: Sequence[LineReading],
    content_class: ContentClass,
    *,
    settings: SelectorSettings,
    calibrations: Calibrations,
    lexicon: Lexicon,
) -> LineChoice:
    """Score every reading of one line and pick the winner among the class's engines (all
    readings compete when none of them belongs to the class's set)."""
    if not readings:
        return LineChoice(scores=(), chosen=None, line_score=None, flagged=True, calibrations=())
    engine_set = settings.engine_sets[content_class]
    competing = [i for i, r in enumerate(readings) if r.engine.name in engine_set]
    if not competing:
        competing = list(range(len(readings)))
    texts = [r.text for r in readings]
    lex = [lexicon.fit(t) for t in texts]
    any_words = any(lex[i] is not None for i in competing)
    used: dict[str, ContentRef] = {}
    scores: list[ReadingScore] = []
    for i, reading in enumerate(readings):
        calibration = calibrations.get((reading.engine.name, content_class))
        if calibration is not None:
            used[reading.engine.name] = calibration.ref
            p_hat = calibration.calibrate(reading.confidence)
            weight = calibration.weight
        else:
            p_hat, weight = reading.confidence, 1.0
        agree = _agreement(i, texts, competing)
        lexicon_fit = lex[i] or 0.0
        score = weight * p_hat + settings.alpha * agree + settings.beta * lexicon_fit
        scores.append(
            ReadingScore(
                calibrated=p_hat,
                agreement=agree,
                lexicon=lexicon_fit,
                weight=weight,
                score=score,
                competing=i in competing,
            )
        )

    def rank(i: int) -> tuple[float, float, int]:
        name = readings[i].engine.name
        order = engine_set.index(name) if name in engine_set else len(engine_set)
        return (-scores[i].score, -scores[i].calibrated, order)

    chosen = min(competing, key=rank)
    best = scores[chosen]
    ceiling = (
        best.weight
        + (settings.alpha if len(competing) >= 2 else 0.0)
        + (settings.beta if any_words else 0.0)
    )
    line_score = min(1.0, best.score / ceiling) if ceiling > 0 else 0.0
    flagged = line_score < settings.flag_threshold or not normalise(readings[chosen].text)
    return LineChoice(
        scores=tuple(scores),
        chosen=chosen,
        line_score=line_score,
        flagged=flagged,
        calibrations=tuple(used[name] for name in sorted(used)),
    )
