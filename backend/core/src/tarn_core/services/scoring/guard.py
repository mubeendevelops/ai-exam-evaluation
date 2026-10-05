"""Off-target guard (C13, design.md "Text scoring"): two checks on every answer.

1. **Relevance** of the whole answer to the question and its key: the mean of the answer's
   best sentence similarities to the question and key sentences. Low relevance flags it.
2. **Names that contradict the key**: the glossary's teacher-written ``off_target_terms``
   (e.g. "Congress" in an answer on the Indian President) flag it outright; capitalised names
   found nowhere in the exam's content (and not common English words) flag it only when
   relevance is also middling (``relevance_soft``), since students capitalise freely and OCR
   invents words.

A fluent off-target answer can still earn keyword credit; the guard only raises the flag and
the teacher decides (requirements: "human judgement, AI flags")."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from tarn_core.ports.engines import Embedder, WordList
from tarn_core.services.scoring.policy import ScoringPolicy
from tarn_core.services.scoring.scorers import Vector, cosine
from tarn_core.services.scoring.text import (
    find_phrase,
    sentence_tokens,
    split_sentences,
    tokens,
)

_CAPITALISED = re.compile(r"\b[A-Z][a-z]{2,}\b")


@dataclass(frozen=True, slots=True, kw_only=True)
class GuardResult:
    relevance: float | None
    """None when there was no embedder or nothing to compare."""
    contradicting: tuple[str, ...]
    """Off-target terms (as the teacher wrote them) found in the answer."""
    unfamiliar: int
    """Capitalised names unknown to the exam's content (a count: the words stay in the text)."""
    off_target: bool
    reasons: tuple[str, ...]


class OffTargetGuard:
    def __init__(
        self,
        embedder: Embedder | None,
        policy: ScoringPolicy,
        *,
        word_list: WordList | None = None,
    ) -> None:
        self._embedder = embedder
        self._policy = policy
        self._words = word_list
        self._targets: dict[tuple[str, ...], list[Vector]] = {}

    def check(
        self,
        sentences: Sequence[str],
        vectors: Sequence[Vector],
        *,
        key_texts: Sequence[str],
        off_target_terms: Sequence[str],
        vocabulary: frozenset[str],
    ) -> GuardResult:
        """``key_texts``: the question, its usable keys and the semantic statements;
        ``vocabulary``: lower-case words of the whole exam's content (questions, keys,
        criteria, glossaries); ``vectors``: one per sentence from this guard's embedder."""
        relevance = self._relevance(vectors, key_texts) if sentences else None

        words = sentence_tokens(sentences)
        contradicting = tuple(t for t in off_target_terms if t.strip() and find_phrase(t, words))
        unfamiliar = self._unfamiliar(sentences, vocabulary)

        reasons: list[str] = []
        if relevance is not None and relevance < self._policy.relevance_min:
            reasons.append(f"low relevance to the question and key ({relevance:.2f})")
        if contradicting:
            reasons.append("names that contradict the key: " + ", ".join(contradicting))
        if (
            unfamiliar
            and relevance is not None
            and relevance < self._policy.relevance_soft
            and not contradicting
        ):
            reasons.append(
                f"{unfamiliar} name(s) not found in the paper's content, "
                f"with middling relevance ({relevance:.2f})"
            )
        return GuardResult(
            relevance=None if relevance is None else round(relevance, 4),
            contradicting=contradicting,
            unfamiliar=unfamiliar,
            off_target=bool(reasons),
            reasons=tuple(reasons),
        )

    def _relevance(self, vectors: Sequence[Vector], key_texts: Sequence[str]) -> float | None:
        if self._embedder is None or not vectors:
            return None
        targets = [s for text in key_texts for s in split_sentences(text.splitlines())]
        if not targets:
            return None
        key = tuple(targets)
        if key not in self._targets:
            self._targets[key] = [tuple(v) for v in self._embedder.embed(targets)]
        target_vectors = self._targets[key]
        best = sorted((max(cosine(t, v) for t in target_vectors) for v in vectors), reverse=True)[
            : self._policy.relevance_top
        ]
        return sum(best) / len(best)

    def _unfamiliar(self, sentences: Sequence[str], vocabulary: frozenset[str]) -> int:
        names: set[str] = set()
        for sentence in sentences:
            for m in _CAPITALISED.finditer(sentence):
                if m.start() == 0 or sentence[: m.start()].rstrip().endswith((".", ":", ";")):
                    continue  # a sentence's first word is capitalised anyway
                word = tokens(m.group())[0]
                if word in vocabulary:
                    continue
                if self._words is not None and self._words.contains(word):
                    continue
                names.add(word)
        return len(names)
