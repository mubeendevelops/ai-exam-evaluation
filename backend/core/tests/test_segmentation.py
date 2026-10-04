"""Splitting a booklet into answers (design.md "Segmentation", P12) on synthetic pages laid out
like the samples: the written orders of B-CI2 and B-IPR1, unlabelled continuations, an
unlabelled block, a rewritten question used as a heading, lists inside answers, section rules,
misread labels, duplicates, diagrams, single-question papers. Text is made up; the papers and
their wording are the development seed's."""

from dataclasses import dataclass

import pytest

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import SegmentFlag, SegmentSource
from tarn_core.ids import CollegeId
from tarn_core.services.segmentation.segmenter import SegmentationResult, Segmenter
from tarn_core.services.segmentation.service import question_texts
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.testing import InMemory
from tarn_core.testing.seed_world import seed_in_memory
from tarn_core.testing.segmentation import booklet_pages

COLLEGE = CollegeId(__import__("uuid").UUID(int=3))


@dataclass(frozen=True)
class Paper:
    blueprint: ExamBlueprint
    texts: dict[str, str]


@pytest.fixture(scope="module")
def papers() -> dict[str, Paper]:
    mem = InMemory()
    seed_in_memory(mem)
    found = {}
    for blueprint in mem.content.latest(ExamBlueprint):
        key = (
            blueprint.title.split(" ", 1)[0]
            if blueprint.title.startswith("QP")
            else (" ".join(blueprint.title.split(" ")[:2]))
        )
        found[key] = Paper(blueprint, question_texts(mem.content, blueprint))
    return found


def run(paper: Paper, pages: list[list[str]], booklet: str = "b") -> SegmentationResult:
    segmenter = Segmenter(paper.blueprint, paper.texts, TrigramEmbedder())
    return segmenter.segment(booklet_pages(COLLEGE, pages, booklet))


def order(result: SegmentationResult) -> list[str | None]:
    """Written order of the answers (the text before the first answer left out)."""
    return [s.slot_label for s in result.segments if SegmentFlag.BEFORE_FIRST_ANSWER not in s.flags]


def pages_of(result: SegmentationResult, label: str) -> list[int]:
    index = {pid: k for k, pid in enumerate(result.order.order)}
    found: set[int] = set()
    for s in result.segments:
        if s.slot_label == label:
            found |= {index[span.page_id] + 1 for span in s.spans}
    return sorted(found)


FILL = "and the student goes on writing about it at some length"

# --- the sample patterns ---------------------------------------------------------------------

B_CI2 = [
    ["cover page admission ticket school of commerce", "USN and the name of the student here"],
    [
        "^ Section A",
        "> 1. there include the freedom of speech and expression",
        "freedom of assembly freedom to move and reside anywhere",
        "> 2. the constitution mentions three types of emergency",
        "national emergency, state emergency and financial emergency",
        "> 5. a member of parliament must be a citizen of india",
        "qualifications for parliament age thirty and twenty five",
    ],
    [
        "the member must hold the qualifications parliament sets",
        "> 6. constitutional provisions that make india a secular state",
        "freedom to practise any religion, no state religion",
        "> 7. fundamental duties",
        "  * abide by the constitution and respect the flag",
        "  * defend the country and render national service",
    ],
    [
        "^ Section B",
        "> 8. the indian constitution is a living document",
        "it is amended over time as the needs of people change",
        "> 9. parliamentary control instruments over the executive",
        "question hour, zero hour, adjournment and no confidence motions",
    ],
    [
        "control of the executive through debates and motions",
        "> 10. powers of the rajya sabha and the lok sabha",
        "the rajya sabha and lok sabha share legislative powers",
    ],
    [
        "the lok sabha has more powers than the rajya sabha",
        "> 12. executive powers of the president",
        "the president is head of state and appoints ministers",
    ],
    [  # a landscape strip: the executive powers go on
        "executive powers include the command of the armed forces",
        "the president of the executive appoints governors",
    ],
    [
        "^ Section C",
        "> 17. fundamental rights and directive principles",
        "fundamental rights are justiciable, directive principles are not",
    ],
    ["directive principles guide the state, rights limit it", FILL],
    [
        "> 16. types of writs issued by the supreme court",
        "  (i) habeas corpus",
        "  (ii) mandamus",
        "  (iii) certiorari",
    ],
    ["writs issued by the supreme court protect the rights", FILL],
    ["the writ of quo warranto asks by what authority", FILL],
]


def test_b_ci2_order(papers: dict[str, Paper]) -> None:
    result = run(papers["QP-CI"], B_CI2)
    assert order(result) == ["1", "2", "5", "6", "7", "8", "9", "10", "12", "17", "16"]
    assert pages_of(result, "5") == [2, 3]  # page 3 starts by continuing answer 5
    assert pages_of(result, "12") == [6, 7]  # the landscape strip continues 12
    assert pages_of(result, "16") == [10, 11, 12]  # "(i) (ii)" do not start answers
    assert result.segments[0].flags == (SegmentFlag.BEFORE_FIRST_ANSWER,)
    assert not any(SegmentFlag.DUPLICATE in s.flags for s in result.segments)


B_IPR1 = [
    [
        "admission ticket of the school and the semester",
        "the course code and the name of the course",
    ],
    [
        "name and usn of the student",
        "^ Sec-A",
        "> 2. unfair competition is using deceptive selling practices",
        "that harm consumers and other businesses in the market",
        "> 7. WIPO is the global forum for intellectual property",
        "the world intellectual property organization is a UN agency",
    ],
    ["> 3. brand names and trademarks registered in india", "registered trademarks such as logos"],
    ["> 1. step one filing the application for geographical indications", "  * registration"],
    ["> 4. literary works dramatic works musical works", "works eligible for copyright protection"],
    [
        "^ Sec-B",
        "> 10. these rights include the right of adaptation",
        "rights of the copyright owner",
    ],
    ["copyright owner rights of reproduction and communication", FILL],
    ["> 11. step one write down the invention for the patent", "patents registration process"],
    ["the patent registration process ends with the grant", FILL],
    ["> 8. section 38 of the act gives the performer rights", "performer's right under copyright"],
    ["performers include actors, singers and musicians", FILL],
    [
        "^ sec-c",
        "> 13a, signs and logos not allowed to be registered as trademarks",
        "  4. names which cannot be registered as trademarks",  # a bullet read as "4."
    ],
    ["  4. generic terms and government symbols are not registered", FILL],
    [
        "> 13b, the patent cooperation treaty",
        "the PCT lets an applicant file one patent application",
    ],
]


def test_b_ipr1_order(papers: dict[str, Paper]) -> None:
    result = run(papers["QP-IPR"], B_IPR1)
    assert order(result) == ["2", "7", "3", "1", "4", "10", "11", "8", "13.a", "13.b"]
    assert pages_of(result, "13.a") == [12, 13]  # the "4." bullets of section C stay in 13a
    assert pages_of(result, "4") == [5]


# --- the rules one by one --------------------------------------------------------------------


def test_unlabelled_continuation_joins_the_previous_answer(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            ["> 9. parliamentary control instruments over the executive", "question hour"],
            ["zero hour and adjournment motions control the executive", FILL],
            ["no confidence motions are the last instrument of control", FILL],
        ],
    )
    assert order(result) == ["9"]
    assert pages_of(result, "9") == [1, 2, 3]


def test_unlabelled_block_found_by_similarity(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            [
                "> 12. executive powers of the president",
                "the president appoints ministers and governors",
                "~",
                "pardon commutation remission respite and reprieve",
                "a pardon frees the convict, commutation lightens the sentence",
            ]
        ],
    )
    assert order(result) == ["12", "13"]
    unlabelled = result.segments[-1]
    assert unlabelled.source is SegmentSource.SIMILARITY and unlabelled.match_score is not None


def test_rewritten_question_used_as_a_heading(papers: dict[str, Paper]) -> None:
    result = run(
        papers["Assignment 1"],
        [
            ["^ Assignment 1", "Ethical dilemma: what an ethical dilemma is, with examples"],
            ["a dilemma between two values, the scenario of a donation", FILL],
            [
                "the ethical conflict is resolved by the teacher",
                "~",
                "Influence of cultural ethos on business in India, Korea and Japan",
                "cultural ethos shapes how business is done",
            ],
            ["korea and japan have a culture of hierarchy in business", FILL],
        ],
    )
    assert order(result) == ["1", "2"]
    assert pages_of(result, "2") == [3, 4]


def test_list_inside_an_answer_is_not_a_set_of_answers(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            [
                "> 9. parliamentary control instruments over the executive",
                "> 1. question hour",
                "> 2. zero hour",
                "> 3. adjournment motion",
                "> 4. no confidence motion",
            ]
        ],
    )
    assert order(result) == ["9"]


def test_label_outside_the_section_being_answered_is_refused(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            [
                "^ Section C",
                "> 16. types of writs issued by the supreme court",
                "  2. mandamus orders a public official to do the duty",
                "  5. quo warranto asks by what authority",
            ]
        ],
    )
    assert order(result) == ["16"]


def test_misread_number_with_a_prefix_goes_by_similarity(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            [
                "> 1. freedom of speech and expression and assembly",
                "> Q no. 19 the types of emergency: national, state, financial",
                "three types of emergency in the constitution",
            ]
        ],
    )
    assert order(result) == ["1", "2"]
    assert SegmentFlag.NUMBER_UNREAD in result.segments[-1].flags


def test_unmatched_answer_mark_goes_to_the_tray(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            [
                "> 8. the indian constitution is a living document",
                "it is amended over time as needs change",
                "~",
                "> Ans:- zzz qqq xxv wvu",
                "kkk jjj yyy",
            ]
        ],
    )
    tray = [s for s in result.segments if s.slot_label is None]
    assert tray and tray[-1].proposed_label is not None


def test_same_question_answered_twice_keeps_both_flagged(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            ["> 7. fundamental duties: abide by the constitution", "respect the flag"],
            ["> 8. the indian constitution is a living document", "amended over time"],
            ["> Q7. the fundamental duties again: defend the country", "render national service"],
        ],
    )
    assert order(result) == ["7", "8", "7"]
    sevens = [s for s in result.segments if s.slot_label == "7"]
    assert all(SegmentFlag.DUPLICATE in s.flags for s in sevens)


def test_sub_part_inside_a_question_with_parts(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-IPR"],
        [
            [
                "> 13. signs and logos not allowed to be registered as trademarks",
                "generic names cannot be registered",
                "> (b) the patent cooperation treaty",
                "one international patent application",
            ]
        ],
    )
    assert order(result) == ["13.a", "13.b"]


def test_diagram_joins_the_answer_it_sits_in(papers: dict[str, Paper]) -> None:
    pages = [
        [
            "> 9. parliamentary control instruments over the executive",
            "[diagram]",
            "question hour and zero hour",
            "> 10. powers of the rajya sabha and the lok sabha",
            "the lok sabha has more powers",
        ]
    ]
    result = run(papers["QP-CI"], pages)
    diagram = next(
        r.id for r in booklet_pages(COLLEGE, pages)[0].regions if r.kind.value == "diagram"
    )
    owner = next(s for s in result.segments if diagram in s.region_ids)
    assert owner.slot_label == "9"
    assert owner.region_ids.index(diagram) == 1  # reading order: after the label line


def test_single_question_paper_takes_everything(papers: dict[str, Paper]) -> None:
    result = run(
        papers["Assignment 2"],
        [["^ Assignment-01", "startup schemes in india"], ["more about the schemes", FILL]],
    )
    assert [s.slot_label for s in result.segments] == ["1"]
    assert len(result.segments[0].spans) == 2


def test_every_line_is_in_exactly_one_segment(papers: dict[str, Paper]) -> None:
    pages = booklet_pages(COLLEGE, B_CI2)
    result = Segmenter(papers["QP-CI"].blueprint, papers["QP-CI"].texts, TrigramEmbedder()).segment(
        pages
    )
    held = [rid for s in result.segments for rid in s.region_ids]
    assert len(held) == len(set(held))
    assert set(held) == {r.id for p in pages for r in p.regions}
    assert [s.position for s in result.segments] == list(range(len(result.segments)))


def test_pages_are_put_in_written_order(papers: dict[str, Paper]) -> None:
    result = run(
        papers["QP-CI"],
        [
            ["@2", "zero hour and adjournment motions control the executive", FILL],
            ["@1", "> 9. parliamentary control instruments over the executive", "question hour"],
            ["@3", "> 10. powers of the rajya sabha and the lok sabha", "money bills"],
        ],
    )
    assert result.order.reordered
    assert order(result) == ["9", "10"]
    assert pages_of(result, "9") == [1, 2]


def test_short_numbered_answers_are_answers_not_a_list(papers: dict[str, Paper]) -> None:
    """One-line answers numbered from the start (an objective section) stay answers."""
    result = run(
        papers["QP-CI"],
        [
            [
                "^ Section A",
                "> 1. freedom of speech and expression",
                "> 2. national state and financial emergency",
                "> 3. parliamentary system and the prime minister",
            ]
        ],
    )
    assert order(result) == ["1", "2", "3"]


def test_a_question_already_answered_is_not_reopened_by_a_stray_number(
    papers: dict[str, Paper],
) -> None:
    """A lone "1." inside answer 9 (a point, not a list) does not reopen answer 1."""
    result = run(
        papers["QP-CI"],
        [
            [
                "> 1. freedom of speech and expression and assembly",
                "freedom to move and reside anywhere in the country",
                "> 9. parliamentary control instruments over the executive",
                "the executive answers to parliament through its instruments",
                "> 1. question hour is the first instrument of control",
                "ministers answer questions about the executive",
                "adjournment and no confidence motions follow",
            ]
        ],
    )
    assert order(result) == ["1", "9"]
