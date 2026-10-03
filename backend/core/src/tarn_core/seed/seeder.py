"""The development seed, written against ports so that it runs on the in-memory adapters in
tests and on PostgreSQL + MinIO for ``tarn seed`` / ``make seed``.

Two steps per college, each idempotent (a second run finds what is there and changes nothing):

1. ``seed_accounts``: register the college and its admin the way a person would (verify the
   email, operator approval), invite two teachers who accept, import the fictitious roster.
2. ``seed_content``: subjects, the questions the college owns (reference answers, rubric,
   glossary, reference diagrams) and the exam blueprints linked to them.

A question is found by its code within the owning college and a blueprint by its title; what
exists is left alone, even if the seed data changed since (edit it in the app instead)."""

import csv
import io
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.content import (
    CriterionParams,
    DiagramParams,
    ListItem,
    ListParams,
    NumericParams,
    Question,
    SemanticParams,
    Subject,
)
from tarn_core.domain.identity import TenantRecord, TenantStatus
from tarn_core.domain.tenancy import Role, User, canonical_email
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, QuestionId, ReferenceDiagramId, SubjectId, UserId
from tarn_core.ports.identity import EmailMessage, IdentityStore
from tarn_core.ports.repositories import (
    CollegeRepository,
    ContentRepository,
    QuestionQuery,
    StudentRepository,
    UserRepository,
)
from tarn_core.seed.model import CollegeSeed, CriterionSeed, PaperSeed, QuestionSeed
from tarn_core.services._support import Runtime
from tarn_core.services.auth import AccountService, AuthKit, AuthService
from tarn_core.services.blueprints import BlueprintService
from tarn_core.services.marking import check_blueprint_questions
from tarn_core.services.question_bank import CriterionSpec, QuestionBankService
from tarn_core.services.registration import RegistrationService
from tarn_core.services.roster import RosterService
from tarn_core.services.subjects import SubjectService

OPERATOR = "tarn seed"
"""Named in the audit event of the approval."""

AssetReader = Callable[[str], bytes]
"""Bytes of a seed asset (a reference diagram PNG) by file name."""


class SeedError(Exception):
    """The seed cannot continue (for example an existing tenant is not active)."""


@dataclass
class SeedReport:
    """What a run did, for the console and for tests."""

    created: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)

    def made(self, what: str, count: int = 1) -> None:
        self.created[what] = self.created.get(what, 0) + count

    def found(self, what: str, count: int = 1) -> None:
        self.kept[what] = self.kept.get(what, 0) + count

    def merge(self, other: "SeedReport") -> None:
        for what, count in other.created.items():
            self.made(what, count)
        for what, count in other.kept.items():
            self.found(what, count)


class _MailCatcher:
    """Collects the emails the account flows would send, so the seed can follow the links."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)

    def token(self, to: str) -> str:
        for message in reversed(self.sent):
            if message.to == to and "token=" in message.text:
                return message.text.split("token=", 1)[1].split()[0]
        raise SeedError("the account flow sent no link to follow")


@dataclass(frozen=True, slots=True)
class SeededAccounts:
    college_id: CollegeId
    admin_id: UserId
    teacher_ids: tuple[UserId, ...]


def seed_accounts(
    college: CollegeSeed,
    *,
    college_id: CollegeId,
    tenant: TenantRecord | None,
    password: str,
    identity: IdentityStore,
    users: UserRepository,
    colleges: CollegeRepository,
    students: StudentRepository,
    kit: AuthKit,
    runtime: Runtime,
    report: SeedReport,
) -> SeededAccounts:
    """The college, its admin and two teachers (who have chosen ``password``), the roster.

    ``tenant`` is the registry entry found for the Institution ID (None: register it with
    ``college_id``); the caller opens the units of work for that college."""
    catcher = _MailCatcher()
    kit = replace(kit, mailer=catcher)
    registration = RegistrationService(
        identity=identity, users=users, colleges=colleges, kit=kit, runtime=runtime
    )
    if tenant is None:
        registration.register(
            college_id,
            institution_id=college.institution_id,
            institution_name=college.name,
            admin_name=college.admin.name,
            email=college.admin.email,
            password=password,
        )
        registration.verify_email(college_id, catcher.token(college.admin.email))
        registration.approve(college_id, operator=OPERATOR)
        report.made("colleges")
    else:
        if tenant.status is not TenantStatus.ACTIVE:
            raise SeedError(
                f"{college.institution_id} exists but is {tenant.status.value}, not active: "
                "finish its registration (tarn tenants approve) or remove it"
            )
        report.found("colleges")

    present = {canonical_email(u.email): u for u in users.list(college_id)}
    admin = _only(u for u in present.values() if u.role is Role.ADMIN)
    accounts = AccountService(identity=identity, users=users, kit=kit, runtime=runtime)
    auth = AuthService(identity=identity, users=users, kit=kit, runtime=runtime)
    teacher_ids: list[UserId] = []
    for person in college.teachers:
        known = present.get(canonical_email(person.email))
        if known is not None:
            teacher_ids.append(known.id)
            report.found("teachers")
            continue
        teacher = accounts.invite_teacher(
            college_id, admin.id, display_name=person.name, email=person.email
        )
        auth.accept_invite(college_id, catcher.token(person.email), password)
        teacher_ids.append(teacher.id)
        report.made("teachers")

    _import_roster(college, college_id, admin, students, users, runtime, report)
    return SeededAccounts(college_id=college_id, admin_id=admin.id, teacher_ids=tuple(teacher_ids))


def _only(users: Iterable[User]) -> User:
    found = list(users)
    if not found:
        raise SeedError("the college has no admin")
    return found[0]


def _import_roster(
    college: CollegeSeed,
    college_id: CollegeId,
    admin: User,
    students: StudentRepository,
    users: UserRepository,
    runtime: Runtime,
    report: SeedReport,
) -> None:
    from tarn_core.domain.tenancy import normalise_usn

    current = {
        s.usn: s
        for s in (students.find_by_usn(college_id, normalise_usn(w.usn)) for w in college.students)
        if s is not None
    }
    if all(
        (found := current.get(normalise_usn(w.usn))) is not None
        and (found.name, found.class_section) == (w.name, w.class_section)
        for w in college.students
    ):
        report.found("students", len(college.students))
        return
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["name", "usn", "class/section"])
    for student in college.students:
        writer.writerow([student.name, student.usn, student.class_section])
    result = RosterService(students=students, users=users, runtime=runtime).import_csv(
        college_id, admin.id, buffer.getvalue()
    )
    if not result.imported:
        raise SeedError(f"the roster of {college.institution_id} was refused: {result.errors}")
    if result.created:
        report.made("students", result.created)
    if result.rows - result.created:
        report.found("students", result.rows - result.created)


@dataclass(frozen=True, slots=True)
class SeededContent:
    questions: Mapping[str, QuestionId]
    """Question code -> id, for every question of the college (new or found)."""


def seed_content(
    college: CollegeSeed,
    *,
    college_id: CollegeId,
    question_author: UserId,
    paper_author: UserId,
    content: ContentRepository,
    bank: QuestionBankService,
    subjects: SubjectService,
    blueprints: BlueprintService,
    assets: AssetReader,
    report: SeedReport,
) -> SeededContent:
    by_code = {s.code: s for s in subjects.list()}
    for subject in college.subjects:
        if subject.code in by_code:
            report.found("subjects")
            continue
        by_code[subject.code] = subjects.create(
            college_id, question_author, code=subject.code, name=subject.name
        )
        report.made("subjects")

    ids: dict[str, QuestionId] = {}
    for subject_code, seed in college.questions:
        ids[seed.code] = _seed_question(
            seed,
            subject=by_code[subject_code],
            college_id=college_id,
            author=question_author,
            bank=bank,
            assets=assets,
            report=report,
        )

    for paper in college.papers:
        _seed_paper(
            paper,
            subject=by_code[paper.subject_code],
            question_ids=ids,
            college_id=college_id,
            author=paper_author,
            content=content,
            blueprints=blueprints,
            report=report,
        )
    return SeededContent(questions=ids)


def _seed_question(
    seed: QuestionSeed,
    *,
    subject: Subject,
    college_id: CollegeId,
    author: UserId,
    bank: QuestionBankService,
    assets: AssetReader,
    report: SeedReport,
) -> QuestionId:
    existing = bank.search(
        QuestionQuery(code=seed.code, exact_code=True, owner_id=college_id), limit=1
    )
    if existing.items:
        report.found("questions")
        return existing.items[0].question.id

    question = bank.create_question(
        college_id,
        author,
        subject_id=SubjectId(subject.id),
        code=seed.code,
        text=seed.text,
        max_marks=seed.marks,
        difficulty=seed.difficulty,
        category=seed.topic,
    )
    for answer in seed.answers:
        bank.add_reference_answer(
            college_id,
            author,
            question.id,
            text=answer.text,
            guidance_only=answer.guidance_only,
            synthetic=answer.synthetic,
        )
        report.made("reference answers")
    diagrams: dict[str, ReferenceDiagramId] = {}
    for diagram in seed.diagrams:
        # The picture is cut from a faculty key: no student data (C9).
        stored = bank.upload_reference_diagram(
            college_id,
            author,
            question.id,
            file_name=diagram.file,
            data=assets(diagram.file),
            declared_type="image/png",
            no_student_data=True,
        )
        diagrams[diagram.key] = stored.id
        report.made("reference diagrams")
    if seed.criteria:
        bank.set_rubric(
            college_id,
            author,
            question.id,
            [_spec(criterion, diagrams) for criterion in seed.criteria],
        )
        report.made("criteria", len(seed.criteria))
    if seed.terms:
        bank.set_glossary_terms(college_id, author, question.id, seed.terms)
    report.made("questions")
    return question.id


def _spec(criterion: CriterionSeed, diagrams: Mapping[str, ReferenceDiagramId]) -> CriterionSpec:
    params: CriterionParams
    match criterion.type.value:
        case "list":
            params = ListParams(
                items=tuple(ListItem(term=t.term, synonyms=t.synonyms) for t in criterion.items),
                required_count=criterion.need,
            )
        case "numeric":
            params = NumericParams(
                expected=criterion.expected, tolerance=criterion.tolerance, unit=criterion.unit
            )
        case "semantic":
            params = SemanticParams(reference_statement=criterion.statement)
        case "diagram":
            params = DiagramParams(
                reference_diagram_id=diagrams[criterion.diagram], component=criterion.component
            )
        case other:
            raise SeedError(f"criterion type {other} is not seeded")
    return CriterionSpec(
        label=criterion.label, type=criterion.type, weight=criterion.weight, params=params
    )


def resolve_document(
    paper: PaperSeed, subject_id: SubjectId, question_ids: Mapping[str, QuestionId]
) -> dict[str, object]:
    """The paper's document with the ``@subject`` and ``@CODE`` placeholders replaced."""

    def walk(node: object) -> object:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if node == "@subject":
            return str(subject_id)
        if isinstance(node, str) and node.startswith("@"):
            code = node[1:]
            if code not in question_ids:
                raise SeedError(f"paper {paper.title!r} links unknown question {code!r}")
            return str(question_ids[code])
        return node

    resolved = walk(paper.document)
    if not isinstance(resolved, dict):
        raise SeedError(f"paper {paper.title!r} is not a document")
    return resolved


def _seed_paper(
    paper: PaperSeed,
    *,
    subject: Subject,
    question_ids: Mapping[str, QuestionId],
    college_id: CollegeId,
    author: UserId,
    content: ContentRepository,
    blueprints: BlueprintService,
    report: SeedReport,
) -> None:
    if any(
        b.title == paper.title and b.meta.owning_college_id == college_id for b in blueprints.list()
    ):
        report.found("blueprints")
        return
    document = resolve_document(paper, SubjectId(subject.id), question_ids)
    blueprint = blueprints.create(college_id, author, document)
    check_blueprints_marks(blueprint, content)
    report.made("blueprints")


def check_blueprints_marks(blueprint: ExamBlueprint, content: ContentRepository) -> None:
    """Every leaf is linked and carries the marks of its question (O22/O26)."""
    linked: dict[QuestionId, Question] = {}
    for slot in blueprint.slots():
        for leaf_question_id in _linked(slot):
            linked[leaf_question_id] = content.get(Question, leaf_question_id)
    if not linked:
        raise InvariantError(f"{blueprint.title}: no question is linked")
    check_blueprint_questions(blueprint, linked)


def _linked(slot: Any) -> list[QuestionId]:
    if slot.parts:
        return [p.question_id for p in slot.parts if p.question_id is not None]
    return [] if slot.question_id is None else [slot.question_id]
