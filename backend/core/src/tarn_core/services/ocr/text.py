"""Text measures for the selector and calibration: normalisation, edit distance, character
error rate and words."""

import re
import unicodedata

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")


def normalise(text: str) -> str:
    """Compare-form of a reading: Unicode NFC, case folded, runs of white space as one space."""
    return _SPACE.sub(" ", unicodedata.normalize("NFC", text)).strip().casefold()


def levenshtein(a: str, b: str) -> int:
    """Edit distance (insertions, deletions, substitutions of one character each)."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def normalised_distance(a: str, b: str) -> float:
    """Edit distance of the normalised texts over the longer one's length: 0 equal, 1 nothing
    in common. Two empty texts are equal."""
    na, nb = normalise(a), normalise(b)
    longest = max(len(na), len(nb))
    return 0.0 if longest == 0 else levenshtein(na, nb) / longest


def cer(reading: str, truth: str) -> float:
    """Character error rate of ``reading`` against ``truth`` (normalised), clipped to [0, 1].
    An empty truth: 0 for an empty reading, 1 otherwise."""
    nr, nt = normalise(reading), normalise(truth)
    if not nt:
        return 0.0 if not nr else 1.0
    return min(1.0, levenshtein(nr, nt) / len(nt))


def words(text: str) -> list[str]:
    """Words of at least two letters (digits, signs and single letters left out), case folded."""
    return [w.casefold() for w in _WORD.findall(text) if len(w) >= 2]
