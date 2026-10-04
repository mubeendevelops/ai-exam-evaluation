"""Booklets: upload (one PDF, or a set of images), the page-cleaning and reading status the UI
shows, the cleaned and original page images, what OCR read on a page, and the teacher's "use
anyway" and delete."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    File,
    Form,
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
    BookletStudentOut,
    ErrorOut,
    PageOut,
    PageTextOut,
    ReadingOut,
    RegionOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet, Page, Region
from tarn_core.domain.common import BlobKey
from tarn_core.domain.tenancy import Role
from tarn_core.errors import UploadTooLargeError
from tarn_core.ids import BlueprintId, BookletId, CollegeId, StudentId

router = APIRouter(
    prefix="/api/v1",
    tags=["booklets"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

_MEDIA = {"jpg": "image/jpeg", "png": "image/png"}


def _page_out(base: str, page: Page) -> PageOut:
    m = page.metrics
    number = page.index + 1
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
        image_url=f"{base}/pages/{number}/image" if page.cleaned else None,
        original_url=f"{base}/pages/{number}/image?kind=original" if page.original else None,
        text_read=page.text_read,
        needs_text=page.needs_text,
        ocr_failures=list(page.ocr_failures),
        text_url=f"{base}/pages/{number}/text" if page.text_read else None,
    )


def _booklet_out(
    unit: Unit,
    college_id: CollegeId,
    booklet: Booklet,
    *,
    duplicates: tuple[UUID, ...] = (),
) -> tuple[BookletOut, list[Page]]:
    pages = list(unit.scope.booklets.pages(college_id, booklet.id))
    student = unit.scope.students.get(college_id, booklet.student_id)
    blueprint = unit.scope.content.get(
        ExamBlueprint, booklet.blueprint.id, booklet.blueprint.version
    )
    out = BookletOut(
        id=booklet.id,
        status=booklet.status.value,
        student=BookletStudentOut(id=student.id, name=student.name, usn=student.usn),
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
    return out, pages


@router.post(
    "/booklets",
    response_model=BookletOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": "The same file was uploaded before (`duplicate_of` lists the booklets)"
        },
        413: {"model": ErrorOut},
        415: {"model": ErrorOut},
        429: {"model": ErrorOut, "description": "You already have the maximum waiting"},
    },
    summary="Upload a booklet: one PDF, or the page images in order",
    description="`files` is one PDF, or JPEG/PNG images in page order (the order sent is the "
    "page order). The type is judged from the file's content. The booklet is queued for page "
    "cleaning (status `uploaded`). The same file hash within the college is refused with 409 "
    "unless `allow_duplicate` is true; a teacher may have a limited number of booklets "
    "waiting (429 beyond it).",
)
async def upload_booklet(
    request: Request,
    who: PrincipalDep,
    backends: BackendsDep,
    student_id: Annotated[UUID, Form()],
    blueprint_id: Annotated[UUID, Form()],
    files: Annotated[list[UploadFile], File(description="One PDF, or images in page order")],
    allow_duplicate: Annotated[bool, Form()] = False,
) -> BookletOut:
    limits = backends.upload_limits
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limits.max_total_bytes + 1024 * 1024:
        raise UploadTooLargeError(
            f"The upload is larger than {limits.max_total_bytes // (1024 * 1024)} MB."
        )
    data: list[bytes] = []
    total = 0
    for upload in files:
        content = await upload.read(limits.max_total_bytes + 1)
        total += len(content)
        if total > limits.max_total_bytes:
            raise UploadTooLargeError(
                f"The upload is larger than {limits.max_total_bytes // (1024 * 1024)} MB."
            )
        data.append(content)
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        registration = unit.uploads.upload(
            who.college_id,
            who.user_id,
            student_id=StudentId(student_id),
            blueprint_id=BlueprintId(blueprint_id),
            files=data,
            allow_duplicate=allow_duplicate,
        )
        out, _ = _booklet_out(
            unit, who.college_id, registration.booklet, duplicates=tuple(registration.duplicates)
        )
    return out


@router.get(
    "/booklets",
    response_model=BookletPageOut,
    summary="Your college's booklets with their processing status, newest first",
)
def list_booklets(
    who: PrincipalDep,
    backends: BackendsDep,
    mine: Annotated[bool, Query(description="Only booklets I uploaded")] = False,
    booklet_status: Annotated[
        Literal["uploaded", "processing", "needs_retake", "pages_ready", "failed"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookletPageOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        every = [
            b
            for b in unit.scope.booklets.list(who.college_id)
            if (not mine or b.uploaded_by == who.user_id)
            and (booklet_status is None or b.status.value == booklet_status)
        ]
        every.sort(key=lambda b: (b.uploaded_at, str(b.id)), reverse=True)
        items = [_booklet_out(unit, who.college_id, b)[0] for b in every[offset : offset + limit]]
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
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        booklet = unit.scope.booklets.get(who.college_id, BookletId(booklet_id))
        out, pages = _booklet_out(unit, who.college_id, booklet)
    base = f"/api/v1/booklets/{booklet_id}"
    return BookletDetailOut(**out.model_dump(), pages=[_page_out(base, p) for p in pages])


@router.get(
    "/booklets/{booklet_id}/pages/{number}/image",
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}, 404: {"model": ErrorOut}},
    summary="The cleaned page image (or the original with `kind=original`)",
)
def page_image(
    booklet_id: UUID,
    number: int,
    who: PrincipalDep,
    backends: BackendsDep,
    kind: Annotated[Literal["cleaned", "original"], Query()] = "cleaned",
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        pages = unit.scope.booklets.pages(who.college_id, BookletId(booklet_id))
        page = next((p for p in pages if p.index + 1 == number), None)
        key: BlobKey | None = None
        if page is not None:
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


def _region_out(region: Region) -> RegionOut:
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
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
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
            regions=[_region_out(r) for r in regions],
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
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        booklet = unit.page_decisions.use_anyway(
            who.college_id, who.user_id, BookletId(booklet_id), number - 1
        )
        out, _ = _booklet_out(unit, who.college_id, booklet)
    return out


@router.delete(
    "/booklets/{booklet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorOut}},
    summary="Delete a booklet: images, text and marks go; a content-free record stays",
)
def delete_booklet(booklet_id: UUID, who: PrincipalDep, backends: BackendsDep) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.booklet_service.delete(who.college_id, who.user_id, BookletId(booklet_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
