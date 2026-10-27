"""Subjects and exam blueprints, the college's own content (D165): ``/api/v1/subjects``,
``/api/v1/blueprints``. Admins and teachers read; teachers write (the admin cannot author
papers). Another college's subject or blueprint is a 404. Blueprints are never deleted yet:
booklets pin a version and content rows are append-only until P28."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Query, status

from tarn_api.backends import Unit, unit_of_work
from tarn_api.schemas import (
    BlueprintOut,
    BlueprintSummaryOut,
    ErrorOut,
    IssueOut,
    SectionSummaryOut,
    SubjectIn,
    SubjectOut,
    ValidationOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.content import Subject
from tarn_core.domain.tenancy import Role
from tarn_core.errors import NotFoundError
from tarn_core.ids import BlueprintId, CollegeId
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
        "(version 1.0) and the examples next to it."
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
    return SubjectOut(id=subject.id, code=subject.code, name=subject.name)


def _summary(b: ExamBlueprint, subjects: dict[UUID, Subject]) -> dict[str, Any]:
    subject = subjects.get(b.subject_id)
    return {
        "id": b.id,
        "version": b.meta.version,
        "title": b.title,
        "course_code": b.course_code,
        "subject_id": b.subject_id,
        "subject_name": None if subject is None else subject.name,
        "total_marks": float(b.total_marks),
        "duration_minutes": b.duration_minutes,
        "section_count": len(b.sections),
        "question_count": sum(1 for _ in b.slots()),
        "unlinked_count": len(b.unlinked_leaves()),
    }


def _subjects(unit: Unit, college_id: CollegeId) -> dict[UUID, Subject]:
    return {s.id: s for s in unit.subjects.list(college_id)}


def _full_out(b: ExamBlueprint, unit: Unit) -> BlueprintOut:
    subjects = _subjects(unit, b.meta.college_id)
    return BlueprintOut(**_summary(b, subjects), document=blueprint_to_document(b))


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


@router.get("/subjects", response_model=list[SubjectOut], summary="My college's subjects, by name")
def list_subjects(who: PrincipalDep, backends: BackendsDep) -> list[SubjectOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        subjects = unit.subjects.list(who.college_id)
    return [_subject_out(s) for s in subjects]


@router.post(
    "/subjects",
    status_code=status.HTTP_201_CREATED,
    response_model=SubjectOut,
    responses={422: {"model": ErrorOut}},
    summary="Create a subject of my college",
)
def create_subject(body: SubjectIn, who: PrincipalDep, backends: BackendsDep) -> SubjectOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        subject = unit.subjects.create(who.college_id, who.user_id, code=body.code, name=body.name)
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
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        report = unit.blueprints.check(document)
    return _validation_out(report)


@router.get(
    "/blueprints",
    response_model=list[BlueprintSummaryOut],
    summary="The newest version of every blueprint of my college, by title",
)
def list_blueprints(who: PrincipalDep, backends: BackendsDep) -> list[BlueprintSummaryOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        subjects = _subjects(unit, who.college_id)
        found = unit.blueprints.list(who.college_id)
    return [BlueprintSummaryOut(**_summary(b, subjects)) for b in found]


@router.post(
    "/blueprints",
    status_code=status.HTTP_201_CREATED,
    response_model=BlueprintOut,
    responses={**_INVALID, 403: {"model": ErrorOut, "description": "Teachers only."}},
    summary="Create a blueprint of my college",
)
def create_blueprint(document: Document, who: PrincipalDep, backends: BackendsDep) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
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
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        blueprint = unit.blueprints.get(who.college_id, BlueprintId(blueprint_id), version)
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
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        versions = unit.blueprints.versions(who.college_id, BlueprintId(blueprint_id))
        subjects = _subjects(unit, who.college_id)
    if not versions:
        raise NotFoundError(f"blueprint {blueprint_id}")
    return [BlueprintSummaryOut(**_summary(b, subjects)) for b in versions]


@router.put(
    "/blueprints/{blueprint_id}",
    response_model=BlueprintOut,
    responses={**_INVALID, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
    summary="Save the document as the next version (teachers only)",
    description="Earlier versions stay valid for booklets pinned to them.",
)
def update_blueprint(
    blueprint_id: UUID, document: Document, who: PrincipalDep, backends: BackendsDep
) -> BlueprintOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        blueprint = unit.blueprints.update(
            who.college_id, who.user_id, BlueprintId(blueprint_id), document
        )
        return _full_out(blueprint, unit)
