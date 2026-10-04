"""Answer labels, section headings, page numbers and page geometry for segmentation (P12)."""

from uuid import UUID

import pytest

from tarn_core.domain.booklet import Region, RegionKind
from tarn_core.domain.common import Box
from tarn_core.ids import CollegeId, PageId, RegionId
from tarn_core.services.segmentation.labels import LabelKind, looks_like_mark, parse_label
from tarn_core.services.segmentation.lines import PageInput, page_lines
from tarn_core.services.segmentation.pages import page_order, written_number
from tarn_core.testing.segmentation import booklet_pages, sheet

COLLEGE = CollegeId(UUID(int=7))
PAGE = PageId(UUID(int=1))


@pytest.mark.parametrize(
    ("text", "number", "part", "strong"),
    [
        ("1. The first point", 1, None, False),
        ("1) text", 1, None, False),
        ("(1) text", 1, None, False),
        ("2 . with a space", 2, None, False),
        ("5- text", 5, None, False),
        ("10.", 10, None, False),
        ("l. misread one", 1, None, False),
        ("Q no. 3 Explain", 3, None, True),
        ("Q.no.3", 3, None, True),
        ("Qno 3", 3, None, True),
        ("Q3.", 3, None, True),
        ("Question 7", 7, None, True),
        ("Ans 4)", 4, None, True),
        ("Ans. 5", 5, None, True),
        ("No1: Example", 1, None, True),
        ("12 b Copyright", 12, "b", True),
        ("13(a) Patent", 13, "a", True),
        ("13a) x", 13, "a", True),
        ("13a, x", 13, "a", True),
        ("Qno. 12(b)", 12, "b", True),
        ("③ circled", 3, None, True),
    ],
)
def test_question_labels(text: str, number: int, part: str | None, strong: bool) -> None:
    label = parse_label(text)
    assert label is not None and label.kind is LabelKind.QUESTION
    assert (label.number, label.part, label.strong) == (number, part, strong)


@pytest.mark.parametrize(
    "text",
    [
        "a. man of words",
        "I am writing",
        "Part A of the constitution says",
        "Quite so",
        "The 3. thing",
        "2 marks are given",
        "50,000 firms",
        "Answer the following",
        "",
    ],
)
def test_not_labels(text: str) -> None:
    assert parse_label(text) is None


def test_answer_marks_and_parts() -> None:
    mark = parse_label("Ans:- The right")
    assert mark is not None and mark.kind is LabelKind.ANSWER and mark.rest == "The right"
    part = parse_label("(ii) second")
    assert part is not None and part.kind is LabelKind.PART and part.part == "ii"
    part = parse_label("b) part")
    assert part is not None and part.kind is LabelKind.PART and part.part == "b"
    roman = parse_label("(i) writ")
    assert roman is not None and roman.kind is LabelKind.PART  # not the number 1


@pytest.mark.parametrize(
    ("text", "section"),
    [
        ("Section B", "B"),
        ("PART - C", "C"),
        ("Sec-A", "A"),
        ("Sections - C.", "C"),
        ("Part II", "2"),
        ("bection-A", "A"),  # misread by the OCR
        ("Seetion-B", "B"),
        ("Seltion-C", "C"),
    ],
)
def test_section_headings(text: str, section: str) -> None:
    label = parse_label(text)
    assert label is not None and label.kind is LabelKind.SECTION and label.section == section


def test_misread_mark() -> None:
    assert looks_like_mark("a. The parliament")
    assert looks_like_mark("Ba, Trademarks")
    assert not looks_like_mark("The parliament consists")


def _page(regions: list[Region], width: int = 1400) -> PageInput:
    return PageInput(page_id=PAGE, index=0, width=width, height=2200, regions=tuple(regions))


def _region(n: int, kind: RegionKind, box: Box, text: str | None = None) -> Region:
    return Region(
        id=RegionId(UUID(int=100 + n)),
        college_id=COLLEGE,
        page_id=PAGE,
        kind=kind,
        box=box,
        teacher_text=text,
    )


def test_table_cells_and_page_sized_figures_are_lines() -> None:
    """Ruled pages come back as one big table or picture (O44): their lines stay lines."""
    regions = [
        _region(0, RegionKind.DIAGRAM, Box(x0=0, y0=0, x1=1400, y1=2200)),
        _region(1, RegionKind.LABEL, Box(x0=70, y0=150, x1=900, y1=200), "3. inside a picture"),
        _region(2, RegionKind.TEXT_LINE, Box(x0=260, y0=220, x1=1100, y1=270), "body"),
    ]
    laid = page_lines(_page(regions))
    assert [ln.in_figure for ln in laid.lines] == [False, False]
    small = [
        _region(0, RegionKind.DIAGRAM, Box(x0=200, y0=100, x1=900, y1=600)),
        _region(1, RegionKind.LABEL, Box(x0=300, y0=200, x1=400, y1=250), "1."),
    ]
    assert page_lines(_page(small)).lines[0].in_figure


def test_sliver_of_the_neighbouring_page_is_left_out() -> None:
    page = sheet(
        COLLEGE, 0, ["> 1. an answer starts here", "the body of the answer is long", "! 2."]
    )
    laid = page_lines(page)
    assert len(laid.slivers) == 1
    assert all(ln.text != "2." for ln in laid.lines)


def test_lines_in_rows_left_to_right() -> None:
    regions = [
        _region(0, RegionKind.TEXT_LINE, Box(x0=700, y0=150, x1=1300, y1=200), "right"),
        _region(1, RegionKind.TEXT_LINE, Box(x0=70, y0=155, x1=600, y1=205), "left"),
        _region(2, RegionKind.TEXT_LINE, Box(x0=70, y0=260, x1=600, y1=310), "next"),
    ]
    laid = page_lines(_page(regions))
    assert [ln.text for ln in laid.lines] == ["left", "right", "next"]
    assert [ln.first_in_row for ln in laid.lines] == [True, False, True]


def test_page_order_from_written_numbers() -> None:
    pages = booklet_pages(
        COLLEGE,
        [
            ["cover page of the booklet with a long line"],
            ["@2", "the second written page, a long body line"],
            ["@1", "the first written page, a long body line"],
            ["an unnumbered page after page one, long line"],
            ["@3", "the third written page, a long body line"],
        ],
    )
    laid = [page_lines(p) for p in pages]
    assert written_number(laid[1]) == 2
    order = page_order(laid)
    assert order.reordered
    assert [pages.index(next(p for p in pages if p.page_id == pid)) for pid in order.order] == [
        0,
        2,
        3,
        1,
        4,
    ]


def test_page_order_refused_when_numbers_repeat_or_are_rare() -> None:
    repeated = booklet_pages(
        COLLEGE,
        [
            ["@2", "a long enough body line here"],
            ["@2", "a long enough body line here"],
            ["@1", "a long enough body line here"],
        ],
    )
    order = page_order([page_lines(p) for p in repeated])
    assert not order.reordered and order.order == tuple(p.page_id for p in repeated)
    rare = booklet_pages(
        COLLEGE,
        [["@3", "a long enough body line here"]] + [["a long enough body line here"]] * 6,
    )
    assert not page_order([page_lines(p) for p in rare]).reordered


def test_question_number_at_the_margin_is_not_a_page_number() -> None:
    page = sheet(COLLEGE, 0, ["> 3.", "a long enough body line for the page"])
    assert written_number(page_lines(page)) is None
