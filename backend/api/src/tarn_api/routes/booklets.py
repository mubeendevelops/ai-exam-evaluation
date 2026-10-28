"""Booklets: the page-cleaning and reading status the UI shows (admin and SMEs:
``view_progress``; the admin sees no marks, page images or text, D167), the cleaned and
original page images, what OCR read on a page, the admin's "use anyway" and retake (replace)
(``upload_booklets``, P26) and the SME's delete (``evaluate``). Booklets are uploaded into a
unit (``routes/units.py``). An SME sees the booklets of the units they own only (P26)."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from tarn_api.backends import Unit, unit_of_work
from tarn_api.schemas import (
    BookletBlueprintOut,
    BookletDetailOut,
    BookletOut,
    BookletPageOut,
    BookletResultOut,
    BookletStatusName,
    BookletStudentOut,
    BookletUnitOut,
    ErrorOut,
    PageOut,
    PageTextOut,
    ReadingOut,
    RegionOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, booklet_user, current_user
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, BookletStatus, Page, Region
from tarn_core.domain.common import BlobKey
from tarn_core.domain.personas import CAPABILITIES, Capability
from tarn_core.domain.tenancy import User
from tarn_core.errors import UploadTooLargeError
from tarn_core.ids import BookletId, CollegeId, UnitId

router = APIRouter(
    prefix="/api/v1",
    tags=["booklets"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

_MEDIA = {"jpg": "image/jpeg", "png": "image/png"}


def is_evaluator(user: User) -> bool:
    """Marks, page images and text are for the SME (D167)."""
    return Capability.EVALUATE in CAPABILITIES[user.role]


def _retake_image(booklet: Booklet, page: Page) -> bool:
    """The admin may see a page's cleaned image only while it holds a retake back (P26)."""
    return booklet.status is BookletStatus.NEEDS_RETAKE and page.needs_retake and page.cleaned


def _page_out(
    base: str, page: Page, evaluator: bool = True, booklet: Booklet | None = None
) -> PageOut:
    m = page.metrics
    number = page.index + 1
    shown = evaluator or (booklet is not None and _retake_image(booklet, page))
    return PageOut(
        number=number,
        cleaned=page.cleaned,
        width=page.width,
        height=page.height,
        retake_reasons=[r.value for r in page.retake_reasons],
        use_anyway=page.use_anyway,
        sharpness=None if m is None else m.sharpness,
        glare_share=None if m is None else m.glare_share,
        rotation_degrees=None if m is None else m.rotation_degrees,
        rotation_guessed=None if m is None else m.rotation_guessed,
        skew_degrees=None if m is None else m.skew_degrees,
        cropped=None if m is None else m.cropped,
        perspective_corrected=None if m is None else m.perspective_corrected,
        neighbour_removed=None if m is None else m.neighbour_removed,
        page_found=None if m is None else m.page_found,
        image_url=f"{base}/pages/{number}/image" if page.cleaned and shown else None,
        original_url=(
            f"{base}/pages/{number}/image?kind=original" if page.original and evaluator else None
        ),
        text_read=page.text_read,
        needs_text=page.needs_text,
        ocr_failures=list(page.ocr_failures),
        text_url=f"{base}/pages/{number}/text" if page.text_read and evaluator else None,
    )


def booklet_out(
    unit: Unit,
    college_id: CollegeId,
    booklet: Booklet,
    *,
    duplicates: tuple[UUID, ...] = (),
    evaluator: bool = True,
) -> tuple[BookletOut, list[Page]]:
    pages = list(unit.scope.booklets.pages(college_id, booklet.id))
    student = unit.scope.students.get(college_id, booklet.student_id)
    blueprint = unit.scope.content.get(
        college_id, ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
    )
    work = unit.scope.units.get(college_id, booklet.unit_id)
    out = BookletOut(
        id=booklet.id,
        status=booklet.status.value,
        student=BookletStudentOut(id=student.id, name=student.name, usn=student.usn),
        unit=BookletUnitOut(id=work.id, code=work.code),
        blueprint=BookletBlueprintOut(
            id=blueprint.id, version=blueprint.meta.version, title=blueprint.title
        ),
        uploaded_by=booklet.uploaded_by,
        uploaded_at=booklet.uploaded_at,
        version=booklet.version,
        page_count=len(pages),
        pages_cleaned=sum(1 for p in pages if p.cleaned),
        flagged_pages=[p.index + 1 for p in pages if p.needs_retake],
        pages_read=sum(1 for p in pages if p.text_read),
        needs_text_pages=[p.index + 1 for p in pages if p.needs_text],
        failure_reason=booklet.failure_reason,
        duplicate_of=list(duplicates),
    )
    if booklet.approved and evaluator:
        sheets = unit.scope.sheets.versions(college_id, booklet.id)
        if sheets:
            latest = sheets[-1]
            out.result = BookletResultOut(
                total=float(latest.total),
                max_marks=float(latest.max_marks),
                sheet_version=latest.version,
            )
    return out, pages


@router.get(
    "/booklets",
    response_model=BookletPageOut,
    summary="Your college's booklets with their processing status, newest first",
)
def list_booklets(
    who: PrincipalDep,
    backends: BackendsDep,
    mine: Annotated[bool, Query(description="Only booklets I uploaded")] = False,
    unit_id: Annotated[UUID | None, Query(description="Only the booklets of this unit")] = None,
    booklet_status: Annotated[
        BookletStatusName | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookletPageOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.VIEW_PROGRESS)
        visible = unit.access.visible(
            who.college_id, unit.reach(user), unit.scope.booklets.list(who.college_id)
        )
        every = [
            b
            for b in visible
            if (not mine or b.uploaded_by == who.user_id)
            and (unit_id is None or b.unit_id == UnitId(unit_id))
            and (booklet_status is None or b.status.value == booklet_status)
        ]
        every.sort(key=lambda b: (b.uploaded_at, str(b.id)), reverse=True)
        evaluator = is_evaluator(user)
        items = [
            booklet_out(unit, who.college_id, b, evaluator=evaluator)[0]
            for b in every[offset : offset + limit]
        ]
        waiting = unit.scope.booklets.count_waiting(who.college_id, who.user_id)
    return BookletPageOut(
        items=items,
        total=len(every),
        limit=limit,
        offset=offset,
        waiting=waiting,
        max_waiting=backends.upload_limits.max_waiting,
    )


@router.get(
    "/booklets/{booklet_id}",
    response_model=BookletDetailOut,
    responses={404: {"model": ErrorOut}},
    summary="One booklet with its pages, their measurements and retake reasons",
)
def get_booklet(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> BookletDetailOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = booklet_user(unit, who, Capability.VIEW_PROGRESS, booklet_id)
        booklet = unit.scope.booklets.get(who.college_id, BookletId(booklet_id))
        evaluator = is_evaluator(user)
        out, pages = booklet_out(unit, who.college_id, booklet, evaluator=evaluator)
    base = f"/api/v1/booklets/{booklet_id}"
    return BookletDetailOut(
        **out.model_dump(), pages=[_page_out(base, p, evaluator, booklet) for p in pages]
    )


@router.get(
    "/booklets/{booklet_id}/pages/{number}/image",
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}, 404: {"model": ErrorOut}},
    summary="The cleaned page image (or the original with `kind=original`)",
    description="For the SME. The admin may fetch the cleaned image of a page that holds a "
    "retake back, while the booklet waits for the retake (P26); anything else is 404.",
)
def page_image(
    booklet_id: UUID,
    number: int,
    who: PrincipalDep,
    backends: BackendsDep,
    kind: Annotated[Literal["cleaned", "original"], Query()] = "cleaned",
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.EVALUATE, Capability.UPLOAD_BOOKLETS)
        booklet = unit.access.booklet(who.college_id, unit.reach(user), BookletId(booklet_id))
        pages = unit.scope.booklets.pages(who.college_id, BookletId(booklet_id))
        page = next((p for p in pages if p.index + 1 == number), None)
        key: BlobKey | None = None
        if page is not None and not is_evaluator(user):
            if kind == "cleaned" and _retake_image(booklet, page):
                key = page.image
        elif page is not None:
            key = page.original if kind == "original" else (page.image if page.cleaned else None)
        if key is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found.")
        data = unit.scope.blobs.get(key)
    extension = key.value.rsplit(".", 1)[-1].lower()
    return Response(
        content=data,
        media_type=_MEDIA.get(extension, "application/octet-stream"),
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


def region_out(region: Region) -> RegionOut:
    scores = region.scores or (None,) * len(region.readings)
    return RegionOut(
        id=region.id,
        kind=region.kind.value,
        box=[region.box.x0, region.box.y0, region.box.x1, region.box.y1],
        text=region.text,
        chosen=region.chosen,
        content_class=None if region.content_class is None else region.content_class.value,
        line_score=region.line_score,
        flagged=region.flagged,
        struck_out=region.struck_out,
        read_by=list(region.read_by),
        parent_id=region.parent_id,
        row=region.row,
        col=region.col,
        readings=[
            ReadingOut(
                engine=r.engine.name,
                engine_version=r.engine.version,
                text=r.text,
                confidence=r.confidence,
                box=[r.box.x0, r.box.y0, r.box.x1, r.box.y1],
                calibrated=None if s is None else s.calibrated,
                agreement=None if s is None else s.agreement,
                lexicon=None if s is None else s.lexicon,
                weight=None if s is None else s.weight,
                score=None if s is None else s.score,
                competing=None if s is None else s.competing,
            )
            for r, s in zip(region.readings, scores, strict=True)
        ],
    )


@router.get(
    "/booklets/{booklet_id}/pages/{number}/text",
    response_model=PageTextOut,
    responses={404: {"model": ErrorOut}},
    summary="What OCR read on one page: every line with every engine's reading and score",
    description="Lines keep every engine's reading, the selector's terms for each, the chosen "
    "one and whether the line is flagged for the teacher. Tables come as a `table` region "
    "followed by its cells (`parent_id`, `row`, `col`). Empty until the page has been read.",
)
def page_text(
    booklet_id: UUID, number: int, who: PrincipalDep, backends: BackendsDep
) -> PageTextOut:
    with unit_of_work(backends, who.college_id) as unit:
        booklet_user(unit, who, Capability.EVALUATE, booklet_id)
        pages = unit.scope.booklets.pages(who.college_id, BookletId(booklet_id))
        page = next((p for p in pages if p.index + 1 == number), None)
        if page is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found.")
        regions = unit.scope.booklets.regions(who.college_id, page.id)
        return PageTextOut(
            number=number,
            text_read=page.text_read,
            needs_text=page.needs_text,
            ocr_failures=list(page.ocr_failures),
            regions=[region_out(r) for r in regions],
        )


@router.post(
    "/booklets/{booklet_id}/pages/{number}/use-anyway",
    response_model=BookletOut,
    responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Go on with a page the quality gate flagged",
    description="Overrides the retake request for that page. When no flagged page is left the "
    "booklet moves to `pages_ready`. Recorded in the audit log.",
)
def use_page_anyway(
    booklet_id: UUID, number: int, who: PrincipalDep, backends: BackendsDep
) -> BookletOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = booklet_user(unit, who, Capability.UPLOAD_BOOKLETS, booklet_id)
        booklet = unit.page_decisions.use_anyway(
            who.college_id, who.user_id, BookletId(booklet_id), number - 1
        )
        out, _ = booklet_out(unit, who.college_id, booklet, evaluator=is_evaluator(user))
    return out


@router.post(
    "/booklets/{booklet_id}/replace",
    response_model=BookletOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut, "description": "The booklet is not waiting for a retake"},
        413: {"model": ErrorOut},
        422: {"model": ErrorOut},
    },
    summary="Retake: replace a booklet that needs a retake, or failed, with a new PDF",
    description="The old booklet is deleted (a content-free deletion record stays) and the new "
    "PDF is queued for the same student in the same unit, in one transaction. Everything is "
    "checked before anything is deleted.",
)
async def replace_booklet(
    booklet_id: UUID,
    request: Request,
    who: PrincipalDep,
    backends: BackendsDep,
    file: Annotated[UploadFile, File(description="The new PDF")],
) -> BookletOut:
    data = await read_capped(request, [file], backends.upload_limits.max_total_bytes)
    with unit_of_work(backends, who.college_id) as unit:
        user = booklet_user(unit, who, Capability.UPLOAD_BOOKLETS, booklet_id)
        new_id = unit.unit_uploads.replace(
            who.college_id, who.user_id, BookletId(booklet_id), data[0]
        )
        booklet = unit.scope.booklets.get(who.college_id, new_id)
        out, _ = booklet_out(unit, who.college_id, booklet, evaluator=is_evaluator(user))
    return out


async def read_capped(request: Request, files: list[UploadFile], max_bytes: int) -> list[bytes]:
    """The files' bytes, refusing a request larger than ``max_bytes`` (plus room for the form)
    before reading, and again while reading."""
    too_large = f"The upload is larger than {max_bytes // (1024 * 1024)} MB."
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes + 1024 * 1024:
        raise UploadTooLargeError(too_large)
    data: list[bytes] = []
    total = 0
    for upload in files:
        content = await upload.read(max_bytes + 1)
        total += len(content)
        if total > max_bytes:
            raise UploadTooLargeError(too_large)
        data.append(content)
    return data


@router.delete(
    "/booklets/{booklet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorOut}},
    summary="Delete a booklet: images, text and marks go; a content-free record stays",
)
def delete_booklet(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        booklet_user(unit, who, Capability.EVALUATE, booklet_id)
        unit.booklet_service.delete(who.college_id, who.user_id, BookletId(booklet_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
