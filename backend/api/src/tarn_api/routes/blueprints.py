"""Subjects and exam blueprints, the college's own content (D165): ``/api/v1/subjects``,
``/api/v1/blueprints``. The admin keeps the subject list (D160); SMEs read and write the papers
of their own subjects (D178). Another college's subject or blueprint, and a paper of a subject
the SME does not have, is a 404. Blueprints are never deleted yet:
booklets pin a version and content rows are append-only until P28."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Query, Response, status

from tarn_api.backends import Unit, unit_of_work
from tarn_api.schemas import (
    BlueprintOut,
    BlueprintSummaryOut,
    DeletionCheckOut,
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
        "(version 1.1; 1.0 is still read) and the examples next to it."
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
    }


def _subjects(unit: Unit, college_id: CollegeId) -> dict[UUID, Subject]:
    return {s.id: s for s in unit.subjects.all(college_id)}


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
    summary="The newest version of every blueprint of my college, by title",
)
def list_blueprints(who: PrincipalDep, backends: BackendsDep) -> list[BlueprintSummaryOut]:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who, Capability.AUTHOR_CONTENT)
        subjects = _subjects(unit, who.college_id)
        found = unit.blueprints.list(who.college_id, reach=unit.reach(user))
    return [BlueprintSummaryOut(**_summary(b, subjects)) for b in found]


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
    responses={**_INVALID, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}},
    summary="Save the document as the next version (SMEs only)",
    description="Earlier versions stay valid for booklets pinned to them.",
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
