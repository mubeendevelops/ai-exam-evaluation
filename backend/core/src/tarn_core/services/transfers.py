"""Moving units between SMEs (P27, D163): the admin transfers an open unit to another active
SME entitled to its subject, with a reason. The whole unit moves with every booklet's state
(completed stays completed, open stays open, amendment drafts stay open); progress and credit
go to the new owner, and each review keeps the SME who made it. The old SME loses access at
once: their unit is out of their reach (``services.access``) and the locks on its booklets are
released, so a write with the old lock is refused. Nothing is lost: every edit was saved when
it was made. Both SMEs are emailed (no reason, no student data).

Revoking a subject from an SME who owns open units in it transfers each of them in the same
action, or nothing happens (all or nothing). Everything is checked before anything is written,
so a refusal changes nothing even without a database transaction (the in-memory adapters)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.blueprint import ExamBlueprint
from tarn_core.domain.content import Subject
from tarn_core.domain.personas import Capability
from tarn_core.domain.tenancy import User
from tarn_core.domain.unit import (
    REASON_MAX,
    RequestState,
    Unit,
    UnitState,
    UnitTransfer,
    normalise_reason,
)
from tarn_core.errors import IllegalTransitionError, InvariantError, NotFoundError, StaleWriteError
from tarn_core.ids import CollegeId, SubjectId, UnitId, UnitTransferId, UserId
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
from tarn_core.services.entitlements import EntitlementService
from tarn_core.services.units import NOT_ENTITLED, may_own

NOT_OPEN = "Only an open unit can be moved to another SME."
SAME_OWNER = "The unit is with this SME already: choose another SME."
UNITS_LEFT = (
    "Choose a new SME for each open unit this SME has in the subject: "
    "no unit may stay with an SME who is not entitled to its subject."
)


def check_reason(raw: str, what: str = "reason") -> str:
    reason = normalise_reason(raw)
    if not reason:
        raise InvariantError(f"Enter a {what}.")
    if len(reason) > REASON_MAX:
        raise InvariantError(f"The {what} has at most {REASON_MAX} characters.")
    return reason


@dataclass(frozen=True, slots=True, kw_only=True)
class Transfer:
    """A checked transfer, ready to be written."""

    unit: Unit
    source: User
    target: User


class TransferService:
    def __init__(
        self,
        *,
        units: UnitRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        entitlement_service: EntitlementService,
        content: ContentRepository,
        booklets: BookletRepository,
        mailer: Mailer,
        runtime: Runtime,
    ) -> None:
        self._units = units
        self._users = users
        self._entitlements = entitlements
        self._grants = entitlement_service
        self._content = content
        self._booklets = booklets
        self._mailer = mailer
        self._rt = runtime

    # --- transfer one unit ----------------------------------------------------------------

    def transfer(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        unit_id: UnitId,
        *,
        owner_id: UserId,
        reason: str,
        expected_version: int | None = None,
    ) -> Unit:
        """Raises ``NotFoundError`` (unit), ``StaleWriteError`` (the unit changed since it was
        loaded), ``IllegalTransitionError`` (not open) and ``InvariantError`` (blank or long
        reason, a target that is not another active SME entitled to the unit's subject)."""
        require(self._users.get(college_id, actor_id), Capability.REDISTRIBUTE_UNITS)
        reason = check_reason(reason)
        unit = self._units.get(college_id, unit_id)
        if expected_version is not None and unit.version != expected_version:
            raise StaleWriteError("The unit changed since it was loaded: reload it.")
        planned = self._plan(college_id, unit, owner_id, unit.subject_id)
        return self._apply(college_id, actor_id, planned, reason)

    def history(
        self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId
    ) -> Sequence[UnitTransfer]:
        require(self._users.get(college_id, actor_id), Capability.REDISTRIBUTE_UNITS)
        self._units.get(college_id, unit_id)
        return self._units.transfers(college_id, unit_id)

    # --- revoke a subject, moving the SME's open units in it ------------------------------------

    def open_units(
        self, college_id: CollegeId, owner_id: UserId, subject_id: SubjectId | None = None
    ) -> Sequence[Unit]:
        return self._units.list(
            college_id, owner_id=owner_id, subject_id=subject_id, state=UnitState.OPEN
        )

    def revoke_subject(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        user_id: UserId,
        subject_id: SubjectId,
        *,
        transfers: Mapping[UnitId, UserId],
        reason: str,
    ) -> frozenset[SubjectId]:
        """Takes ``subject_id`` from the SME and moves each of their open units in it to the
        SME named in ``transfers``. ``transfers`` must name exactly those units
        (``IllegalTransitionError`` otherwise); any refusal changes nothing."""
        self._grants.check_revocable(college_id, actor_id, user_id, subject_id)
        open_units = {u.id: u for u in self.open_units(college_id, user_id, subject_id)}
        if set(transfers) != set(open_units):
            raise IllegalTransitionError(UNITS_LEFT)
        plans = []
        if open_units:
            reason = check_reason(reason)
            plans = [
                self._plan(college_id, open_units[uid], target, subject_id)
                for uid, target in transfers.items()
            ]
        for planned in plans:
            self._apply(college_id, actor_id, planned, reason)
        return self._grants.revoke(college_id, actor_id, user_id, subject_id)

    # --- the parts ----------------------------------------------------------------------------

    def _plan(
        self, college_id: CollegeId, unit: Unit, owner_id: UserId, subject_id: SubjectId
    ) -> Transfer:
        if unit.state is not UnitState.OPEN:
            raise IllegalTransitionError(NOT_OPEN)
        if owner_id == unit.owner_id:
            raise InvariantError(SAME_OWNER)
        try:
            target = self._users.get(college_id, owner_id)
        except NotFoundError:
            raise InvariantError(NOT_ENTITLED) from None
        if not may_own(self._entitlements, target, subject_id):
            raise InvariantError(NOT_ENTITLED)
        source = self._users.get(college_id, unit.owner_id)
        return Transfer(unit=unit, source=source, target=target)

    def _apply(
        self, college_id: CollegeId, actor_id: UserId, planned: Transfer, reason: str
    ) -> Unit:
        unit, source, target = planned.unit, planned.source, planned.target
        moved = replace(unit, owner_id=target.id, version=unit.version + 1)
        self._units.save(college_id, moved)
        # The old SME's open booklets close now: a write with their lock is refused.
        released = 0
        for booklet in self._booklets.in_unit(college_id, unit.id):
            if self._booklets.lock(college_id, booklet.id) is not None:
                self._booklets.delete_lock(college_id, booklet.id)
                released += 1
        now = self._rt.clock.now()
        request = self._units.pending_request(college_id, unit.id)
        if request is not None:
            self._units.save_request(
                college_id,
                replace(request, state=RequestState.DONE, closed_by=actor_id, closed_at=now),
            )
        self._units.save_transfer(
            college_id,
            UnitTransfer(
                id=self._rt.new_id(UnitTransferId),
                college_id=college_id,
                unit_id=unit.id,
                from_owner=source.id,
                to_owner=target.id,
                reason=reason,
                by=actor_id,
                at=now,
                request_id=None if request is None else request.id,
            ),
        )
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.UNIT_TRANSFERRED,
            before={"unit_id": str(unit.id), "owner_id": str(source.id), "version": unit.version},
            after={
                "unit_id": str(unit.id),
                "owner_id": str(target.id),
                "version": moved.version,
                "reason": reason,
                "request_id": None if request is None else str(request.id),
                "locks_released": released,
            },
        )
        paper = self._content.get(college_id, ExamBlueprint, unit.paper_id)
        subject = self._content.get(college_id, Subject, unit.subject_id)
        booklets = len(self._booklets.in_unit(college_id, unit.id))
        self._mailer.send(received_email(moved, target, paper, subject, booklets))
        self._mailer.send(moved_away_email(moved, source, paper, subject))
        return moved


def _unit_lines(unit: Unit, paper: ExamBlueprint, subject: Subject) -> str:
    return (
        f"Paper: {paper.title} ({paper.exam_code})\n"
        f"Subject: {subject.name} ({subject.code})\n"
        f"Student year: {unit.year}\n"
        f"Due date: {unit.due_date.isoformat()}\n"
    )


def received_email(
    unit: Unit, owner: User, paper: ExamBlueprint, subject: Subject, booklets: int
) -> EmailMessage:
    """The new owner's notice. No reason (it may be personal) and no student data."""
    return EmailMessage(
        to=owner.email,
        subject=f"Tarn: unit {unit.code} transferred to you",
        text=(
            f"Hello {owner.display_name},\n\n"
            f"Your admin has transferred the unit {unit.code} to you, with its {booklets} "
            "booklet(s) as they stand: booklets already completed stay completed.\n\n"
            f"{_unit_lines(unit, paper, subject)}\n"
            "Sign in to Tarn AI Evaluation to continue the marking."
        ),
    )


def moved_away_email(
    unit: Unit, former: User, paper: ExamBlueprint, subject: Subject
) -> EmailMessage:
    """The former owner's notice: the unit and its booklets are no longer theirs to open."""
    return EmailMessage(
        to=former.email,
        subject=f"Tarn: unit {unit.code} moved to another SME",
        text=(
            f"Hello {former.display_name},\n\n"
            f"Your admin has moved the unit {unit.code} to another SME. "
            "Everything you saved stays with the unit; you no longer have access to it.\n\n"
            f"{_unit_lines(unit, paper, subject)}"
        ),
    )


__all__ = [
    "NOT_OPEN",
    "SAME_OWNER",
    "UNITS_LEFT",
    "TransferService",
    "check_reason",
    "moved_away_email",
    "received_email",
]
