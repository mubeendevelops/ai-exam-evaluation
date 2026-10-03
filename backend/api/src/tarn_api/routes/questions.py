"""The question bank: ``/api/v1/questions``. Admins and teachers. Questions, reference answers,
rubric criteria, glossaries, answer-key files and reference diagrams are global content with an
owning college: the owner edits (a new version each time), everyone else copies. Nothing is
deleted; removing a key or criterion retires it."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from tarn_api.backends import unit_of_work
from tarn_api.schemas import (
    ContentRefOut,
    CriterionBody,
    CriterionOut,
    DiagramCriterion,
    DiagramParamsBody,
    ErrorOut,
    GlossaryIn,
    GlossaryOut,
    KeyFileOut,
    ListCriterion,
    ListItemBody,
    ListParamsBody,
    NumericCriterion,
    NumericParamsBody,
    QuestionCreateIn,
    QuestionOut,
    QuestionPageOut,
    QuestionSummaryOut,
    QuestionUpdateIn,
    ReferenceAnswerIn,
    ReferenceAnswerOut,
    ReferenceDiagramOut,
    RubricIn,
    RubricOut,
    SemanticCriterion,
    SemanticParamsBody,
)
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.content import (
    CriterionType,
    DiagramComponent,
    DiagramParams,
    Difficulty,
    KeyFile,
    ListItem,
    ListParams,
    NumericParams,
    Question,
    ReferenceAnswer,
    ReferenceDiagram,
    RubricCriterion,
    SemanticParams,
)
from tarn_core.domain.tenancy import Role
from tarn_core.ids import (
    CollegeId,
    CriterionId,
    KeyFileId,
    QuestionId,
    ReferenceAnswerId,
    ReferenceDiagramId,
    SubjectId,
)
from tarn_core.ports.repositories import QuestionQuery
from tarn_core.services.question_bank import (
    DIAGRAM_MAX_BYTES,
    KEY_FILE_MAX_BYTES,
    CriterionSpec,
    QuestionDetail,
    QuestionSummary,
)

router = APIRouter(
    prefix="/api/v1",
    tags=["question bank"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)
_FAILED: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorOut},
    422: {"model": ErrorOut, "description": "A rule is broken; the message says which."},
}
_OWNER_ONLY: dict[int | str, dict[str, object]] = {
    **_FAILED,
    403: {"model": ErrorOut, "description": "Another college owns it: copy it first."},
}
_UPLOAD_BODY = {
    "required": True,
    "content": {
        "application/pdf": {"schema": {"type": "string", "format": "binary"}},
        "image/png": {"schema": {"type": "string", "format": "binary"}},
        "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
    },
}
_PNG_BODY = {
    "required": True,
    "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
}


# --- conversions ------------------------------------------------------------------------------


def _dec(value: float) -> Decimal:
    """A JSON number as a Decimal that prints naturally (4, not 4.0)."""
    number = Decimal(str(value))
    return Decimal(int(number)) if number == number.to_integral_value() else number


def _num(value: Decimal) -> float:
    return float(value)


def _spec(body: CriterionBody) -> CriterionSpec:
    match body:
        case ListCriterion():
            ctype = CriterionType.LIST
            params: ListParams | NumericParams | SemanticParams | DiagramParams = ListParams(
                items=tuple(
                    ListItem(term=i.term, synonyms=tuple(i.synonyms)) for i in body.params.items
                ),
                required_count=body.params.required_count,
            )
        case NumericCriterion():
            ctype = CriterionType.NUMERIC
            params = NumericParams(
                expected=_dec(body.params.expected),
                tolerance=_dec(body.params.tolerance),
                unit=body.params.unit,
            )
        case SemanticCriterion():
            ctype = CriterionType.SEMANTIC
            params = SemanticParams(reference_statement=body.params.reference_statement)
        case DiagramCriterion():
            ctype = CriterionType.DIAGRAM
            params = DiagramParams(
                reference_diagram_id=ReferenceDiagramId(body.params.reference_diagram_id),
                component=DiagramComponent(body.params.component),
            )
    return CriterionSpec(
        id=None if body.id is None else CriterionId(body.id),
        label=body.label,
        type=ctype,
        weight=_dec(body.weight),
        params=params,
    )


def _criterion_body(c: RubricCriterion) -> CriterionBody:
    common = {"id": c.id, "label": c.label, "weight": _num(c.weight)}
    match c.params:
        case ListParams():
            return ListCriterion(
                type="list",
                params=ListParamsBody(
                    items=[
                        ListItemBody(term=i.term, synonyms=list(i.synonyms)) for i in c.params.items
                    ],
                    required_count=c.params.required_count,
                ),
                **common,
            )
        case NumericParams():
            return NumericCriterion(
                type="numeric",
                params=NumericParamsBody(
                    expected=_num(c.params.expected),
                    tolerance=_num(c.params.tolerance),
                    unit=c.params.unit,
                ),
                **common,
            )
        case SemanticParams():
            return SemanticCriterion(
                type="semantic",
                params=SemanticParamsBody(reference_statement=c.params.reference_statement),
                **common,
            )
        case DiagramParams():
            return DiagramCriterion(
                type="diagram",
                params=DiagramParamsBody(
                    reference_diagram_id=c.params.reference_diagram_id,
                    component=c.params.component.value,
                ),
                **common,
            )
        case _:  # LLM criteria exist in the core but are off by default (D5) and not editable here
            raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Unsupported criterion.")


def _summary(q: Question, college_id: CollegeId) -> dict[str, object]:
    copied = q.meta.copied_from
    return {
        "id": q.id,
        "version": q.meta.version,
        "code": q.code,
        "text": q.text,
        "max_marks": _num(q.max_marks),
        "difficulty": q.difficulty.value,
        "category": q.category,
        "subject_id": q.subject_id,
        "owning_college_id": q.meta.owning_college_id,
        "owned": q.meta.owning_college_id == college_id,
        "copied_from": None
        if copied is None
        else ContentRefOut(kind=copied.kind.value, id=copied.id, version=copied.version),
    }


def _summary_out(s: QuestionSummary, college_id: CollegeId) -> QuestionSummaryOut:
    return QuestionSummaryOut(
        **_summary(s.question, college_id),
        subject_name=s.subject_name,
        key_count=s.key_count,
        owner_name=s.owner_name,
    )


def _file_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _detail_out(d: QuestionDetail, college_id: CollegeId) -> QuestionOut:
    q = d.question
    base = f"/api/v1/questions/{q.id}"
    glossary = d.glossary
    teacher_terms = list(glossary.teacher_terms) if glossary else []
    labels = list(glossary.reference_labels) if glossary else []
    return QuestionOut(
        **_summary(q, college_id),
        subject_name=None if d.subject is None else d.subject.name,
        key_count=len(d.answers) + len(d.key_files),
        owner_name=d.owner_name,
        reference_answers=[
            ReferenceAnswerOut(
                id=a.id,
                version=a.meta.version,
                text=a.text,
                guidance_only=a.guidance_only,
                synthetic=a.synthetic,
            )
            for a in d.answers
        ],
        rubric=RubricOut(
            criteria=[
                CriterionOut(version=c.meta.version, criterion=_criterion_body(c))
                for c in d.criteria
            ],
            total=_num(d.rubric_total),
            max_marks=_num(q.max_marks),
            complete=bool(d.criteria) and d.rubric_total == q.max_marks,
        ),
        glossary=GlossaryOut(
            teacher_terms=teacher_terms,
            reference_labels=labels,
            terms=list(glossary.terms) if glossary else [],
        ),
        key_files=[_key_file_out(f, base) for f in d.key_files],
        diagrams=[_diagram_out(x, base) for x in d.diagrams],
    )


def _key_file_out(f: KeyFile, base: str) -> KeyFileOut:
    return KeyFileOut(
        id=f.id,
        name=f.name,
        media_type=f.media_type,
        size_bytes=f.size_bytes,
        keywords=list(f.keywords),
        content_url=f"{base}/key-files/{f.id}/content",
    )


def _diagram_out(d: ReferenceDiagram, base: str) -> ReferenceDiagramOut:
    return ReferenceDiagramOut(
        id=d.id,
        name=_file_name(d.png.value),
        node_count=len(d.graph.nodes),
        edge_count=len(d.graph.edges),
        labels=[n.label for n in d.graph.nodes if n.label],
        content_url=f"{base}/diagrams/{d.id}/content",
    )


async def _upload_bytes(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"The file is larger than {limit // (1024 * 1024)} MB.",
        )
    return await request.body()


# --- list, search -----------------------------------------------------------------------------


@router.get(
    "/questions",
    response_model=QuestionPageOut,
    summary="Search the question bank (every college's questions)",
    description="Every filter given must match; text filters ignore case. Ordered by code.",
)
def search_questions(
    who: PrincipalDep,
    backends: BackendsDep,
    keyword: Annotated[
        str, Query(max_length=200, description="In the question, its code, topic or key.")
    ] = "",
    code: Annotated[str, Query(max_length=40, description="Part of the code.")] = "",
    topic: Annotated[str, Query(max_length=200, description="The topic exactly.")] = "",
    subject_id: UUID | None = None,
    difficulty: Difficulty | None = None,
    mine: Annotated[bool, Query(description="Only questions my college owns.")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QuestionPageOut:
    query = QuestionQuery(
        keyword=keyword.strip(),
        code=code.strip(),
        topic=topic.strip(),
        subject_id=None if subject_id is None else SubjectId(subject_id),
        difficulty=difficulty,
        owner_id=who.college_id if mine else None,
    )
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        page = unit.bank.search(query, limit=limit, offset=offset)
    return QuestionPageOut(
        items=[_summary_out(s, who.college_id) for s in page.items],
        total=page.total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/question-topics",
    response_model=list[str],
    summary="The topics in use, for the topic filter",
)
def list_topics(
    who: PrincipalDep, backends: BackendsDep, subject_id: UUID | None = None
) -> list[str]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        return list(unit.bank.topics(None if subject_id is None else SubjectId(subject_id)))


# --- one question -----------------------------------------------------------------------------


@router.post(
    "/questions",
    status_code=status.HTTP_201_CREATED,
    response_model=QuestionOut,
    responses={**_FAILED, 409: {"model": ErrorOut, "description": "The code is taken."}},
    summary="Create a question owned by my college, optionally with its key and rubric",
)
def create_question(
    body: QuestionCreateIn, who: PrincipalDep, backends: BackendsDep
) -> QuestionOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        question = unit.bank.create_question(
            who.college_id,
            who.user_id,
            subject_id=SubjectId(body.subject_id),
            code=body.code,
            text=body.text,
            max_marks=_dec(body.max_marks),
            difficulty=Difficulty(body.difficulty),
            category=body.category,
            reference_answer=body.reference_answer,
            criteria=[_spec(c) for c in body.criteria],
        )
        return _detail_out(unit.bank.detail(question.id), who.college_id)


@router.get(
    "/questions/{question_id}",
    response_model=QuestionOut,
    responses={404: {"model": ErrorOut}},
    summary="One question with its keys, rubric, glossary and files",
)
def get_question(question_id: UUID, who: PrincipalDep, backends: BackendsDep) -> QuestionOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        return _detail_out(unit.bank.detail(QuestionId(question_id)), who.college_id)


@router.put(
    "/questions/{question_id}",
    response_model=QuestionOut,
    responses={**_OWNER_ONLY, 409: {"model": ErrorOut}},
    summary="Save the next version of a question (owning college only)",
    description="The subject never changes. If the marks change and a rubric exists, send the "
    "new `criteria` too: their weights must add up to the marks.",
)
def update_question(
    question_id: UUID, body: QuestionUpdateIn, who: PrincipalDep, backends: BackendsDep
) -> QuestionOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.bank.update_question(
            who.college_id,
            who.user_id,
            QuestionId(question_id),
            code=body.code,
            text=body.text,
            max_marks=_dec(body.max_marks),
            difficulty=Difficulty(body.difficulty),
            category=body.category,
            criteria=None if body.criteria is None else [_spec(c) for c in body.criteria],
        )
        return _detail_out(unit.bank.detail(QuestionId(question_id)), who.college_id)


@router.post(
    "/questions/{question_id}/copy",
    status_code=status.HTTP_201_CREATED,
    response_model=QuestionOut,
    responses={404: {"model": ErrorOut}},
    summary="Copy a question, with its keys, rubric, glossary and files, to my college",
)
def copy_question(question_id: UUID, who: PrincipalDep, backends: BackendsDep) -> QuestionOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        copy = unit.content.copy_question(who.college_id, who.user_id, QuestionId(question_id))
        return _detail_out(unit.bank.detail(copy.id), who.college_id)


# --- rubric, reference answers, glossary ------------------------------------------------------


@router.put(
    "/questions/{question_id}/rubric",
    response_model=RubricOut,
    responses=_OWNER_ONLY,
    summary="Replace the rubric (owning college only)",
    description="Criteria with an `id` get a new version if changed; criteria left out are "
    "retired. The weights must add up to the question's marks (an empty list is allowed: a "
    "guidance-only key is marked by hand).",
)
def put_rubric(
    question_id: UUID, body: RubricIn, who: PrincipalDep, backends: BackendsDep
) -> RubricOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.bank.set_rubric(
            who.college_id,
            who.user_id,
            QuestionId(question_id),
            [_spec(c) for c in body.criteria],
        )
        return _detail_out(unit.bank.detail(QuestionId(question_id)), who.college_id).rubric


def _answer_out(a: ReferenceAnswer) -> ReferenceAnswerOut:
    return ReferenceAnswerOut(
        id=a.id,
        version=a.meta.version,
        text=a.text,
        guidance_only=a.guidance_only,
        synthetic=a.synthetic,
    )


@router.post(
    "/questions/{question_id}/reference-answers",
    status_code=status.HTTP_201_CREATED,
    response_model=ReferenceAnswerOut,
    responses=_OWNER_ONLY,
    summary="Add a reference answer (owning college only)",
)
def add_reference_answer(
    question_id: UUID, body: ReferenceAnswerIn, who: PrincipalDep, backends: BackendsDep
) -> ReferenceAnswerOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        return _answer_out(
            unit.bank.add_reference_answer(
                who.college_id,
                who.user_id,
                QuestionId(question_id),
                text=body.text,
                guidance_only=body.guidance_only,
            )
        )


@router.put(
    "/questions/{question_id}/reference-answers/{answer_id}",
    response_model=ReferenceAnswerOut,
    responses=_OWNER_ONLY,
    summary="Save the next version of a reference answer (owning college only)",
)
def edit_reference_answer(
    question_id: UUID,
    answer_id: UUID,
    body: ReferenceAnswerIn,
    who: PrincipalDep,
    backends: BackendsDep,
) -> ReferenceAnswerOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        return _answer_out(
            unit.bank.edit_reference_answer(
                who.college_id,
                who.user_id,
                QuestionId(question_id),
                ReferenceAnswerId(answer_id),
                text=body.text,
                guidance_only=body.guidance_only,
            )
        )


@router.delete(
    "/questions/{question_id}/reference-answers/{answer_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_OWNER_ONLY,
    summary="Retire a reference answer (owning college only)",
    description="Saves a retired version: it stops being listed, and scores that used it keep it.",
)
def retire_reference_answer(
    question_id: UUID, answer_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.bank.retire_reference_answer(
            who.college_id, who.user_id, QuestionId(question_id), ReferenceAnswerId(answer_id)
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/questions/{question_id}/glossary",
    response_model=GlossaryOut,
    responses=_OWNER_ONLY,
    summary="Set the glossary terms (owning college only)",
    description="The glossary is the teacher's terms plus the labels of the reference "
    "diagrams, which are kept up to date automatically.",
)
def put_glossary(
    question_id: UUID, body: GlossaryIn, who: PrincipalDep, backends: BackendsDep
) -> GlossaryOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        unit.bank.set_glossary_terms(
            who.college_id, who.user_id, QuestionId(question_id), body.terms
        )
        return _detail_out(unit.bank.detail(QuestionId(question_id)), who.college_id).glossary


# --- files ------------------------------------------------------------------------------------


@router.post(
    "/questions/{question_id}/key-files",
    status_code=status.HTTP_201_CREATED,
    response_model=KeyFileOut,
    responses={**_OWNER_ONLY, 413: {"model": ErrorOut}},
    openapi_extra={"requestBody": _UPLOAD_BODY},
    summary="Upload an answer key or sample (PDF or image; owning college only)",
    description="The request body is the file itself. The file type is read from its content. "
    "`confirm_no_student_data` must be true: keys are written by faculty and hold no student "
    "data (C9).",
)
async def upload_key_file(
    question_id: UUID,
    request: Request,
    who: PrincipalDep,
    backends: BackendsDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    confirm_no_student_data: Annotated[bool, Query()] = False,
    keywords: Annotated[list[str], Query(max_length=30)] = [],  # noqa: B006  (read-only query)
) -> KeyFileOut:
    data = await _upload_bytes(request, KEY_FILE_MAX_BYTES)
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        key_file = unit.bank.upload_key_file(
            who.college_id,
            who.user_id,
            QuestionId(question_id),
            file_name=filename,
            data=data,
            declared_type=request.headers.get("content-type"),
            keywords=keywords,
            no_student_data=confirm_no_student_data,
        )
    return _key_file_out(key_file, f"/api/v1/questions/{question_id}")


@router.get(
    "/questions/{question_id}/key-files/{file_id}/content",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}, 404: {"model": ErrorOut}},
    summary="Download an answer-key file",
)
def key_file_content(
    question_id: UUID, file_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        key_file, data = unit.bank.read_key_file(QuestionId(question_id), KeyFileId(file_id))
    return _file_response(data, key_file.media_type, key_file.name)


@router.post(
    "/questions/{question_id}/diagrams",
    status_code=status.HTTP_201_CREATED,
    response_model=ReferenceDiagramOut,
    responses={**_OWNER_ONLY, 413: {"model": ErrorOut}},
    openapi_extra={"requestBody": _PNG_BODY},
    summary="Upload a reference diagram as PNG (owning college only)",
    description="The request body is the PNG. Its nodes and edges are read from the picture "
    "later (P14); until then the graph is empty. `confirm_no_student_data` must be true (C9).",
)
async def upload_reference_diagram(
    question_id: UUID,
    request: Request,
    who: PrincipalDep,
    backends: BackendsDep,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    confirm_no_student_data: Annotated[bool, Query()] = False,
) -> ReferenceDiagramOut:
    data = await _upload_bytes(request, DIAGRAM_MAX_BYTES)
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        diagram = unit.bank.upload_reference_diagram(
            who.college_id,
            who.user_id,
            QuestionId(question_id),
            file_name=filename,
            data=data,
            declared_type=request.headers.get("content-type"),
            no_student_data=confirm_no_student_data,
        )
    return _diagram_out(diagram, f"/api/v1/questions/{question_id}")


@router.get(
    "/questions/{question_id}/diagrams/{diagram_id}/content",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}, 404: {"model": ErrorOut}},
    summary="Download a reference diagram PNG",
)
def diagram_content(
    question_id: UUID, diagram_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> Response:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN, Role.TEACHER)
        diagram, data = unit.bank.read_reference_diagram(
            QuestionId(question_id), ReferenceDiagramId(diagram_id)
        )
    return _file_response(data, "image/png", _file_name(diagram.png.value))


def _file_response(data: bytes, media_type: str, name: str) -> Response:
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=300",
        },
    )
