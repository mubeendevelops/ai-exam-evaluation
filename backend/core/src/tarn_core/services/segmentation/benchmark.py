"""Measuring a segmentation against a person's labelling (P12 report).

The truth of a booklet says, per page (upload order, from 1), which question leaves start on
it in written order and which leaf the top of the page continues (None: the page starts with a
new answer or holds no answer). A prediction is compared on:

- **written order**: the sequence of answers as written (unassigned = ``?``): exact or not,
  and the edit distance;
- **starts**: (page, leaf) pairs, precision and recall;
- **continuations**: pages whose top continues an answer, and whether the right one;
- **page leaves**: per page, the set of leaves present (continued or started), Jaccard.

Only labels and counts: no text."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from tarn_core.domain.booklet import SegmentFlag
from tarn_core.ids import PageId
from tarn_core.services.ocr.text import levenshtein
from tarn_core.services.segmentation.segmenter import SegmentationResult

UNASSIGNED = "?"


@dataclass(frozen=True, slots=True, kw_only=True)
class TruthPage:
    page: int
    continues: str | None
    starts: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class BookletTruth:
    booklet: str
    paper: str
    pages: tuple[TruthPage, ...]
    written_numbers: tuple[int | None, ...] | None = None
    """The page number written on each page (upload order), when the labeller noted them."""

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(label for p in self.pages for label in p.starts)


@dataclass(frozen=True, slots=True, kw_only=True)
class PredictedPage:
    page: int
    continues: str | None
    starts: tuple[str, ...]
    leaves: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class BookletScore:
    booklet: str
    truth_order: tuple[str, ...]
    predicted_order: tuple[str, ...]
    order_distance: int
    true_starts: int
    predicted_starts: int
    correct_starts: int
    continuation_pages: int
    correct_continuations: int
    page_jaccard: float
    unassigned: int
    duplicates: int
    by_similarity: int

    @property
    def order_exact(self) -> bool:
        return self.truth_order == self.predicted_order

    @property
    def precision(self) -> float:
        return self.correct_starts / self.predicted_starts if self.predicted_starts else 1.0

    @property
    def recall(self) -> float:
        return self.correct_starts / self.true_starts if self.true_starts else 1.0


@dataclass(frozen=True, slots=True, kw_only=True)
class BookletRun:
    """One booklet segmented in the benchmark."""

    booklet: str
    paper: str
    result: SegmentationResult
    page_numbers: Mapping[PageId, int]
    """Upload number (from 1) of each page."""
    seconds: float
    truth: BookletTruth | None

    def score(self) -> "BookletScore | None":
        return (
            None
            if self.truth is None
            else score_booklet(self.truth, self.result, self.page_numbers)
        )

    @property
    def written_found(self) -> dict[int, int]:
        """Upload number → the page number segmentation read on it."""
        return {self.page_numbers[pid]: n for pid, n in self.result.order.written.items()}


def predicted_pages(
    result: SegmentationResult, page_numbers: Mapping[PageId, int]
) -> list[PredictedPage]:
    """Per page (upload number): what the segmentation says starts there and continues there.
    The text before the first answer (flagged) is neither."""
    starts: dict[int, list[str]] = {n: [] for n in page_numbers.values()}
    leaves: dict[int, set[str]] = {n: set() for n in page_numbers.values()}
    first_owner: dict[int, tuple[int, str | None]] = {}  # page -> (segment position, leaf)
    reading = {pid: k for k, pid in enumerate(result.order.order)}
    for segment in result.segments:
        if SegmentFlag.BEFORE_FIRST_ANSWER in segment.flags:
            continue
        leaf = segment.slot_label or UNASSIGNED
        pages = sorted(
            {page_numbers[s.page_id] for s in segment.spans},
            key=lambda n: reading[_page_id(page_numbers, n)],
        )
        for n in pages:
            leaves[n].add(leaf)
        starts[pages[0]].append(leaf)
        for n in pages[1:]:
            if n not in first_owner:
                first_owner[n] = (segment.position, leaf)
    return [
        PredictedPage(
            page=n,
            continues=first_owner[n][1] if n in first_owner else None,
            starts=tuple(starts[n]),
            leaves=frozenset(leaves[n]),
        )
        for n in sorted(starts)
    ]


def _page_id(page_numbers: Mapping[PageId, int], number: int) -> PageId:
    for pid, n in page_numbers.items():
        if n == number:
            return pid
    raise KeyError(number)


def score_booklet(
    truth: BookletTruth, result: SegmentationResult, page_numbers: Mapping[PageId, int]
) -> BookletScore:
    predicted = {p.page: p for p in predicted_pages(result, page_numbers)}
    reading = {page_numbers[pid]: k for k, pid in enumerate(result.order.order)}
    predicted_order = tuple(
        leaf for p in sorted(predicted.values(), key=lambda p: reading[p.page]) for leaf in p.starts
    )
    true_pairs = {(p.page, s) for p in truth.pages for s in p.starts}
    predicted_pairs = {(p.page, s) for p in predicted.values() for s in p.starts}
    continuation_pages = [p for p in truth.pages if p.continues is not None]
    jaccards: list[float] = []
    for page in truth.pages:
        want = set(page.starts) | ({page.continues} if page.continues else set())
        got = set(predicted[page.page].leaves) if page.page in predicted else set()
        union = want | got
        jaccards.append(len(want & got) / len(union) if union else 1.0)
    segments = [s for s in result.segments if SegmentFlag.BEFORE_FIRST_ANSWER not in s.flags]
    return BookletScore(
        booklet=truth.booklet,
        truth_order=truth.order,
        predicted_order=predicted_order,
        order_distance=levenshtein(list(truth.order), list(predicted_order)),
        true_starts=len(true_pairs),
        predicted_starts=sum(len(p.starts) for p in predicted.values()),
        correct_starts=len(true_pairs & predicted_pairs),
        continuation_pages=len(continuation_pages),
        correct_continuations=sum(
            1
            for p in continuation_pages
            if p.page in predicted and predicted[p.page].continues == p.continues
        ),
        page_jaccard=sum(jaccards) / len(jaccards) if jaccards else 1.0,
        unassigned=sum(1 for s in segments if s.slot_label is None),
        duplicates=sum(1 for s in segments if SegmentFlag.DUPLICATE in s.flags),
        by_similarity=sum(1 for s in segments if s.source.value == "similarity"),
    )


def summarise(scores: Sequence[BookletScore]) -> dict[str, float]:
    starts_true = sum(s.true_starts for s in scores)
    starts_pred = sum(s.predicted_starts for s in scores)
    correct = sum(s.correct_starts for s in scores)
    cont = sum(s.continuation_pages for s in scores)
    return {
        "booklets": len(scores),
        "orders_exact": sum(s.order_exact for s in scores),
        "order_distance": sum(s.order_distance for s in scores),
        "start_precision": correct / starts_pred if starts_pred else 1.0,
        "start_recall": correct / starts_true if starts_true else 1.0,
        "continuations": sum(s.correct_continuations for s in scores) / cont if cont else 1.0,
        "page_jaccard": sum(s.page_jaccard for s in scores) / len(scores) if scores else 1.0,
    }


# --- the report --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbedderRuns:
    name: str
    """The embedder, as ``name version``."""
    runs: tuple[BookletRun, ...]


def _pct(value: float) -> str:
    return f"{value * 100:.0f} %"


def _order(labels: Sequence[str]) -> str:
    return ", ".join(labels) if labels else "none"


def _header(*columns: str) -> list[str]:
    """A Markdown table header; a column named with a leading ``>`` is right-aligned."""
    names = [c.lstrip(">") for c in columns]
    rule = ["---:" if c.startswith(">") else "---" for c in columns]
    return ["| " + " | ".join(names) + " |", "| " + " | ".join(rule) + " |"]


def render_report(
    *,
    date: str,
    compared: Sequence[EmbedderRuns],
    policy: Mapping[str, float],
    unlabelled: Sequence[str],
    notes: Sequence[str] = (),
) -> str:
    """Markdown report: question labels, page numbers and counts; never text."""
    out = [f"# Segmentation benchmark, {date}", ""]
    out += [
        "Generated by `tarn bench segment`. Each sample booklet's cached OCR is segmented "
        "against its seeded paper and compared with a labelling of where each answer starts "
        "(per page: the questions that start there, in written order, and the question the "
        "top of the page continues). The report holds question labels and counts only.",
        "",
        "Metrics: **order** = the answers in written order (`?` = unassigned), exact or not, "
        "and the edit distance to the truth; **starts** = (page, question) pairs, precision "
        "and recall; **continuations** = pages whose top continues an answer, given the "
        "right one; **page Jaccard** = per page, overlap of the questions present (started "
        "or continued), averaged.",
        "",
    ]
    if notes:
        out += ["## Notes", ""]
        out += [f"- {note}" for note in notes]
        out.append("")
    out += [
        "## Summary",
        "",
        *_header(
            "Embedder",
            ">Booklets",
            ">Orders exact",
            ">Order edits",
            ">Start precision",
            ">Start recall",
            ">Continuations",
            ">Page Jaccard",
        ),
    ]
    for item in compared:
        scores = [sc for r in item.runs if (sc := r.score()) is not None]
        sm = summarise(scores)
        out.append(
            f"| {item.name} | {int(sm['booklets'])} | {int(sm['orders_exact'])} | "
            f"{int(sm['order_distance'])} | {_pct(sm['start_precision'])} | "
            f"{_pct(sm['start_recall'])} | {_pct(sm['continuations'])} | "
            f"{sm['page_jaccard']:.2f} |"
        )
    out.append("")
    for item in compared:
        out += [f"## Per booklet: {item.name}", ""]
        out += _header(
            "Booklet",
            "Paper",
            "Truth order",
            "Segmented order",
            ">Edits",
            "Starts P / R",
            "Continuations",
            ">Page Jaccard",
            ">Unassigned",
            ">Duplicates",
            ">By similarity",
            ">Seconds",
        )
        for run in item.runs:
            sc = run.score()
            if sc is None:
                continue
            out.append(
                f"| {run.booklet} | {run.paper} | {_order(sc.truth_order)} | "
                f"{_order(sc.predicted_order)} | {sc.order_distance} | "
                f"{_pct(sc.precision)} / {_pct(sc.recall)} | "
                f"{sc.correct_continuations}/{sc.continuation_pages} | "
                f"{sc.page_jaccard:.2f} | {sc.unassigned} | {sc.duplicates} | "
                f"{sc.by_similarity} | {run.seconds:.1f} |"
            )
        out.append("")
    first = compared[0].runs if compared else ()
    numbered = [r for r in first if r.truth is not None and r.truth.written_numbers]
    if numbered:
        out += [
            "## Page order",
            "",
            "| Booklet | Pages with a written number | Read correctly | Read wrongly | Reordered |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for run in numbered:
            truth = run.truth
            if truth is None or truth.written_numbers is None:
                continue
            found = run.written_found
            have = [(k + 1, n) for k, n in enumerate(truth.written_numbers) if n is not None]
            right = sum(1 for page, n in have if found.get(page) == n)
            wrong = sum(1 for page, n in found.items() if n != dict(have).get(page))
            out.append(
                f"| {run.booklet} | {len(have)} | {right} | {wrong} | "
                f"{'yes' if run.result.order.reordered else 'no'} |"
            )
        out.append("")
    if unlabelled:
        out += ["## Not scored", ""]
        out += [f"- {line}" for line in unlabelled]
        out.append("")
    out += ["## Settings", "", *_header("Weight", ">Value")]
    out += [f"| `{k}` | {v} |" for k, v in policy.items()]
    out.append("")
    return "\n".join(out)
