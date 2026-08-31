"""
core/text_match.py — shared fuzzy string matching.

Factored out of core/diagram_evaluator.py::match_glossary so the same
OCR/typo-tolerant matching logic backs both diagram label matching and the
question-keyword / rubric coverage checks in core/plugins/text_extraction.py,
rather than the two reimplementing it independently.

Two small, composable pieces:
  - fuzzy_match(query, candidates)  — which of a list of candidate strings
    best matches `query`? (diagram_evaluator: which glossary term/alias does
    an OCR'd label match; text_extraction: which sliding text-window best
    matches a keyword/rubric phrase.)
  - phrase_in_text(phrase, text)    — is `phrase` present, allowing for
    fuzzy/OCR-style noise, somewhere inside a longer body of text? Built on
    top of fuzzy_match by turning `text` into overlapping word-windows and
    matching `phrase` against them.

Matching is plain string similarity (difflib), never embeddings — same scope
note as core/diagram_evaluator.py's module docstring: this exists to
tolerate spelling/OCR noise, not to find semantic equivalents.
"""
from __future__ import annotations

import difflib

#: difflib.SequenceMatcher ratio threshold for accepting a fuzzy match by
#: default. Callers with a different noise tolerance (e.g. diagram label
#: matching vs. free-text keyword coverage) may pass their own.
DEFAULT_FUZZY_THRESHOLD = 0.75

#: A ratio-based threshold unfairly penalizes short strings: a single
#: character OCR misread on a 3-letter word (e.g. "CPU" -> "CPV") already
#: drops the SequenceMatcher ratio to 0.667. Below this length, an absolute
#: edit-distance check is used instead, so short strings aren't penalized
#: just for being short.
SHORT_STRING_MAX_LEN = 6
SHORT_STRING_MAX_EDITS = 1


def edit_distance(a: str, b: str) -> int:
    """Plain Levenshtein distance, stdlib-only (only ever used on short
    strings, at most a handful of times per match)."""
    if a == b:
        return 0
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur_row = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cur_row[j] = min(
                cur_row[j - 1] + 1,
                prev_row[j] + 1,
                prev_row[j - 1] + (ca != cb),
            )
        prev_row = cur_row
    return prev_row[-1]


def fuzzy_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def fuzzy_match(query: str, candidates: list[str], *,
                 threshold: float = DEFAULT_FUZZY_THRESHOLD,
                 short_max_len: int = SHORT_STRING_MAX_LEN,
                 short_max_edits: int = SHORT_STRING_MAX_EDITS) -> dict | None:
    """Which candidate best matches `query`? Case/whitespace-insensitive.

    Short-circuits — ignoring the ratio threshold and any later candidates —
    on the FIRST candidate that is either an exact normalized match, or, for
    short strings, within `short_max_edits` edit distance (see
    SHORT_STRING_MAX_LEN). Otherwise scans every candidate and returns the
    single highest-ratio one, if it clears `threshold`.

    Returns {"index", "candidate", "ratio", "match_type": "exact"|"fuzzy"}
    or None.
    """
    query_norm = query.strip().lower()
    if not query_norm:
        return None

    best_idx = None
    best_ratio = 0.0
    for idx, candidate in enumerate(candidates):
        candidate_norm = candidate.strip().lower()
        if not candidate_norm:
            continue
        if candidate_norm == query_norm:
            return {"index": idx, "candidate": candidate, "ratio": 1.0, "match_type": "exact"}
        if (max(len(query_norm), len(candidate_norm)) <= short_max_len
                and edit_distance(query_norm, candidate_norm) <= short_max_edits):
            return {
                "index": idx, "candidate": candidate,
                "ratio": round(fuzzy_ratio(query_norm, candidate_norm), 4),
                "match_type": "fuzzy",
            }
        ratio = fuzzy_ratio(query_norm, candidate_norm)
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = idx

    if best_idx is not None and best_ratio >= threshold:
        return {
            "index": best_idx, "candidate": candidates[best_idx],
            "ratio": round(best_ratio, 4), "match_type": "fuzzy",
        }
    return None


#: Per-WORD fuzzy_match threshold used by phrase_in_text. Higher than
#: DEFAULT_FUZZY_THRESHOLD (0.75, tuned for OCR'd diagram labels) because
#: phrase_in_text matches real vocabulary words against each other, where a
#: 1-edit-distance neighbor can be a genuinely different word rather than
#: noise — e.g. SequenceMatcher("fifo", "lifo").ratio() == 0.75, which would
#: clear 0.75 but correctly fails to clear this higher bar.
WORD_MATCH_THRESHOLD = 0.8


def phrase_in_text(phrase: str, text: str, *,
                    coverage_threshold: float = 1.0,
                    word_threshold: float = WORD_MATCH_THRESHOLD,
                    short_max_len: int = SHORT_STRING_MAX_LEN,
                    short_max_edits: int = SHORT_STRING_MAX_EDITS) -> dict | None:
    """Is `phrase` present, allowing for fuzzy/OCR-style noise, somewhere
    inside `text`? Checks WORD BY WORD — what fraction of `phrase`'s words
    each fuzzy-match some word in `text` — rather than comparing the whole
    phrase against a text window as one string. That distinction matters:
    a whole-phrase difflib ratio over-weights words the two share ("order"
    in both "FIFO order" and "LIFO order") and can let it mask a missing
    distinctive word ("FIFO"), since the shared word alone drives the ratio
    well above typical thresholds. Checking each word independently doesn't
    have this failure mode. Word order/adjacency in `text` is NOT checked —
    this is a coverage heuristic (used for question-keyword and
    rubric-criterion coverage in core/plugins/text_extraction.py), not a
    phrase-boundary detector.

    `short_max_edits` defaults on (see fuzzy_match) but callers matching
    whole VOCABULARY WORDS rather than OCR'd glyphs should pass 0 to
    disable it: two short, correctly-spelled words one edit apart can be
    different words with opposite meanings (e.g. "FIFO"/"LIFO") — unlike a
    diagram label, where a short candidate one edit from the glossary term
    is far more likely an OCR misread of it than a coincidentally-similar
    different word.

    Returns {"coverage", "matched_words", "total_words", "match_type"}, or
    None if fewer than `coverage_threshold` of the phrase's words are found.
    match_type is "exact" only when every matched word was an exact hit.
    """
    phrase_words = phrase.strip().lower().split()
    if not phrase_words:
        return None
    text_words = text.split()
    if not text_words:
        return None

    matched = 0
    match_types = []
    for word in phrase_words:
        hit = fuzzy_match(word, text_words, threshold=word_threshold,
                           short_max_len=short_max_len, short_max_edits=short_max_edits)
        if hit:
            matched += 1
            match_types.append(hit["match_type"])

    coverage = matched / len(phrase_words)
    if coverage < coverage_threshold:
        return None
    return {
        "coverage": round(coverage, 4),
        "matched_words": matched,
        "total_words": len(phrase_words),
        "match_type": "exact" if match_types and all(t == "exact" for t in match_types) else "fuzzy",
    }
