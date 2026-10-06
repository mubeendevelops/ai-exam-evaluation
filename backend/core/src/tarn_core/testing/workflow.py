"""Scored booklets and the review services wired to the in-memory adapters (P15 tests). The
re-scorer is the scorer itself, so new suggestions arrive at once. All text is synthetic."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, BookletStatus
from tarn_core.services.diagrams.service import Rescorer
from tarn_core.services.scoring import BookletScorer, ScoringPolicy, ScoringService
from tarn_core.services.segmentation.similarity import TrigramEmbedder
from tarn_core.services.workflow import (
    BookletGuard,
    LockPolicy,
    RescoreRequests,
    ReviewService,
    SegmentEdits,
    StudentGraphEdits,
    TextEditor,
)
from tarn_core.testing.builders import Backend, CollegeFixture, make_services
from tarn_core.testing.scoring import Line, written_answer

POLICY = ScoringPolicy(half=0.25, full=0.45, relevance_min=0.0, relevance_soft=0.0)
GOOD = ["alpha and beta.", "The idea follows from alpha and beta."]
"""Full marks on ``add_rubric``."""
HALF = ["alpha only."]


def booklet_scorer(mem: Backend) -> BookletScorer:
    return BookletScorer(
        booklets=mem.booklets,
        scoring=ScoringService.standard(
            booklets=mem.booklets,
            scores=mem.scores,
            content=mem.content,
            runtime=mem.runtime,
            embedder=TrigramEmbedder(),
            policy=POLICY,
        ),
        runtime=mem.runtime,
    )


@dataclass(frozen=True, slots=True)
class Workflow:
    guard: BookletGuard
    rescore: RescoreRequests
    review: ReviewService
    text: TextEditor
    segments: SegmentEdits
    graphs: StudentGraphEdits


def workflow(
    mem: Backend, *, policy: LockPolicy | None = None, rescorer: Rescorer | None = None
) -> Workflow:
    rt = mem.runtime
    guard = BookletGuard(booklets=mem.booklets, users=mem.users, runtime=rt, policy=policy)
    rescore = RescoreRequests(
        booklets=mem.booklets, runtime=rt, rescorer=rescorer or booklet_scorer(mem)
    )
    return Workflow(
        guard=guard,
        rescore=rescore,
        review=ReviewService(
            booklets=mem.booklets,
            scores=mem.scores,
            content=mem.content,
            sheets=mem.sheets,
            users=mem.users,
            runtime=rt,
            guard=guard,
            rescore=rescore,
        ),
        text=TextEditor(booklets=mem.booklets, runtime=rt, guard=guard, rescore=rescore),
        segments=SegmentEdits(
            booklets=mem.booklets,
            scores=mem.scores,
            content=mem.content,
            runtime=rt,
            guard=guard,
            rescore=rescore,
        ),
        graphs=StudentGraphEdits(booklets=mem.booklets, runtime=rt, guard=guard, rescore=rescore),
    )


def scored_booklet(
    mem: Backend,
    college: CollegeFixture,
    blueprint: ExamBlueprint,
    answers: Mapping[str, Sequence[str | Line]],
) -> Booklet:
    """A booklet of the college's first student with these answers, scored (``scored``)."""
    booklet = (
        make_services(mem)
        .booklets.register(
            college.id,
            college.teacher.id,
            student_id=college.students[0].id,
            blueprint_id=blueprint.id,
            file_sha256=f"{mem.ids.new().int:064x}"[-64:],
        )
        .booklet
    )
    # The machine stages are other tests' business: jump to segmented.
    segmented = replace(booklet, status=BookletStatus.SEGMENTED, version=booklet.version + 1)
    mem.booklets.save(college.id, segmented)
    for label, lines in answers.items():
        written_answer(mem, segmented, label, lines)
    booklet_scorer(mem).step(college.id, booklet.id)
    return mem.booklets.get(college.id, booklet.id)
