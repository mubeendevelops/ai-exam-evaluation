"""Answer labels at the start of a line: ``1.``, ``1)``, ``(1)``, ``Q no. 3``, ``Q3``,
``Ans:-``, ``Ans 4``, ``No1:``, ``12 b``, ``13(a)``, ``(ii)``, and section headings (``Section B``,
``Part - C``). The OCR misreads digits as letters (``l.``, ``I)``, ``O``) about as often as it
reads them right, so the number of a label is read tolerantly, and only at label position.

Parsing says what a line *could* be; whether it is accepted depends on the blueprint and on
where the line sits (``segmenter``)."""

import re
from dataclasses import dataclass
from enum import StrEnum

from tarn_core.services.ocr.text import levenshtein


class LabelKind(StrEnum):
    QUESTION = "question"
    """A question number, perhaps with a sub-part: ``3.``, ``Q3``, ``12 b``."""
    PART = "part"
    """A sub-part alone: ``(b)``, ``ii)``."""
    ANSWER = "answer"
    """An answer mark without a readable number: ``Ans:-``."""
    SECTION = "section"
    """A section heading: ``Section B``."""


@dataclass(frozen=True, slots=True)
class Label:
    kind: LabelKind
    number: int | None = None
    part: str | None = None
    """Lower case: ``a``, ``b``, ``ii``."""
    section: str | None = None
    """Upper case letter, or the 1-based position as a string for ``Part 2`` / ``Part II``."""
    strong: bool = False
    """Written as a label for sure (``Q``/``Ans`` prefix, a number with a sub-part, a circled
    number); a bare ``3.`` may also be a point of a list inside an answer."""
    rest: str = ""
    """The text after the label on the same line."""


_DIGIT_LIKE = str.maketrans({"l": "1", "I": "1", "|": "1", "!": "1", "O": "0", "o": "0"})
_CIRCLED = {chr(0x2460 + k): k + 1 for k in range(20)}  # ① .. ⑳
_LEAD = re.compile(r"^[\s\"'`*•·~_,]+")
_PARTS = r"(?:viii|vii|vi|iv|v|i{1,3}|ix|x|[a-h])"

_SECTION = re.compile(
    r"^(?:sections?|sec|parts?)\s*[-\u2013:.]?\s*(?P<s>[A-Ea-e]|I{1,3}|IV|[1-5])\b[\s.:\-\u2013)]*(?P<rest>.*)$",
    re.IGNORECASE,
)
# A heading the OCR misread ("bection-A", "Seetion-B"): one word close to "section", a dash or
# stop, the letter, nothing else.
_LOOSE_SECTION = re.compile(
    r"^(?P<w>[a-z]{3,9})\s*[-\u2013:.]+\s*(?P<s>[A-E]|I{1,3}|IV)[\s.:)\-\u2013]*$", re.IGNORECASE
)
_SECTION_WORDS = ("section", "sections", "sec", "part")
_STRONG_PREFIX = re.compile(
    r"^(?:(?P<ans>ans(?:wer)?s?)\b\.?\s*(?:to\s*)?(?:(?:q(?:uestion|ues|n|s)?\.?\s*)?(?:no|n0)?\.?)?"
    r"|(?P<q>q(?:uestion|ues|n(?![o0])|s)?)\s*\.?\s*(?:no|n0|num|number)?\s*\.?)"
    r"\s*[-\u2013:.#)]*\s*",
    re.IGNORECASE,
)
_NUMBER = r"(?P<n>[0-9lI|!oO]{1,2})"
_SUBPART = rf"(?:(?P<sep>[.\s]?)\(?(?P<p>{_PARTS})(?:[).:,\]]+|(?=\s|$)))"
_BARE = re.compile(
    rf"^[(\[]?{_NUMBER}(?:{_SUBPART}|(?P<term>\s?[).:\]\-\u2013]+|$))\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_AFTER_PREFIX = re.compile(
    rf"^[(\[]?{_NUMBER}(?:{_SUBPART}|[).:\]\-\u2013]*)(?=[\s\-\u2013:.)]|$)\s*[-\u2013:.)]*\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^no\.?\s?(?P<n>\d{1,2})\s*[:.)\-]+\s*(?P<rest>.*)$", re.IGNORECASE)
_PART_ALONE = re.compile(rf"^(?P<open>\()?(?P<p>{_PARTS})(?P<close>[).:\]]+)\s*(?P<rest>.*)$")


def _number(token: str) -> int | None:
    value = token.translate(_DIGIT_LIKE)
    if not value.isdigit():
        return None
    if not any(c.isdigit() for c in token) and len(token) > 1:
        return None  # "lo", "Il": too likely a word
    number = int(value)
    return number if number > 0 else None


def _part(match: re.Match[str]) -> str | None:
    part = match.group("p")
    return None if part is None else part.lower()


def parse_label(text: str) -> Label | None:
    """The label a line starts with, if it looks like one."""
    line = _LEAD.sub("", text).strip()
    if not line:
        return None
    if line[0] in _CIRCLED:
        circled = _CIRCLED[line[0]]
        return Label(LabelKind.QUESTION, number=circled, strong=True, rest=line[1:].strip())

    section = _SECTION.match(line)
    if section is not None and len(section.group("rest").split()) <= 3:
        raw = section.group("s").upper()
        roman = {"I": "1", "II": "2", "III": "3", "IV": "4"}
        return Label(LabelKind.SECTION, section=roman.get(raw, raw), rest=section.group("rest"))

    numbered = _NUMBERED.match(line)
    if numbered is not None:  # "No1:", "No. 2 -"
        return Label(
            LabelKind.QUESTION,
            number=int(numbered.group("n")),
            strong=True,
            rest=numbered.group("rest").strip(),
        )

    loose = _LOOSE_SECTION.match(line)
    if loose is not None:
        word = loose.group("w").lower()
        if any(levenshtein(word, w) <= max(1, len(w) // 3) for w in _SECTION_WORDS):
            raw = loose.group("s").upper()
            roman = {"I": "1", "II": "2", "III": "3", "IV": "4"}
            return Label(LabelKind.SECTION, section=roman.get(raw, raw))

    prefix = _STRONG_PREFIX.match(line)
    if prefix is not None and prefix.end() > 0 and (prefix.group("ans") or prefix.group("q")):
        tail = line[prefix.end() :]
        numbered = _AFTER_PREFIX.match(tail)
        if numbered is not None:
            number = _number(numbered.group("n"))
            if number is not None:
                return Label(
                    LabelKind.QUESTION,
                    number=number,
                    part=_part(numbered),
                    strong=True,
                    rest=numbered.group("rest").strip(),
                )
        ans = prefix.group("ans")
        punctuated = re.search(r"[.:\-\u2013#)]\s*$", line[: prefix.end()]) is not None
        if ans and (punctuated or not tail.strip() or ans.lower() == "ans"):
            # "Ans:-", "Ans the …", a lone "Answer"; not "Answer the following …"
            return Label(LabelKind.ANSWER, strong=True, rest=tail.strip())
        if ans:
            return None
        # "Q" followed by something that is not a number: a word such as "Quite"? Only a
        # prefix that ended in punctuation or "no" is a label without a number.
        if re.search(r"(?:no|n0|[.:\-\u2013#])\s*$", line[: prefix.end()], re.IGNORECASE):
            return Label(LabelKind.ANSWER, strong=True, rest=tail.strip())
        return None

    bare = _BARE.match(line)
    if bare is not None:
        number = _number(bare.group("n"))
        token = bare.group("n")
        part = _part(bare)
        if number is not None and (part is not None or bare.group("term") is not None):
            if not any(c.isdigit() for c in token) and bare.group("term") in (None, ""):
                return None  # a lone "I" or "l" is a word, not a number
            return Label(
                LabelKind.QUESTION,
                number=number,
                part=part,
                strong=part is not None and any(c.isdigit() for c in token),
                rest=bare.group("rest").strip(),
            )

    alone = _PART_ALONE.match(line)
    if alone is not None:
        part = alone.group("p")
        if part.isupper() and len(part) == 1:
            return None
        # "a." or "i." opening a sentence is too common: a lone part needs a bracket.
        if alone.group("open") is None and ")" not in alone.group("close"):
            return None
        return Label(LabelKind.PART, part=part.lower(), rest=alone.group("rest").strip())
    return None


_SHORT_MARK = re.compile(r"^[(\[]?\w{1,3}\s?[).:,\]]")


def looks_like_mark(text: str) -> bool:
    """A short token and a stop at the start of a line (``a.``, ``Ba,``, ``S.``): a label the
    OCR could not read, when the line also hangs left of the text column."""
    return _SHORT_MARK.match(_LEAD.sub("", text)) is not None
