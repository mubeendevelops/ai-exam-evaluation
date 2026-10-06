"""A booklet's segments and the teacher's corrections of them (design.md "Segmentation"):
merge, split, reassign to another question (or the unassigned tray) and move the boundary
between two neighbouring segments. Each edit needs the booklet's lock and its version; the
answers whose text changed are re-scored by the worker."""

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, status

from tarn_api.backends import unit_of_work
from tarn_api.routes.review import WRITE_ERRORS
from tarn_api.schemas import (
    ErrorOut,
    ExpectedVersionIn,
    MergeIn,
    MoveBoundaryIn,
    ReassignIn,
    ResegmentOut,
    SegmentOut,
    SegmentsOut,
    SplitIn,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.booklet import Segment
from tarn_core.domain.tenancy import Role
from tarn_core.ids import BookletId, RegionId, SegmentId
from tarn_core.services.workflow import SegmentEdits
from tarn_core.services.workflow.edits import SegmentEditResult

router = APIRouter(
    prefix="/api/v1",
    tags=["segments"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
)


def _segment_out(s: Segment) -> SegmentOut:
    return SegmentOut(
        id=s.id,
        slot_label=s.slot_label,
        proposed_label=s.proposed_label,
        position=s.position,
        source=s.source.value,
        flags=[f.value for f in s.flags],
        match_score=s.match_score,
        region_ids=list(s.region_ids),
        page_ids=list(s.page_ids),
    )


def _result(done: SegmentEditResult) -> SegmentsOut:
    return SegmentsOut(
        booklet_version=done.booklet.version,
        segments=[_segment_out(s) for s in done.segments],
        rescoring=list(done.rescoring),
        emptied=list(done.emptied),
    )


@router.get(
    "/booklets/{booklet_id}/segments",
    response_model=SegmentsOut,
    summary="A booklet's segments in written order (unassigned ones have no question)",
)
def list_segments(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> SegmentsOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        booklet = unit.scope.booklets.get(who.college_id, BookletId(booklet_id))
        segments = unit.scope.booklets.segments(who.college_id, booklet.id)
    return SegmentsOut(
        booklet_version=booklet.version, segments=[_segment_out(s) for s in segments]
    )


def _edit(
    booklet_id: UUID,
    who: PrincipalDep,
    backends: BackendsDep,
    run: Callable[[SegmentEdits, BookletId], SegmentEditResult],
) -> SegmentsOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        done = run(unit.segment_edits(BookletId(booklet_id)), BookletId(booklet_id))
    return _result(done)


@router.post(
    "/booklets/{booklet_id}/segments/merge",
    response_model=SegmentsOut,
    responses=WRITE_ERRORS,
    summary="Merge two segments (the second joins the first)",
)
def merge(booklet_id: UUID, body: MergeIn, who: PrincipalDep, backends: BackendsDep) -> SegmentsOut:
    return _edit(
        booklet_id,
        who,
        backends,
        lambda edits, b: edits.merge(
            who.college_id,
            who.user_id,
            b,
            expected_version=body.expected_version,
            first=SegmentId(body.first),
            second=SegmentId(body.second),
        ),
    )


@router.post(
    "/booklets/{booklet_id}/segments/split",
    response_model=SegmentsOut,
    responses=WRITE_ERRORS,
    summary="Split a segment: the regions from `at_region` on become a new segment",
)
def split(booklet_id: UUID, body: SplitIn, who: PrincipalDep, backends: BackendsDep) -> SegmentsOut:
    return _edit(
        booklet_id,
        who,
        backends,
        lambda edits, b: edits.split(
            who.college_id,
            who.user_id,
            b,
            expected_version=body.expected_version,
            segment_id=SegmentId(body.segment_id),
            at_region=RegionId(body.at_region),
            label=body.label,
        ),
    )


@router.post(
    "/booklets/{booklet_id}/segments/reassign",
    response_model=SegmentsOut,
    responses=WRITE_ERRORS,
    summary="Give a segment another question, or move it to the unassigned tray",
)
def reassign(
    booklet_id: UUID, body: ReassignIn, who: PrincipalDep, backends: BackendsDep
) -> SegmentsOut:
    return _edit(
        booklet_id,
        who,
        backends,
        lambda edits, b: edits.reassign(
            who.college_id,
            who.user_id,
            b,
            expected_version=body.expected_version,
            segment_id=SegmentId(body.segment_id),
            label=body.label,
        ),
    )


@router.post(
    "/booklets/{booklet_id}/segments/move-boundary",
    response_model=SegmentsOut,
    responses=WRITE_ERRORS,
    summary="Move the boundary between two neighbouring segments",
)
def move_boundary(
    booklet_id: UUID, body: MoveBoundaryIn, who: PrincipalDep, backends: BackendsDep
) -> SegmentsOut:
    return _edit(
        booklet_id,
        who,
        backends,
        lambda edits, b: edits.move_boundary(
            who.college_id,
            who.user_id,
            b,
            expected_version=body.expected_version,
            upper=SegmentId(body.upper),
            lower=SegmentId(body.lower),
            region=RegionId(body.region),
        ),
    )


@router.post(
    "/booklets/{booklet_id}/segments/resegment",
    response_model=ResegmentOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses=WRITE_ERRORS,
    summary="Segment the booklet again from its current text (queued)",
    description="The worker segments the booklet afresh, replacing every segment including the "
    "teacher's own edits; answers whose text changed are re-scored. Needs the booklet's lock "
    "and its version. Only for a scored booklet in review (not once approved), and refused "
    "(409) while any answer is approved. The booklet's version moves on when it is done.",
)
def resegment(
    booklet_id: UUID, body: ExpectedVersionIn, who: PrincipalDep, backends: BackendsDep
) -> ResegmentOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        booklet = unit.resegment().request(
            who.college_id,
            who.user_id,
            BookletId(booklet_id),
            expected_version=body.expected_version,
        )
    return ResegmentOut(booklet_version=booklet.version)
