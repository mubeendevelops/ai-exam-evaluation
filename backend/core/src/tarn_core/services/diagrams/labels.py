"""Label normalisation and glossary snapping (design.md "Diagram comparison", step 2).

A label is compared in its normal form: Unicode NFKC, case folded, trimmed, white space
collapsed, punctuation dropped except the symbols that carry meaning in a diagram
(``< > = + - * / ? ! % ^ ( )``), and spaces around those symbols removed, so ``n>0`` and
``N > 0 ?`` agree up to the question mark. Snapping replaces a label by the nearest glossary
term (the teacher's terms plus the reference diagram's labels, U6 Q21) when it is close
enough: a misread or misspelt label still counts as that term."""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

_KEEP = "<>=+-*/?!%^()"
_DROP = re.compile(rf"[^\w\s{re.escape(_KEEP)}]")
_SPACE = re.compile(r"\s+")
_AROUND = re.compile(rf"\s*([{re.escape(_KEEP)}])\s*")


class LabelMatch(StrEnum):
    EXACT = "exact"
    CLOSE = "close"
    NONE = "none"


def normalise_label(label: str) -> str:
    text = unicodedata.normalize("NFKC", label).casefold()
    text = text.replace("≥", ">=").replace("≤", "<=").replace("≠", "!=").replace("×", "*")
    text = _DROP.sub(" ", text)
    text = _AROUND.sub(r"\1", text)
    return _SPACE.sub(" ", text).strip()


def osa_distance(a: str, b: str) -> int:
    """Edits (insert, delete, substitute, swap two neighbours) from ``a`` to ``b``: a swap of
    two letters is one slip of the pen, not two."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def label_distance(a: str, b: str) -> float:
    """Edit distance (OSA) of two *normalised* labels over the longer one's length: 0 equal, 1
    nothing in common. Two empty labels are equal; one empty label is as far as can be."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    if not a or not b:
        return 1.0
    return min(1.0, osa_distance(a, b) / longest)


@dataclass(frozen=True, slots=True, kw_only=True)
class SnappedLabel:
    raw: str
    normal: str
    """The normal form, or the glossary term's normal form when snapped."""
    term: str | None
    """The glossary term matched (as the glossary writes it), or None."""
    match: LabelMatch


class Glossary:
    """Terms of one question, normalised once. ``close`` is the largest normalised distance
    that still snaps a label to a term."""

    def __init__(self, terms: Sequence[str], *, close: float) -> None:
        self._terms: list[tuple[str, str]] = []
        seen: set[str] = set()
        for term in terms:
            normal = normalise_label(term)
            if normal and normal not in seen:
                seen.add(normal)
                self._terms.append((normal, term.strip()))
        self._close = close

    @property
    def terms(self) -> tuple[str, ...]:
        return tuple(t for _, t in self._terms)

    def snap(self, label: str) -> SnappedLabel:
        normal = normalise_label(label)
        if not normal:
            return SnappedLabel(raw=label, normal="", term=None, match=LabelMatch.NONE)
        best: tuple[float, str, str] | None = None
        for term_normal, term in self._terms:
            d = label_distance(normal, term_normal)
            if best is None or d < best[0]:
                best = (d, term_normal, term)
        if best is not None and best[0] == 0:
            return SnappedLabel(raw=label, normal=best[1], term=best[2], match=LabelMatch.EXACT)
        if best is not None and best[0] <= self._close:
            return SnappedLabel(raw=label, normal=best[1], term=best[2], match=LabelMatch.CLOSE)
        return SnappedLabel(raw=label, normal=normal, term=None, match=LabelMatch.NONE)


def compare_labels(reference: str, student: SnappedLabel, *, close: float) -> LabelMatch:
    """How a student's (snapped) label validates against its matched reference label."""
    ref = normalise_label(reference)
    raw = normalise_label(student.raw)
    if raw == ref:
        return LabelMatch.EXACT
    if not ref or not raw:
        return LabelMatch.NONE
    if (
        student.normal == ref
        or min(label_distance(student.normal, ref), label_distance(raw, ref)) <= close
    ):
        return LabelMatch.CLOSE
    return LabelMatch.NONE


def label_similarity(reference: str, student: SnappedLabel) -> float:
    """1 − the distance between the reference label and the student's snapped (or raw) label,
    whichever is nearer."""
    ref = normalise_label(reference)
    raw = normalise_label(student.raw)
    return 1.0 - min(label_distance(ref, student.normal), label_distance(ref, raw))
