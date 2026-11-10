"""The delegation inbox (P27, D163): an SME asks the admin to move one of their open units to
someone else, with a reason; one pending request per unit. The admin transfers the unit (the
request closes as done, ``TransferService``) or declines it with a note; the SME may withdraw a
pending request (user, 2026-10-28). The admin sees every request; an SME sees their own.

The SME may suggest a replacement (P32, D218) among the active SMEs entitled to the unit's
subject; the admin still decides, and a suggestion that can no longer take the unit is shown as
unavailable. The audit records who was suggested and how long the reason was, never its text
(D230)."""

from collections.abc import Sequence
from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.common import JsonValue
from tarn_core.domain.personas import Capability, can
from tarn_core.domain.tenancy import User
from tarn_core.domain.unit import DelegationRequest, RequestState, Unit
from tarn_core.errors import (
    AlreadyExistsError,
    IllegalTransitionError,
    InvariantError,
    NotFoundError,
    UnitClosedError,
)
from tarn_core.ids import CollegeId, DelegationRequestId, UnitId, UserId
from tarn_core.ports.repositories import (
    EntitlementRepository,
    NotificationRepository,
    UnitRepository,
    UserRepository,
)
from tarn_core.services._support import Runtime
from tarn_core.services.access import require
from tarn_core.services.notifications import Notices
from tarn_core.services.transfers import check_reason
from tarn_core.services.unit_guard import closed_message
from tarn_core.services.units import may_own

WAITING = "A request to move this unit is waiting for your admin already."
CLOSED = "This request has been answered already."
NOT_A_CANDIDATE = "The suggested SME cannot take this unit: choose an active SME with its subject."


class DelegationService:
    def __init__(
        self,
        *,
        units: UnitRepository,
        users: UserRepository,
        entitlements: EntitlementRepository,
        runtime: Runtime,
        notifications: NotificationRepository,
    ) -> None:
        self._units = units
        self._users = users
        self._entitlements = entitlements
        self._rt = runtime
        self._notices = Notices(notifications, runtime)

    def request(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        unit_id: UnitId,
        *,
        reason: str,
        suggested_to: UserId | None = None,
    ) -> DelegationRequest:
        """Another SME's unit is not found. Raises ``UnitClosedError`` for a unit that
        is not open, ``AlreadyExistsError`` while a request for it waits and
        ``InvariantError`` for a suggestion that is not one of ``candidates``."""
        unit = self._own_open_unit(college_id, actor_id, unit_id)
        reason = check_reason(reason)
        if suggested_to is not None and suggested_to not in {
            u.id for u in self._candidates(college_id, unit)
        }:
            raise InvariantError(NOT_A_CANDIDATE)
        if self._units.pending_request(college_id, unit.id) is not None:
            raise AlreadyExistsError(WAITING)
        made = DelegationRequest(
            id=self._rt.new_id(DelegationRequestId),
            college_id=college_id,
            unit_id=unit.id,
            requested_by=actor_id,
            reason=reason,
            requested_at=self._rt.clock.now(),
            suggested_to=suggested_to,
        )
        self._units.save_request(college_id, made)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.DELEGATION_REQUESTED,
            after={
                "request_id": str(made.id),
                "unit_id": str(unit.id),
                "suggested_to": None if suggested_to is None else str(suggested_to),
                "reason_length": len(reason),
            },
        )
        return made

    def candidates(self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId) -> list[User]:
        """Who the owner of an open unit may suggest: the other active SMEs entitled to its
        subject, by name."""
        return self._candidates(college_id, self._own_open_unit(college_id, actor_id, unit_id))

    def available(self, college_id: CollegeId, request: DelegationRequest) -> bool:
        """Whether the suggested SME could still be sent the unit: active, entitled to its
        subject and not its owner now."""
        if request.suggested_to is None:
            return False
        unit = self._units.get(college_id, request.unit_id)
        if request.suggested_to == unit.owner_id:
            return False
        try:
            user = self._users.get(college_id, request.suggested_to)
        except NotFoundError:
            return False
        return may_own(self._entitlements, user, unit.subject_id)

    def _own_open_unit(self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId) -> Unit:
        require(self._users.get(college_id, actor_id), Capability.REQUEST_REASSIGNMENT)
        unit = self._units.get(college_id, unit_id)
        if unit.owner_id != actor_id:
            raise NotFoundError(f"unit {unit_id}")
        if not unit.open:
            raise UnitClosedError(closed_message(unit))  # submitted or frozen (P28)
        return unit

    def _candidates(self, college_id: CollegeId, unit: Unit) -> list[User]:
        return sorted(
            (
                u
                for u in self._users.list(college_id)
                if u.id != unit.owner_id and may_own(self._entitlements, u, unit.subject_id)
            ),
            key=lambda u: (u.display_name.casefold(), str(u.id)),
        )

    def withdraw(
        self, college_id: CollegeId, actor_id: UserId, request_id: DelegationRequestId
    ) -> DelegationRequest:
        """The SME takes back their own pending request; anyone else's is not found."""
        require(self._users.get(college_id, actor_id), Capability.REQUEST_REASSIGNMENT)
        found = self._units.get_request(college_id, request_id)
        if found.requested_by != actor_id:
            raise NotFoundError(f"request {request_id}")
        return self._close(college_id, actor_id, found, RequestState.WITHDRAWN, "")

    def decline(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        request_id: DelegationRequestId,
        *,
        note: str,
    ) -> DelegationRequest:
        """The admin keeps the unit with its SME and says why."""
        require(self._users.get(college_id, actor_id), Capability.REDISTRIBUTE_UNITS)
        found = self._units.get_request(college_id, request_id)
        note = check_reason(note, "note")
        return self._close(college_id, actor_id, found, RequestState.DECLINED, note)

    def list(
        self, college_id: CollegeId, actor_id: UserId, *, state: RequestState | None = None
    ) -> Sequence[DelegationRequest]:
        """The admin: every request; an SME: their own. Newest first."""
        user = self._users.get(college_id, actor_id)
        if can(user, Capability.REDISTRIBUTE_UNITS):
            found = self._units.requests(college_id, state=state)
        else:
            require(user, Capability.REQUEST_REASSIGNMENT)
            found = self._units.requests(college_id, requested_by=user.id, state=state)
        return sorted(found, key=lambda r: (r.requested_at, str(r.id)), reverse=True)

    def _close(
        self,
        college_id: CollegeId,
        actor_id: UserId,
        found: DelegationRequest,
        state: RequestState,
        note: str,
    ) -> DelegationRequest:
        if not found.pending:
            raise IllegalTransitionError(CLOSED)
        closed = replace(
            found, state=state, closed_by=actor_id, closed_at=self._rt.clock.now(), note=note
        )
        self._units.save_request(college_id, closed)
        after: dict[str, JsonValue] = {"request_id": str(found.id), "unit_id": str(found.unit_id)}
        if note:
            after["note"] = note
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.DELEGATION_DECLINED
            if state is RequestState.DECLINED
            else AuditAction.DELEGATION_WITHDRAWN,
            before={"request_id": str(found.id), "state": found.state.value},
            after=after | {"state": state.value},
        )
        if state is RequestState.DECLINED:
            self._notices.declined(self._units.get(college_id, found.unit_id), closed)
        return closed
