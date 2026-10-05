"""Text tools for the scorers: sentences from OCR lines, tokens, OCR-tolerant word and phrase
matching, and number extraction. Standard library only.

OCR of handwriting misreads about a fifth of the characters (P11: CER 22.8 %), so word matching
allows edits scaled to the word's length, and phrases may miss one connecting word."""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "as",
        "at",
        "from",
        "that",
        "this",
        "it",
        "its",
        "his",
        "her",
        "their",
        "which",
        "who",
        "whom",
    }
)
_SENTENCE_END = re.compile(r"[.;?!]\s+")
_NO_BREAK_BEFORE = re.compile(
    r"(?:^|\s)\(?(?:[0-9]{1,3}|[ivx]{1,4}|[a-z]|no|art|arts|sec|fig|eg|e\.g|ie|i\.e|viz|etc|vs)$",
    re.IGNORECASE,
)
_ITEM_START = re.compile(r"^\s*(?:[-*•>]|\(?[0-9ivx]{1,4}[.)]|\(?[a-h][.)])\s+", re.IGNORECASE)
_LABEL = re.compile(
    r"^\s*(?:(?:q(?:uestion)?|ans(?:wer)?)\s*(?:no\.?)?\s*[.:-]?\s*)?"
    r"\(?\d{1,2}\s*(?:\(?[a-h]\)?)?\s*[.):-]\s*(?:ans(?:wer)?\s*[.:-]?\s*)?",
    re.IGNORECASE,
)
_ANS_ONLY = re.compile(r"^\s*ans(?:wer)?\s*[.:-]\s*", re.IGNORECASE)

MAX_SENTENCE_WORDS = 30
"""Handwritten answers often run on without full stops: longer runs are cut into pieces of this
many words so one sentence vector does not average a whole paragraph."""


def normalize(text: str) -> str:
    """Case-folded, accents and curly quotes flattened."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u2212", "-")
    return text.casefold()


def tokens(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def content_tokens(text: str) -> list[str]:
    return [t for t in tokens(text) if t not in _STOP]


def strip_label(line: str) -> str:
    """The first line of a segment without its answer label ("12 b)", "Q no. 3.", "Ans:")."""
    stripped = _LABEL.sub("", line, count=1)
    return _ANS_ONLY.sub("", stripped, count=1)


def split_sentences(lines: Sequence[str]) -> list[str]:
    """Sentences from OCR lines in reading order. A hyphen at a line end joins the word; a
    list item ("1.", "-", "(ii)") or a blank line starts a new sentence; long runs are cut."""
    blocks: list[str] = []
    current = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            if current:
                blocks.append(current)
            current = ""
            continue
        if current and _ITEM_START.match(line):
            blocks.append(current)
            current = ""
        if current.endswith("-") and line[:1].islower():
            current = current[:-1] + line
        else:
            current = f"{current} {line}" if current else line
    if current:
        blocks.append(current)

    sentences: list[str] = []
    for block in blocks:
        for piece in _split_block(block):
            words = piece.split()
            for k in range(0, len(words), MAX_SENTENCE_WORDS):
                chunk = " ".join(words[k : k + MAX_SENTENCE_WORDS])
                if _WORD.search(chunk.casefold()):
                    sentences.append(chunk)
    return sentences


def _split_block(block: str) -> list[str]:
    """Split after . ; ? ! unless the word before is a list number ("1."), a single letter or
    an abbreviation ("Art.", "no.", "e.g.")."""
    pieces: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(block):
        if m.group()[0] == "." and _NO_BREAK_BEFORE.search(block[start : m.start()]):
            continue
        pieces.append(block[start : m.start() + 1])
        start = m.end()
    pieces.append(block[start:])
    return [p.strip() for p in pieces if p.strip()]


def allowed_edits(word: str) -> int:
    """Edits tolerated when matching ``word``: none up to 3 letters, 1 up to 6, then 2. Digits
    must match exactly (a misread number is a different number)."""
    if any(c.isdigit() for c in word) or len(word) <= 3:
        return 0
    return 1 if len(word) <= 6 else 2


def edit_distance(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance (insert, delete, substitute, swap neighbours), or
    ``limit + 1`` as soon as it must exceed ``limit``."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2: list[int] = []
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def word_matches(key: str, written: str) -> bool:
    """Does the written token (as read by OCR) stand for the key word?"""
    if key == written:
        return True
    limit = allowed_edits(key)
    return limit > 0 and edit_distance(key, written, limit) <= limit


@dataclass(frozen=True, slots=True)
class Token:
    text: str
    sentence: int


def sentence_tokens(sentences: Sequence[str]) -> list[Token]:
    return [Token(t, i) for i, s in enumerate(sentences) for t in tokens(s)]


@dataclass(frozen=True, slots=True)
class PhraseHit:
    start: int
    end: int
    """Token positions covered, end exclusive."""
    sentence: int


def find_phrase(phrase: str, words: Sequence[Token]) -> list[PhraseHit]:
    """Places where the phrase is written: its content words in order, each matched with OCR
    tolerance, at most one other word between neighbours; a phrase of three or more content
    words may miss one of them. Stop-words are ignored ("appoints the governors" matches
    "appointing governors")."""
    key = content_tokens(phrase) or tokens(phrase)
    if not key:
        return []
    may_miss = 1 if len(key) >= 3 else 0
    hits: list[PhraseHit] = []
    position = 0
    while position < len(words):
        if not word_matches(key[0], words[position].text) and not (
            may_miss and len(key) > 1 and word_matches(key[1], words[position].text)
        ):
            position += 1
            continue
        hit = _match_from(key, words, position, may_miss)
        if hit is None:
            position += 1
            continue
        hits.append(hit)
        position = hit.end
    return hits


def _match_from(
    key: Sequence[str], words: Sequence[Token], start: int, may_miss: int
) -> PhraseHit | None:
    missed = 0
    k = 0
    if not word_matches(key[0], words[start].text):
        missed, k = 1, 1  # the first key word is the one missing
    position = start
    last = start
    gap_allowed = 1
    while k < len(key):
        found = None
        for p in range(position, min(len(words), position + gap_allowed + 1)):
            if word_matches(key[k], words[p].text):
                found = p
                break
        if found is None:
            missed += 1
            if missed > may_miss:
                return None
            k += 1
            continue
        last = found
        position = found + 1
        k += 1
    return PhraseHit(start=start, end=last + 1, sentence=words[start].sentence)


_FRACTIONS = {"½": "0.5", "¼": "0.25", "¾": "0.75", "⅓": "0.3333", "⅔": "0.6667"}
_NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>-)?"
    r"(?:(?P<grouped>\d{1,3}(?:,\d{3})+(?:\.\d+)?)"
    r"|(?P<num>\d+)\s*/\s*(?P<den>\d+)(?![\d.])"
    r"|(?P<plain>\d*\.\d+|\d+)(?P<frac>[½¼¾⅓⅔])?"
    r"|(?P<alone>[½¼¾⅓⅔]))"
    r"\s*(?P<unit>%|[a-zA-Z]{1,6}\b)?"
)


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: str
    sentence: int


def extract_numbers(sentences: Sequence[str]) -> list[Quantity]:
    """Numbers written in the answer with the unit word that follows them, if any: ``42``,
    ``-3``, ``0.75``, ``.5``, ``1,000``, ``3/4``, ``2½``, ``½``, ``12%``, ``5 cm``."""
    found: list[Quantity] = []
    for index, sentence in enumerate(sentences):
        text = unicodedata.normalize("NFC", sentence).replace("−", "-")
        for m in _NUMBER.finditer(text):
            value = _value(m)
            if value is None:
                continue
            if m.group("sign"):
                value = -value
            unit = (m.group("unit") or "").casefold()
            found.append(Quantity(value=value, unit=unit, sentence=index))
    return found


def _value(m: re.Match[str]) -> Decimal | None:
    try:
        if m.group("grouped"):
            return Decimal(m.group("grouped").replace(",", ""))
        if m.group("num"):
            den = Decimal(m.group("den"))
            return None if den == 0 else Decimal(m.group("num")) / den
        if m.group("plain"):
            value = Decimal(m.group("plain"))
            if m.group("frac"):
                value += Decimal(_FRACTIONS[m.group("frac")])
            return value
        return Decimal(_FRACTIONS[m.group("alone")])
    except InvalidOperation:  # pragma: no cover  (the pattern only passes digits)
        return None


def word_count(sentences: Iterable[str]) -> int:
    return sum(len(tokens(s)) for s in sentences)
