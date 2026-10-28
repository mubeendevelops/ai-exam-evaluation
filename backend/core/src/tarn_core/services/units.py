"""Units of work (P26, D162): the admin creates a unit from a paper, a student year, an SME
entitled to the paper's subject and a due date; the SME is emailed. The admin sees every unit
and its booklets' progress (never marks, D167); an SME sees the units they own.

College data: every call takes the college first, and another college's unit is not found."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.booklet import Booklet
from tarn_core.domain.content import Subject
from tarn_core.domain.personas import Capability, can
from tarn_core.domain.tenancy import Role, User
from tarn_core.domain.unit import YEAR_MAX, Unit, UnitState, normalise_year, unit_code
from tarn_core.errors import AlreadyExistsError, InvariantError, NotFoundError
from tarn_core.ids import BlueprintId, CollegeId, SubjectId, UnitId, UserId
from tarn_core.ports.identity import EmailMessage, Mailer
from tarn_core.ports.repositories import (
    BookletRepository,
    ContentRepository,
    EntitlementRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.access import require

NOT_ENTITLED = "Choose an active SME who has this paper's subject."
GOVERNOR_STATES = frozenset({UnitState.SUBMITTED, UnitState.FROZEN})
"""What the Governor sees of the units (P28): the ones waiting for, or past, the freeze."""


def may_own(entitlements: EntitlementRepository, user: User, subject_id: SubjectId) -> bool:
    """An active SME entitled to the subject may own (be assigned or sent) its units (D160)."""
    return (
        user.role is Role.SME
        and user.active
        and subject_id in entitlements.subjects_of(user.college_id, user.id)
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperChoice:
    """A paper a unit can be made from: every slot linked (D56), an exam code, a live
    subject."""

    paper: ExamBlueprint
    subject: Subject


def paper_problem(paper: ExamBlueprint, subject: Subject) -> str | None:
    """Why a unit cannot use this paper, or None."""
    if paper.unlinked_leaves():
        return "Link every question of the paper before making a unit of it."
    if not paper.exam_code:
        return "Give the paper an exam code in the Schema Designer first."
    if subject.retired:
        return f"The subject {subject.code} is retired."
    return None


class UnitService:
    def __init__(
        self,
        *,
        units: UnitRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        content: ContentRepository,
        booklets: BookletRepository,
        mailer: Mailer,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._users = users
        self._entitlements = entitlements
        self._content = content
        self._booklets = booklets
        self._mailer = mailer
        self._rt = runtime

    # --- what the admin picks from ------------------------------------------------------------

    def papers(
        self, college_id: CollegeId, actor_id: UserId, subject_id: SubjectId | None = None
    ) -> Sequence[PaperChoice]:
        """The papers a unit can be made from, by title."""
        require(self._users.get(college_id, actor_id), Capability.ASSIGN_UNITS)
        subjects = {s.id: s for s in self._content.latest(college_id, Subject)}
        out = []
        for paper in self._content.latest(college_id, ExamBlueprint):
            subject = subjects.get(paper.subject_id)
            if subject is None or (subject_id is not None and paper.subject_id != subject_id):
                continue
            if paper_problem(paper, subject) is None:
                out.append(PaperChoice(paper=paper, subject=subject))
        return sorted(out, key=lambda c: (c.paper.title.casefold(), c.paper.exam_code))

    def entitled_smes(
        self, college_id: CollegeId, actor_id: UserId, subject_id: SubjectId
    ) -> Sequence[User]:
        """The SMEs a unit of ``subject_id`` may be assigned to (D160), by name."""
        require(self._users.get(college_id, actor_id), Capability.ASSIGN_UNITS)
        return sorted(
            (u for u in self._users.list(college_id) if self._may_own(u, subject_id)),
            key=lambda u: (u.display_name.casefold(), str(u.id)),
        )

    def _may_own(self, user: User, subject_id: SubjectId) -> bool:
        return may_own(self._entitlements, user, subject_id)

    # --- create --------------------------------------------------------------------------------

    def create(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        paper_id: BlueprintId,
        year: str,
        owner_id: UserId,
        due_date: date,
    ) -> Unit:
        """Raises ``NotFoundError`` (paper or SME not in the college), ``InvariantError``
        (paper not usable, year blank or long, due date past, SME not entitled) and
        ``AlreadyExistsError`` (a unit of this paper and year exists)."""
        require(self._users.get(college_id, actor_id), Capability.ASSIGN_UNITS)
        paper = self._content.get(college_id, ExamBlueprint, paper_id)
        subject = self._content.get(college_id, Subject, paper.subject_id)
        problem = paper_problem(paper, subject)
        if problem:
            raise InvariantError(problem)
        year = normalise_year(year)
        if not year or len(year) > YEAR_MAX:
            raise InvariantError(f"Enter the student year (at most {YEAR_MAX} characters).")
        today = self._rt.clock.now().date()
        if due_date < today:
            raise InvariantError("The due date is in the past.")
        try:
            owner = self._users.get(college_id, owner_id)
        except NotFoundError:
            raise InvariantError(NOT_ENTITLED) from None
        if not self._may_own(owner, subject.id):
            raise InvariantError(NOT_ENTITLED)
        if self._units.find(college_id, paper.id, year) is not None:
            raise AlreadyExistsError("A unit for this paper and year exists already.")
        unit = Unit(
            id=self._rt.new_id(UnitId),
            college_id=college_id,
            code=unit_code(self._units.count(college_id) + 1),
            paper_id=paper.id,
            subject_id=subject.id,
            year=year,
            owner_id=owner.id,
            due_date=due_date,
            created_by=actor_id,
            created_at=self._rt.clock.now(),
        )
        self._units.save(college_id, unit)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.UNIT_CREATED,
            after={
                "unit_id": str(unit.id),
                "code": unit.code,
                "paper_id": str(paper.id),
                "subject_id": str(subject.id),
                "year": unit.year,
                "owner_id": str(owner.id),
                "due_date": unit.due_date.isoformat(),
            },
        )
        self._mailer.send(assignment_email(unit, owner, paper, subject))
        return unit

    # --- read ----------------------------------------------------------------------------------

    def list(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        *,
        subject_id: SubjectId | None = None,
        owner_id: UserId | None = None,
        state: UnitState | None = None,
    ) -> Sequence[Unit]:
        """The admin: every unit of the college; an SME: their own (``owner_id`` is then
        ignored); the Governor: the submitted and frozen units (P28). Newest first."""
        user = self._users.get(college_id, actor_id)
        if can(user, Capability.FREEZE_UNITS):
            units = [
                u
                for u in self._units.list(
                    college_id, subject_id=subject_id, owner_id=owner_id, state=state
                )
                if u.state in GOVERNOR_STATES
            ]
        else:
            require(user, Capability.VIEW_PROGRESS)
            if not can(user, Capability.ASSIGN_UNITS):
                owner_id = user.id
            units = list(
                self._units.list(college_id, subject_id=subject_id, owner_id=owner_id, state=state)
            )
        return sorted(units, key=lambda u: (u.created_at, u.code), reverse=True)

    def get(self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId) -> Unit:
        """Another SME's unit is not found, like another college's; for the Governor, a unit
        that is not submitted or frozen."""
        user = self._users.get(college_id, actor_id)
        unit = self._units.get(college_id, unit_id)
        if can(user, Capability.FREEZE_UNITS):
            if unit.state not in GOVERNOR_STATES:
                raise NotFoundError(f"unit {unit_id}")
            return unit
        require(user, Capability.VIEW_PROGRESS)
        if not can(user, Capability.ASSIGN_UNITS) and unit.owner_id != user.id:
            raise NotFoundError(f"unit {unit_id}")
        return unit

    def booklets(
        self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId
    ) -> Sequence[Booklet]:
        """The unit's booklets, oldest first."""
        unit = self.get(college_id, actor_id, unit_id)
        return self._booklets.in_unit(college_id, unit.id)


def assignment_email(
    unit: Unit, owner: User, paper: ExamBlueprint, subject: Subject
) -> EmailMessage:
    """The SME's notice: unit code, paper title, subject, due date. No student data."""
    return EmailMessage(
        to=owner.email,
        subject=f"Tarn: unit {unit.code} assigned to you",
        text=(
            f"Hello {owner.display_name},\n\n"
            f"The unit {unit.code} has been assigned to you for marking.\n\n"
            f"Paper: {paper.title} ({paper.exam_code})\n"
            f"Subject: {subject.name} ({subject.code})\n"
            f"Student year: {unit.year}\n"
            f"Due date: {unit.due_date.isoformat()}\n\n"
            "Sign in to Tarn AI Evaluation to see its booklets as they are uploaded."
        ),
    )
