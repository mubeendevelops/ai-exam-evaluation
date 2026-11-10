"""Subjects and exam blueprints, the college's own content (D165): ``/api/v1/subjects``,
``/api/v1/blueprints``. The admin keeps the subject list (D160); SMEs read and write the papers
of their own subjects (D178). Another college's subject or blueprint, and a paper of a subject
the SME does not have, is a 404. Since P33 a blueprint is a schema or a paper (D219): Manage
Schema lists schemas (``/api/v1/schemas``), clones blueprints and generates paper drafts from
schemas (D220). Deletion follows D202."""

import re
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Path, Query, Response, status

from tarn_api.backends import Unit, unit_of_work
from tarn_api.schemas import (
    BlueprintOut,
    BlueprintSummaryOut,
    CloneIn,
    DeletionCheckOut,
    ErrorOut,
    GeneratePaperIn,
    IssueOut,
    PaperCardOut,
    PaperIn,
    PaperListOut,
    SchemaCardOut,
    SchemaListOut,
    SectionSummaryOut,
    SubjectIn,
    SubjectOut,
    ValidationOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.blueprint import BlueprintKind, BlueprintSummary, ExamBlueprint
from tarn_core.domain.content import Subject
from tarn_core.domain.personas import Capability
from tarn_core.errors import NotFoundError
from tarn_core.ids import BlueprintId, CollegeId, SubjectId
from tarn_core.services.blueprint_document import DocumentReport, blueprint_to_document

router = APIRouter(
    prefix="/api/v1",
    tags=["blueprints"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)

Document = Annotated[
    dict[str, Any],
    Body(
        description="An exam blueprint in the public form: see docs/api/blueprint.schema.json "
        "(version 1.2; 1.1 and 1.0 are still read) and the examples next to it."
    ),
]
_INVALID: dict[int | str, dict[str, Any]] = {
    422: {
        "model": ErrorOut,
        "description": "The blueprint breaks a rule; every broken "
        "rule is listed in the message (use /blueprints/validate for a structured list).",
    }
}


def _subject_out(subject: Subject) -> SubjectOut:
    return SubjectOut(
        id=subject.id,
        code=subject.code,
        name=subject.name,
        version=subject.meta.version,
        retired=subject.retired,
    )


def _summary(b: ExamBlueprint, subjects: dict[UUID, Subject]) -> dict[str, Any]:
    subject = subjects.get(b.subject_id)
    return {
        "id": b.id,
        "version": b.meta.version,
        "title": b.title,
        "course_code": b.course_code,
        "exam_code": b.exam_code,
        "subject_id": b.subject_id,
        "subject_name": None if subject is None else subject.name,
        "total_marks": float(b.total_marks),
        "duration_minutes": b.duration_minutes,
        "section_count": len(b.sections),
        "question_count": sum(1 for _ in b.slots()),
        "unlinked_count": len(b.unlinked_leaves()),
        "kind": b.kind.value,
        "target_batch": b.target_batch,
        "weightage_note": b.weightage_note,
        "description": b.description,
        "descriptor": b.descriptor,
        "from_schema": b.from_schema,
        "updated_at": b.saved_at,
    }


def _card(b: BlueprintSummary, subjects: dict[UUID, Subject]) -> SchemaCardOut:
    subject = subjects.get(b.subject_id)
    return SchemaCardOut(
        id=b.id,
        version=b.version,
        title=b.title,
        course_code=b.course_code,
        subject_id=b.subject_id,
        subject_name=None if subject is None else subject.name,
        target_batch=b.target_batch,
        weightage_note=b.weightage_note,
        description=b.description,
        total_marks=float(b.total_marks),
        section_count=b.section_count,
        updated_at=b.saved_at,
        used_in=b.used_in,
    )


def _paper_card(
    b: BlueprintSummary, subjects: dict[UUID, Subject], in_use: frozenset[BlueprintId]
) -> PaperCardOut:
    subject = subjects.get(b.subject_id)
    return PaperCardOut(
        id=b.id,
        version=b.version,
        title=b.title,
        subject_id=b.subject_id,
        subject_name=None if subject is None else subject.name,
        descriptor=b.descriptor,
        description=b.description,
        exam_code=b.exam_code,
        course_code=b.course_code,
        duration_minutes=b.duration_minutes,
        total_marks=float(b.total_marks),
        section_count=b.section_count,
        created_at=b.created_at,
        updated_at=b.saved_at,
        in_use=b.id in in_use,
        from_schema=b.from_schema,
    )


def _subjects(unit: Unit, college_id: CollegeId) -> dict[UUID, Subject]:
    return {s.id: s for s in unit.subjects.all(college_id)}


def _full_out(b: ExamBlueprint, unit: Unit) -> BlueprintOut:
    subjects = _subjects(unit, b.meta.college_id)
    return BlueprintOut(
        **_summary(b, subjects),
        document=blueprint_to_document(b),
        structure_locked=unit.blueprints.structure_locked(b.meta.college_id, b),
    )


def _validation_out(report: DocumentReport) -> ValidationOut:
    return ValidationOut(
        valid=report.valid,
        issues=[IssueOut(path=i.path, message=i.message) for i in report.issues],
        warnings=[IssueOut(path=i.path, message=i.message) for i in report.warnings],
        sections=[
            SectionSummaryOut(
                label=s.label,
                method=s.method.value,
                items=s.items,
                counted=s.counted,
                max_marks=float(s.max_marks),
            )
            for s in report.sections
        ],
        computed_total=None if report.computed_total is None else float(report.computed_total),
        question_count=report.question_count,
        unlinked=list(report.unlinked),
    )


# --- subjects ---------------------------------------------------------------------------------


@router.get(
    "/subjects",
    response_model=list[SubjectOut],
    summary="The subjects I work with, by name",
    description="The admin gets the college's whole list, retired subjects included; an SME "
    "the live subjects granted to them (D160).",
)
def list_subjects(who: PrincipalDep, backends: BackendsDep) -> list[SubjectOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_SUBJECTS, Capability.AUTHOR_CONTENT)
        subjects = unit.subjects.visible_to(who.college_id, who.user_id)
    return [_subject_out(s) for s in subjects]


@router.post(
    "/subjects",
    status_code=status.HTTP_201_CREATED,
    response_model=SubjectOut,
    responses={409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Create a subject of my college (admin)",
)
def create_subject(body: SubjectIn, who: PrincipalDep, backends: BackendsDep) -> SubjectOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_SUBJECTS)
        subject = unit.subjects.create(who.college_id, who.user_id, code=body.code, name=body.name)
    return _subject_out(subject)


@router.put(
    "/subjects/{subject_id}",
    response_model=SubjectOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Rename a subject (admin)",
    description="Saves the next version; questions, papers and scores keep the subject.",
)
def rename_subject(
    subject_id: UUID, body: SubjectIn, who: PrincipalDep, backends: BackendsDep
) -> SubjectOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_SUBJECTS)
        subject = unit.subjects.rename(
            who.college_id, who.user_id, SubjectId(subject_id), code=body.code, name=body.name
        )
    return _subject_out(subject)


@router.post(
    "/subjects/{subject_id}/retire",
    response_model=SubjectOut,
    responses={404: {"model": ErrorOut}},
    summary="Retire a subject (admin)",
    description="A retired subject cannot be granted to an SME or used for a new paper "
    "(D181); its questions, papers, grants and scores stay.",
)
def retire_subject(subject_id: UUID, who: PrincipalDep, backends: BackendsDep) -> SubjectOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.MANAGE_SUBJECTS)
        subject = unit.subjects.retire(who.college_id, who.user_id, SubjectId(subject_id))
    return _subject_out(subject)


# --- blueprints -------------------------------------------------------------------------------


@router.post(
    "/blueprints/validate",
    response_model=ValidationOut,
    summary="Check a blueprint document without saving it",
    description="Reports every broken rule with its path and a plain sentence, and the totals "
    "with the choice rules applied (any N of M, OR pairs). Always 200: `valid` says the verdict.",
)
def validate_blueprint(
    document: Document, who: PrincipalDep, backends: BackendsDep
) -> ValidationOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        report = unit.blueprints.check(document)
    return _validation_out(report)


@router.get(
    "/blueprints",
    response_model=list[BlueprintSummaryOut],
    summary="The newest version of every blueprint of my subjects, by title",
)
def list_blueprints(
    who: PrincipalDep,
    backends: BackendsDep,
    kind: Annotated[Literal["schema", "paper"] | None, Query()] = None,
) -> list[BlueprintSummaryOut]:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        subjects = _subjects(unit, who.college_id)
        found = unit.blueprints.list(
            who.college_id,
            reach=unit.reach(user),
            kind=None if kind is None else BlueprintKind(kind),
        )
    return [BlueprintSummaryOut(**_summary(b, subjects)) for b in found]


@router.get(
    "/schemas",
    response_model=SchemaListOut,
    summary="Manage Schema: the schemas of my subjects, by title, and the batches in use",
    description="`q` matches the name anywhere and `target_batch` exactly, both ignoring case. "
    "`used_in` counts the papers (newest versions) generated from each schema.",
)
def list_schemas(
    who: PrincipalDep,
    backends: BackendsDep,
    q: Annotated[str, Query(max_length=200)] = "",
    target_batch: Annotated[str, Query(max_length=100)] = "",
) -> SchemaListOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        subjects = _subjects(unit, who.college_id)
        listing = unit.blueprints.schemas(
            who.college_id, reach=unit.reach(user), title=q, target_batch=target_batch
        )
    return SchemaListOut(
        items=[_card(b, subjects) for b in listing.items], batches=list(listing.batches)
    )


@router.get(
    "/papers",
    response_model=PaperListOut,
    summary="Manage Question Papers: one page of the papers of my subjects",
    description="`q` matches the title or the descriptor anywhere, `descriptor` exactly (both "
    "ignoring case), `created_on` the UTC date version 1 was saved. Most recently saved first. "
    "`descriptors` lists every descriptor in use (not filtered).",
)
def list_papers(
    who: PrincipalDep,
    backends: BackendsDep,
    q: Annotated[str, Query(max_length=200)] = "",
    descriptor: Annotated[str, Query(max_length=50)] = "",
    created_on: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 12,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PaperListOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        subjects = _subjects(unit, who.college_id)
        listing = unit.blueprints.papers(
            who.college_id,
            reach=unit.reach(user),
            search=q,
            descriptor=descriptor,
            created_on=created_on,
            limit=limit,
            offset=offset,
        )
    return PaperListOut(
        items=[_paper_card(b, subjects, listing.in_use) for b in listing.items],
        total=listing.total,
        limit=limit,
        offset=offset,
        descriptors=list(listing.descriptors),
    )


@router.post(
    "/papers",
    status_code=status.HTTP_201_CREATED,
    response_model=BlueprintOut,
    responses={**_INVALID, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
    summary="Create a question paper (SMEs only)",
    description="From a schema of the same subject (as `/blueprints/{id}/paper`), or a starter "
    "paper: section A of five unlinked 2-mark questions, course code = the subject's code.",
)
def create_paper(body: PaperIn, who: PrincipalDep, backends: BackendsDep) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        paper = unit.blueprints.create_paper(
            who.college_id,
            who.user_id,
            title=body.title,
            subject_id=SubjectId(body.subject_id),
            descriptor=body.descriptor,
            schema_id=None if body.schema_id is None else BlueprintId(body.schema_id),
        )
        return _full_out(paper, unit)


def _file_name(exam_code: str, title: str, version: int) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "-", exam_code or title).strip("-")[:60] or "paper"
    return f"paper-{base}-v{version}.pdf"


@router.get(
    "/blueprints/{blueprint_id}/versions/{version}/paper.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}, 404: {"model": ErrorOut}},
    summary="The question paper of one version as a PDF (D224)",
    description="Rendered on request: header, sections with their rules, OR pairs, sub-parts, "
    "each question's text (its newest version) and marks. Never keys, rubrics or reference "
    "answers. A schema, another subject's or college's paper, or an unknown version is 404.",
)
def paper_pdf(
    blueprint_id: UUID,
    version: Annotated[int, Path(ge=1)],
    who: PrincipalDep,
    backends: BackendsDep,
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        document, pdf = unit.paper_sheets.pdf(
            who.college_id, BlueprintId(blueprint_id), version, reach=unit.reach(user)
        )
    name = _file_name(document.exam_code, document.title, document.version)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            # The questions' newest text is printed, so a version's PDF can change.
            "Cache-Control": "private, no-cache",
        },
    )


@router.post(
    "/blueprints",
    status_code=status.HTTP_201_CREATED,
    response_model=BlueprintOut,
    responses={**_INVALID, 403: {"model": ErrorOut, "description": "SMEs only."}},
    summary="Create a blueprint of my college",
)
def create_blueprint(document: Document, who: PrincipalDep, backends: BackendsDep) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        blueprint = unit.blueprints.create(who.college_id, who.user_id, document)
        return _full_out(blueprint, unit)


@router.get(
    "/blueprints/{blueprint_id}",
    response_model=BlueprintOut,
    responses={404: {"model": ErrorOut}},
    summary="One blueprint: the newest version, or ?version=",
)
def get_blueprint(
    blueprint_id: UUID,
    who: PrincipalDep,
    backends: BackendsDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        blueprint = unit.blueprints.get(
            who.college_id, BlueprintId(blueprint_id), version, reach=unit.reach(user)
        )
        return _full_out(blueprint, unit)


@router.get(
    "/blueprints/{blueprint_id}/versions",
    response_model=list[BlueprintSummaryOut],
    responses={404: {"model": ErrorOut}},
    summary="Every version of a blueprint, oldest first",
)
def blueprint_versions(
    blueprint_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> list[BlueprintSummaryOut]:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        versions = unit.blueprints.versions(
            who.college_id, BlueprintId(blueprint_id), reach=unit.reach(user)
        )
        subjects = _subjects(unit, who.college_id)
    if not versions:
        raise NotFoundError(f"blueprint {blueprint_id}")
    return [BlueprintSummaryOut(**_summary(b, subjects)) for b in versions]


@router.put(
    "/blueprints/{blueprint_id}",
    response_model=BlueprintOut,
    responses={
        **_INVALID,
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
    },
    summary="Save the document as the next version (SMEs only)",
    description="Earlier versions stay valid for booklets pinned to them. Once a booklet is "
    "pinned to a version of a paper, only its title, descriptor, description, target batch, "
    "duration and course code may change (409 otherwise: clone it, D222).",
)
def update_blueprint(
    blueprint_id: UUID, document: Document, who: PrincipalDep, backends: BackendsDep
) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        blueprint = unit.blueprints.update(
            who.college_id, who.user_id, BlueprintId(blueprint_id), document
        )
        return _full_out(blueprint, unit)


@router.post(
    "/blueprints/{blueprint_id}/clone",
    status_code=status.HTTP_201_CREATED,
    response_model=BlueprintOut,
    responses={**_INVALID, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
    summary="Copy a schema or paper into a new blueprint (SMEs only)",
    description='Version 1 of a new blueprint, titled "<title> (copy)" unless a title is '
    "given; a paper's copy has no exam code. Not a version of the source.",
)
def clone_blueprint(
    blueprint_id: UUID,
    who: PrincipalDep,
    backends: BackendsDep,
    body: Annotated[CloneIn | None, Body()] = None,
) -> BlueprintOut:
    body = body or CloneIn()
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        blueprint = unit.blueprints.clone(
            who.college_id,
            who.user_id,
            BlueprintId(blueprint_id),
            title=body.title,
            target_batch=body.target_batch,
            weightage_note=body.weightage_note,
            descriptor=body.descriptor,
        )
        return _full_out(blueprint, unit)


@router.post(
    "/blueprints/{blueprint_id}/paper",
    status_code=status.HTTP_201_CREATED,
    response_model=BlueprintOut,
    responses={
        **_INVALID,
        403: {"model": ErrorOut},
        404: {"model": ErrorOut},
        409: {"model": ErrorOut},
    },
    summary="Generate a paper draft from a schema (SMEs only)",
    description="The schema's sections, rules and marks, every question unlinked, no exam code, "
    "`from_schema` naming the schema (D220). A paper as the source is 409.",
)
def generate_paper(
    blueprint_id: UUID,
    who: PrincipalDep,
    backends: BackendsDep,
    body: Annotated[GeneratePaperIn | None, Body()] = None,
) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        paper = unit.blueprints.generate_paper(
            who.college_id,
            who.user_id,
            BlueprintId(blueprint_id),
            title=None if body is None else body.title,
            descriptor=None if body is None else body.descriptor,
        )
        return _full_out(paper, unit)


# --- permanent deletion (P28, D166) -----------------------------------------------------------


@router.get(
    "/blueprints/{blueprint_id}/deletion",
    response_model=DeletionCheckOut,
    responses={404: {"model": ErrorOut}},
    summary="Can this paper or schema be deleted yet? The reasons it cannot",
)
def blueprint_deletion(
    blueprint_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> DeletionCheckOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        check = unit.content_deletion.check_paper(
            who.college_id, who.user_id, BlueprintId(blueprint_id)
        )
        return DeletionCheckOut(allowed=check.allowed, blockers=list(check.blockers))


@router.delete(
    "/blueprints/{blueprint_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
    summary="Delete a paper or schema for good, every version (SMEs only)",
    description="Allowed once no unit that is not frozen uses it (D166). Frozen records and "
    "issued result sheets stay. Audited without content.",
)
def delete_blueprint(blueprint_id: UUID, who: PrincipalDep, backends: BackendsDep) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.AUTHOR_CONTENT)
        unit.content_deletion.delete_paper(who.college_id, who.user_id, BlueprintId(blueprint_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
