"""The SME's global search (P37, D228, D243): ``GET /api/v1/search?q=``. Their own units and
those units' students, the papers, schemas and questions of their subjects; at most 5 per
group. Counted per user (``SEARCH_USER``). The query is never logged (the access log drops
query strings) or audited."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from tarn_api.backends import unit_of_work
from tarn_api.ratelimit import SEARCH_USER, check
from tarn_api.schemas import (
    BlueprintHitOut,
    ErrorOut,
    QuestionHitOut,
    SearchOut,
    StudentHitOut,
    UnitHitOut,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.personas import Capability
from tarn_core.domain.search import BlueprintHit

router = APIRouter(
    prefix="/api/v1/search",
    tags=["search"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 429: {"model": ErrorOut}},
)


def _blueprint(b: BlueprintHit) -> BlueprintHitOut:
    return BlueprintHitOut(
        blueprint_id=b.blueprint_id,
        title=b.title,
        exam_code=b.exam_code,
        descriptor=b.descriptor,
        subject_id=b.subject_id,
    )


@router.get(
    "",
    response_model=SearchOut,
    summary="Global search (SME)",
    description="2 to 80 characters (after trimming). Units: code, paper title, exam code, "
    "year. Students: roll number from its start or name anywhere, in the SME's own units. "
    "Papers and schemas: title, exam code, descriptor. Questions: code, text, topic, keys. "
    "Only the SME's own units and subjects; at most 5 per group.",
    responses={422: {"model": ErrorOut}},
)
def search(
    request: Request,
    who: PrincipalDep,
    backends: BackendsDep,
    q: Annotated[
        str,
        Query(description="2 to 80 characters, checked in the core (no echo on refusal)."),
    ],
) -> SearchOut:
    check(request, (SEARCH_USER, f"{who.college_id}|{who.user_id}"))
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.VIEW_OWN_WORK)
        found = unit.search.search(who.college_id, who.user_id, q)
    return SearchOut(
        units=[
            UnitHitOut(
                unit_id=u.unit_id,
                code=u.code,
                paper_title=u.paper_title,
                exam_code=u.exam_code,
                year=u.year,
                due_date=u.due_date,
                state=u.state,
            )
            for u in found.units
        ],
        students=[
            StudentHitOut(booklet_id=s.booklet_id, unit_id=s.unit_id, name=s.name, usn=s.usn)
            for s in found.students
        ],
        papers=[_blueprint(b) for b in found.papers],
        schemas=[_blueprint(b) for b in found.schemas],
        questions=[
            QuestionHitOut(
                question_id=x.question_id, code=x.code, text=x.text, subject_id=x.subject_id
            )
            for x in found.questions
        ],
    )
