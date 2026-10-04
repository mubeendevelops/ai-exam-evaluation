"""The best-of-N selector with synthetic readings: every term of S worked out by hand, ties,
lone readings, the flag, the content class, and the text measures behind them."""

import math

import pytest

from tarn_core.domain.booklet import LineReading
from tarn_core.domain.common import Box, EngineRef
from tarn_core.domain.ocr import ContentClass, EngineCalibration, ReadingScore, SelectorSettings
from tarn_core.errors import InvariantError
from tarn_core.services.ocr.selector import Lexicon, classify_line, select
from tarn_core.services.ocr.text import cer, levenshtein, normalise, normalised_distance, words
from tarn_core.testing import SetWordList

BOX = Box(x0=0, y0=0, x1=100, y1=20)
ALL = ("a", "b", "c")
SETTINGS = SelectorSettings(
    alpha=0.5,
    beta=0.25,
    flag_threshold=0.6,
    engine_sets=dict.fromkeys(ContentClass, ALL),
    print_engines=("a", "b"),
)


def reading(engine: str, text: str, confidence: float) -> LineReading:
    return LineReading(
        engine=EngineRef(name=engine, version="1"), text=text, box=BOX, confidence=confidence
    )


def no_words() -> Lexicon:
    return Lexicon()


# --- text measures ----------------------------------------------------------------------------


def test_levenshtein_and_normalised_distance() -> None:
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("same", "same") == 0
    assert normalised_distance("Hello  World", "hello world") == 0.0  # case and spaces
    assert normalised_distance("abcd", "abce") == pytest.approx(0.25)
    assert normalised_distance("", "") == 0.0
    assert normalised_distance("", "xyz") == 1.0


def test_cer_is_clipped_and_handles_empty_truth() -> None:
    assert cer("helo", "hello") == pytest.approx(0.2)
    assert cer("completely different text", "abc") == 1.0
    assert cer("", "") == 0.0
    assert cer("x", "") == 1.0


def test_words_keep_letters_of_two_or_more() -> None:
    assert words("The CPU's 2 cores, a X-ray and e-mail 42") == [
        "the",
        "cpu's",
        "cores",
        "x-ray",
        "and",
        "e-mail",
    ]
    assert normalise("  Two\tspaces \n") == "two spaces"


def test_lexicon_uses_terms_then_the_word_list() -> None:
    lexicon = Lexicon(frozenset({"Backpropagation algorithm"}), SetWordList(["the", "is"]))
    assert lexicon.fit("The backpropagation algorithm is gradiant") == pytest.approx(4 / 5)
    assert lexicon.fit("12 + 7") is None  # no words at all
    assert Lexicon().fit("anything here") == 0.0


# --- selector maths ---------------------------------------------------------------------------


def test_each_term_of_s_by_hand() -> None:
    # a: "cat sat" 0.9, b: "cat sat" 0.6, c: "cot sit" 0.8; lexicon knows "cat", "sat".
    readings = [
        reading("a", "cat sat", 0.9),
        reading("b", "cat sat", 0.6),
        reading("c", "cot sit", 0.8),
    ]
    lexicon = Lexicon(frozenset({"cat", "sat"}))
    choice = select(
        readings, ContentClass.CURSIVE, settings=SETTINGS, calibrations={}, lexicon=lexicon
    )
    d_ac = 2 / 7  # "cat sat" vs "cot sit": two substitutions over seven characters
    agree_a = 1 - (0 + d_ac) / 2
    agree_c = 1 - (d_ac + d_ac) / 2
    expected = [
        1.0 * 0.9 + 0.5 * agree_a + 0.25 * 1.0,
        1.0 * 0.6 + 0.5 * agree_a + 0.25 * 1.0,
        1.0 * 0.8 + 0.5 * agree_c + 0.25 * 0.0,
    ]
    assert [s.score for s in choice.scores] == pytest.approx(expected)
    assert [s.agreement for s in choice.scores] == pytest.approx([agree_a, agree_a, agree_c])
    assert [s.lexicon for s in choice.scores] == [1.0, 1.0, 0.0]
    assert choice.chosen == 0
    # Normalised line score: best S over w + α + β (three readings, words present).
    assert choice.line_score == pytest.approx(expected[0] / (1 + 0.5 + 0.25))
    assert choice.flagged is False


def test_agreement_can_outvote_a_confident_outlier() -> None:
    readings = [
        reading("a", "gradient descent", 0.75),
        reading("b", "gradual descend", 0.7),
        reading("c", "gradual descend", 0.7),
    ]
    choice = select(
        readings, ContentClass.CURSIVE, settings=SETTINGS, calibrations={}, lexicon=no_words()
    )
    assert readings[choice.chosen or 0].text == "gradual descend"


def test_lexicon_breaks_a_near_tie() -> None:
    readings = [reading("a", "neural netwerk", 0.8), reading("b", "neural network", 0.8)]
    lexicon = Lexicon(frozenset({"neural", "network"}))
    choice = select(
        readings, ContentClass.CURSIVE, settings=SETTINGS, calibrations={}, lexicon=lexicon
    )
    assert choice.chosen == 1
    assert choice.scores[1].score - choice.scores[0].score == pytest.approx(0.25 * 0.5)


def test_ties_go_to_higher_calibrated_confidence_then_engine_order() -> None:
    settings = SelectorSettings(
        alpha=0.0, beta=0.0, engine_sets=dict.fromkeys(ContentClass, ("b", "a"))
    )
    same = [reading("a", "x", 0.7), reading("b", "y", 0.7)]
    choice = select(
        same, ContentClass.PRINT, settings=settings, calibrations={}, lexicon=no_words()
    )
    assert choice.chosen == 1  # equal S and p̂: "b" comes first in the class's set
    # Equal S but different p̂ (a weight evens them out): the higher p̂ wins.
    cal = {
        ("a", ContentClass.PRINT): EngineCalibration(
            engine="a", content_class=ContentClass.PRINT, weight=0.5
        )
    }
    uneven = [reading("a", "x", 0.8), reading("b", "y", 0.4)]
    choice = select(
        uneven, ContentClass.PRINT, settings=settings, calibrations=cal, lexicon=no_words()
    )
    assert choice.scores[0].score == pytest.approx(choice.scores[1].score)
    assert choice.chosen == 0


def test_weight_and_calibration_change_the_winner_and_are_recorded() -> None:
    readings = [reading("a", "alpha", 0.9), reading("b", "alpine", 0.6)]
    plain = select(
        readings, ContentClass.CURSIVE, settings=SETTINGS, calibrations={}, lexicon=no_words()
    )
    assert plain.chosen == 0
    calibrations = {
        ("a", ContentClass.CURSIVE): EngineCalibration(
            engine="a",
            content_class=ContentClass.CURSIVE,
            version=3,
            xs=(0.0, 1.0),
            ys=(0.0, 0.5),
            weight=0.4,
        ),
        ("b", ContentClass.CURSIVE): EngineCalibration(
            engine="b", content_class=ContentClass.CURSIVE, version=1, xs=(0.5, 0.7), ys=(0.8, 0.9)
        ),
    }
    tuned = select(
        readings,
        ContentClass.CURSIVE,
        settings=SETTINGS,
        calibrations=calibrations,
        lexicon=no_words(),
    )
    assert tuned.scores[0].calibrated == pytest.approx(0.45)
    assert tuned.scores[0].weight == 0.4
    assert tuned.scores[1].calibrated == pytest.approx(0.85)
    assert tuned.chosen == 1
    assert [(r.id, r.version) for r in tuned.calibrations] == [
        (calibrations[("a", ContentClass.CURSIVE)].id, 3),
        (calibrations[("b", ContentClass.CURSIVE)].id, 1),
    ]


def test_a_lone_reading_is_judged_on_what_it_can_score() -> None:
    lexicon = Lexicon(frozenset({"heap", "sort"}))
    only = select(
        [reading("a", "heap sort", 0.8)],
        ContentClass.CURSIVE,
        settings=SETTINGS,
        calibrations={},
        lexicon=lexicon,
    )
    assert only.scores[0].agreement == 0.0
    assert only.scores[0].score == pytest.approx(0.8 + 0.25)
    assert only.line_score == pytest.approx(1.05 / 1.25)  # no α in the ceiling
    assert only.flagged is False
    weak = select(
        [reading("a", "hcap s0rt", 0.3)],
        ContentClass.CURSIVE,
        settings=SETTINGS,
        calibrations={},
        lexicon=lexicon,
    )
    assert weak.flagged is True


def test_a_line_without_words_drops_beta_from_the_ceiling() -> None:
    readings = [reading("a", "42.5", 0.9), reading("b", "42.5", 0.9)]
    choice = select(
        readings, ContentClass.NUMERIC, settings=SETTINGS, calibrations={}, lexicon=no_words()
    )
    assert choice.line_score == pytest.approx((0.9 + 0.5) / 1.5)


def test_disagreement_and_low_confidence_flag_the_line() -> None:
    readings = [reading("a", "qwzx", 0.3), reading("b", "mnop", 0.35)]
    choice = select(
        readings, ContentClass.CURSIVE, settings=SETTINGS, calibrations={}, lexicon=no_words()
    )
    assert choice.flagged is True
    assert choice.line_score is not None and choice.line_score < 0.6


def test_an_empty_winner_is_always_flagged() -> None:
    choice = select(
        [reading("a", "  ", 1.0), reading("b", "", 1.0)],
        ContentClass.CURSIVE,
        settings=SETTINGS,
        calibrations={},
        lexicon=no_words(),
    )
    assert choice.flagged is True


def test_only_the_class_engines_compete_but_every_reading_is_kept() -> None:
    settings = SelectorSettings(
        engine_sets={
            ContentClass.PRINT: ("a",),
            ContentClass.CURSIVE: ("b",),
            ContentClass.NUMERIC: ("a", "b"),
        }
    )
    readings = [reading("a", "print", 0.99), reading("b", "hand", 0.2)]
    choice = select(
        readings, ContentClass.CURSIVE, settings=settings, calibrations={}, lexicon=no_words()
    )
    assert choice.chosen == 1
    assert [s.competing for s in choice.scores] == [False, True]
    assert choice.scores[1].agreement == 0.0  # the outsider does not count as agreement
    # No reading from the class's engines: all of them compete rather than none.
    only_a = select(
        readings[:1], ContentClass.CURSIVE, settings=settings, calibrations={}, lexicon=no_words()
    )
    assert only_a.chosen == 0


def test_no_readings_no_choice() -> None:
    choice = select(
        [], ContentClass.CURSIVE, settings=SETTINGS, calibrations={}, lexicon=no_words()
    )
    assert choice.chosen is None and choice.flagged


def test_scores_are_well_formed() -> None:
    with pytest.raises(InvariantError):
        ReadingScore(calibrated=1.2, agreement=0, lexicon=0, weight=1, score=1)
    with pytest.raises(InvariantError):
        ReadingScore(calibrated=0.5, agreement=0, lexicon=0, weight=1, score=math.nan)
    with pytest.raises(InvariantError):
        SelectorSettings(alpha=-1)
    with pytest.raises(InvariantError):
        SelectorSettings(engine_sets={ContentClass.PRINT: ("a",)})


# --- content class ----------------------------------------------------------------------------


def test_numeric_lines() -> None:
    assert (
        classify_line([reading("a", "x = 12.5 × 4", 0.5), reading("c", "12.5x4", 0.4)], SETTINGS)
        is ContentClass.NUMERIC
    )
    assert classify_line([reading("a", "(+-)", 0.9)], SETTINGS) is ContentClass.CURSIVE  # no digit


def test_print_needs_confident_agreement_of_the_print_engines() -> None:
    agree = [
        reading("a", "Answer all questions", 0.95),
        reading("b", "Answer all questions", 0.9),
        reading("c", "Answer al questons", 0.5),
    ]
    assert classify_line(agree, SETTINGS) is ContentClass.PRINT
    unsure = [reading("a", "Answer all questions", 0.5), reading("b", "Answer all questions", 0.5)]
    assert classify_line(unsure, SETTINGS) is ContentClass.CURSIVE
    differ = [reading("a", "the cat sat", 0.95), reading("b", "tlie cut snt", 0.95)]
    assert classify_line(differ, SETTINGS) is ContentClass.CURSIVE
    assert classify_line([reading("a", "", 0.9)], SETTINGS) is ContentClass.CURSIVE
