"""The non-LLM criterion scorers (P13): list, numeric, semantic, and their text tools."""

from collections.abc import Sequence
from decimal import Decimal
from uuid import uuid4

import pytest

from tarn_core.domain.common import EngineRef
from tarn_core.domain.content import (
    ContentMeta,
    CriterionParams,
    CriterionType,
    ListItem,
    ListParams,
    NumericParams,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.domain.scoring import CHECK, answer_mark, round_to_step
from tarn_core.ids import CollegeId, CriterionId, QuestionId, UserId
from tarn_core.ports.engines import ScoringInput
from tarn_core.services.scoring import ListScorer, NumericScorer, ScoringPolicy, SemanticScorer
from tarn_core.services.scoring.text import (
    edit_distance,
    extract_numbers,
    find_phrase,
    sentence_tokens,
    split_sentences,
    strip_label,
    word_matches,
)
from tarn_core.services.segmentation.similarity import TrigramEmbedder

META = ContentMeta(owning_college_id=CollegeId(uuid4()), created_by=UserId(uuid4()))


def criterion(kind: CriterionType, params: CriterionParams, weight: str = "2") -> RubricCriterion:
    return RubricCriterion(
        id=CriterionId(uuid4()),
        meta=META,
        question_id=QuestionId(uuid4()),
        label="criterion",
        type=kind,
        weight=Decimal(weight),
        params=params,
    )


def item(c: RubricCriterion, text: str) -> ScoringInput:
    return ScoringInput(criterion=c, answer_text=text)


# --- text tools -------------------------------------------------------------------------------


def test_sentences_join_hyphenated_lines_and_split_list_items() -> None:
    lines = [
        "The President appoints the gover-",
        "nors of states. He also",
        "1. appoints judges",
        "",
    ]
    assert split_sentences(lines) == [
        "The President appoints the governors of states.",
        "He also",
        "1. appoints judges",
    ]


def test_long_runs_without_full_stops_are_cut() -> None:
    sentences = split_sentences([" ".join(["word"] * 70)])
    assert [len(s.split()) for s in sentences] == [30, 30, 10]


def test_answer_labels_are_stripped_from_the_first_line() -> None:
    assert strip_label("12 b) The President") == "The President"
    assert strip_label("Q no. 3. Fundamental duties") == "Fundamental duties"
    assert strip_label("Ans: Article 53") == "Article 53"
    assert strip_label("Article 53 says") == "Article 53 says"


@pytest.mark.parametrize(
    ("key", "written", "ok"),
    [
        ("governors", "govemors", True),  # rn read as m: two edits on a long word
        ("judges", "judgcs", True),
        ("judges", "jxdgcs", False),
        ("cag", "cog", False),  # short words must match exactly
        ("53", "58", False),  # digits never tolerate an edit
        ("emergency", "emregency", True),  # swapped neighbours are one edit
    ],
)
def test_word_matching_tolerates_ocr_errors_by_length(key: str, written: str, ok: bool) -> None:
    assert word_matches(key, written) is ok


def test_edit_distance_stops_early() -> None:
    assert edit_distance("kitten", "sitting", 3) == 3
    assert edit_distance("a", "abcdef", 2) == 3


def test_phrases_skip_stop_words_and_may_miss_one_content_word() -> None:
    words = sentence_tokens(["He appoints governors.", "Chief election commissioner too."])
    assert [h.sentence for h in find_phrase("appoints the governors", words)] == [0]
    assert [h.sentence for h in find_phrase("chief election commissioners", words)] == [1]
    assert find_phrase("supreme court judges", words) == []
    three = sentence_tokens(["the comptroller auditor general"])
    assert find_phrase("comptroller and auditor general", three)
    assert find_phrase("comptroller auditor general india", three)  # one of four missing


def test_numbers_are_extracted_with_units() -> None:
    found = extract_numbers(
        ["Total is 1,000 cm and -3 then 0.75, .5", "about 2½ or ¾ and 3/4 or 12%"]
    )
    assert [str(q.value) for q in found] == [
        "1000", "-3", "0.75", "0.5", "2.5", "0.75", "0.75", "12",
    ]  # fmt: skip
    assert found[0].unit == "cm" and found[-1].unit == "%"
    assert [q.sentence for q in found] == [0, 0, 0, 0, 1, 1, 1, 1]


def test_a_hyphen_between_numbers_is_not_a_minus() -> None:
    assert [q.value for q in extract_numbers(["pages 3-4"])] == [Decimal(3), Decimal(4)]


# --- rounding ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "step", "expected"),
    [
        ("2.25", "0.5", "2.5"),  # halves round up
        ("2.24", "0.5", "2.0"),
        ("2.75", "0.5", "3.0"),
        ("1.125", "0.25", "1.25"),
        ("1.1", "0.25", "1.0"),
        ("3.5", "1", "4"),
    ],
)
def test_marks_round_half_up_to_the_paper_step(value: str, step: str, expected: str) -> None:
    assert round_to_step(Decimal(value), Decimal(step)) == Decimal(expected)


def test_answer_mark_is_the_rounded_weighted_sum() -> None:
    a = ListScorer().score(item(_duties(), "Respect the flag. Protect environment"))
    b = NumericScorer().score(item(_numeric("3.5", "0.1"), "x = 3.5"))
    # 2 x 2/5 + 2 x 1 = 2.8 -> 3.0 on a half-mark step
    assert answer_mark((a, b), Decimal("0.5")) == Decimal("3.0")


# --- list -------------------------------------------------------------------------------------


def _duties(need: int = 5) -> RubricCriterion:
    return criterion(
        CriterionType.LIST,
        ListParams(
            items=(
                ListItem(term="respect the constitution", synonyms=("respect the flag",)),
                ListItem(term="protect the environment", synonyms=("natural environment",)),
                ListItem(term="scientific temper"),
                ListItem(term="safeguard public property"),
                ListItem(term="promote harmony", synonyms=("brotherhood",)),
                ListItem(term="defend the country"),
            ),
            required_count=need,
        ),
    )


def test_list_counts_synonyms_and_misspellings() -> None:
    result = ListScorer().score(
        item(
            _duties(),
            "Every citizen must respect the flag.\nProtect the natual enviroment.\n"
            "Develop scientfic temper. Promote brotherhood.",
        )
    )
    assert result.credit == Decimal("0.8")  # 4 of 5
    assert result.reason is not None
    assert result.reason.matched == (
        "respect the constitution",
        "protect the environment",
        "scientific temper",
        "promote harmony",
    )
    assert "safeguard public property" in result.reason.missing
    assert result.reason.sentences == (0, 1, 2, 3)
    assert result.scorer == EngineRef(name="list", version="1")


def test_list_caps_credit_at_the_required_count() -> None:
    text = (
        "Respect the constitution. Protect the environment. Scientific temper. "
        "Safeguard public property. Promote harmony. Defend the country."
    )
    result = ListScorer().score(item(_duties(need=3), text))
    assert result.credit == Decimal(1)
    assert result.evidence == "6 of 3 required items written (3 counted)"


def test_an_item_written_twice_counts_once() -> None:
    text = "Scientific temper. Scientific temper again. Scientific temper!"
    result = ListScorer().score(item(_duties(need=2), text))
    assert result.credit == Decimal("0.5")


def test_one_stretch_of_text_counts_for_one_item_only() -> None:
    c = criterion(
        CriterionType.LIST,
        ListParams(
            items=(
                ListItem(term="appoints the prime minister", synonyms=("prime minister",)),
                ListItem(term="prime minister", synonyms=("council of ministers",)),
            ),
            required_count=2,
        ),
    )
    assert ListScorer().score(item(c, "He appoints the prime minister.")).credit == Decimal("0.5")


def test_list_credit_of_thirds_is_kept_to_four_places() -> None:
    result = ListScorer().score(item(_duties(need=3), "Scientific temper."))
    assert result.credit == Decimal("0.3333")


# --- numeric ----------------------------------------------------------------------------------


def _numeric(expected: str, tolerance: str = "0", unit: str = "") -> RubricCriterion:
    return criterion(
        CriterionType.NUMERIC,
        NumericParams(expected=Decimal(expected), tolerance=Decimal(tolerance), unit=unit),
    )


def test_numeric_step_within_tolerance() -> None:
    c = _numeric("2.5", "0.05")
    assert NumericScorer().score(item(c, "centroid x = 2.52")).credit == Decimal(1)
    miss = NumericScorer().score(item(c, "centroid x = 2.6"))
    assert miss.credit == Decimal(0)
    assert miss.reason is not None and miss.reason.found == "2.6"
    assert miss.reason.expected == "2.5 ± 0.05"


def test_numeric_reads_fractions() -> None:
    assert NumericScorer().score(item(_numeric("0.75"), "p = 3/4")).credit == Decimal(1)
    assert NumericScorer().score(item(_numeric("2.5"), "about 2½")).credit == Decimal(1)


def test_numeric_with_no_number_scores_zero() -> None:
    result = NumericScorer().score(item(_numeric("4"), "the output is the maximum"))
    assert result.credit == Decimal(0)
    assert result.evidence == "expected value not found"


def test_numeric_missing_unit_keeps_the_step_but_asks_to_check() -> None:
    c = _numeric("12", unit="cm")
    with_unit = NumericScorer().score(item(c, "length 12 cm"))
    assert with_unit.credit == Decimal(1) and with_unit.flags == ()
    bare = NumericScorer().score(item(c, "length 12"))
    assert bare.credit == Decimal(1) and bare.flags == (CHECK,)


# --- semantic ---------------------------------------------------------------------------------


class TableEmbedder:
    """Fixed 2-d vectors per text: cosine to (1, 0) is chosen by the test."""

    ref = EngineRef(name="table", version="1")
    dimension = 2

    def __init__(self, cosines: dict[str, float]) -> None:
        self._cos = cosines
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> Sequence[tuple[float, ...]]:
        self.calls.append(list(texts))
        out: list[tuple[float, ...]] = []
        for t in texts:
            c = self._cos.get(t, 0.0)
            out.append((c, float((1 - c * c) ** 0.5)))
        return out


STATEMENT = "Performers have the right to stop recording of their live performance."


def _semantic() -> RubricCriterion:
    return criterion(CriterionType.SEMANTIC, SemanticParams(reference_statement=STATEMENT))


@pytest.mark.parametrize(
    ("cosine", "credit", "check"),
    [(0.9, "1.0", False), (0.66, "1.0", True), (0.55, "0.5", False), (0.47, "0.5", True),
     (0.2, "0.0", False)],
)  # fmt: skip
def test_semantic_bands_and_borderline(cosine: float, credit: str, check: bool) -> None:
    embedder = TableEmbedder({STATEMENT: 1.0, "Singers own their shows.": cosine})
    scorer = SemanticScorer(embedder, ScoringPolicy(half=0.45, full=0.65, margin=0.05))
    result = scorer.score(item(_semantic(), "Unrelated words here. Singers own their shows."))
    assert result.credit == Decimal(credit)
    assert (CHECK in result.flags) is check
    assert result.similarity == pytest.approx(cosine, abs=1e-3)
    assert result.reason is not None and result.reason.sentences == (1,)
    assert result.scorer.name == "semantic" and "table@1" in result.scorer.version


def test_semantic_uses_precomputed_sentence_vectors_of_the_same_embedder() -> None:
    embedder = TableEmbedder({STATEMENT: 1.0})
    scorer = SemanticScorer(embedder)
    given = ScoringInput(
        criterion=_semantic(),
        answer_text="a. b.",
        sentences=("a.", "b."),
        sentence_vectors=((0.0, 1.0), (1.0, 0.0)),
        embedder=embedder.ref,
    )
    assert scorer.score(given).credit == Decimal(1)
    assert embedder.calls == [[STATEMENT]]  # only the statement was embedded


def test_semantic_matches_two_neighbouring_sentences_together() -> None:
    """OCR splits one sentence in two; their pair can match where neither does alone."""
    embedder = TableEmbedder({STATEMENT: 1.0})
    scorer = SemanticScorer(embedder, ScoringPolicy(half=0.45, full=0.65))
    vectors = ((0.6, 0.8), (0.6, -0.8))  # each 0.6 alone; the sum points along (1, 0)
    given = ScoringInput(
        criterion=_semantic(),
        answer_text="x",
        sentences=("first half", "second half"),
        sentence_vectors=vectors,
        embedder=embedder.ref,
    )
    result = scorer.score(given)
    assert result.credit == Decimal(1)
    assert result.reason is not None and result.reason.sentences == (0, 1)


def test_semantic_on_nothing_written_is_zero_without_embedding() -> None:
    embedder = TableEmbedder({})
    result = SemanticScorer(embedder).score(item(_semantic(), ""))
    assert result.credit == Decimal(0)
    assert embedder.calls == []


def test_semantic_with_the_trigram_embedder_prefers_the_paraphrase() -> None:
    scorer = SemanticScorer(TrigramEmbedder(), ScoringPolicy(half=0.3, full=0.6))
    paraphrase = "Performers can stop the recording of a live performance."
    close = scorer.score(item(_semantic(), paraphrase))
    far = scorer.score(item(_semantic(), "Copyright lasts sixty years after death."))
    assert close.similarity is not None and far.similarity is not None
    assert close.similarity > far.similarity
    assert close.credit > far.credit


def test_scorers_refuse_criteria_of_another_type() -> None:
    assert ListScorer().supports(CriterionType.LIST)
    assert not ListScorer().supports(CriterionType.SEMANTIC)
    assert NumericScorer().supports(CriterionType.NUMERIC)
    assert SemanticScorer(TrigramEmbedder()).supports(CriterionType.SEMANTIC)
    assert not SemanticScorer(TrigramEmbedder()).supports(CriterionType.DIAGRAM)
