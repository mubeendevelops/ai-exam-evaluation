"""The delegation inbox (P27, D163): an SME asks the admin to move one of their open units to
someone else, with a reason; one pending request per unit. The admin transfers the unit (the
request closes as done, ``TransferService``) or declines it with a note; the SME may withdraw a
pending request (user, 2026-10-28). The admin sees every request; an SME sees their own."""

from collections.abc import Sequence
from dataclasses import replace

from tarn_core.domain.audit import AuditAction
from tarn_core.domain.common import JsonValue
from tarn_core.domain.personas import Capability, can
from tarn_core.domain.unit import DelegationRequest, RequestState, UnitState
from tarn_core.errors import AlreadyExistsError, IllegalTransitionError, NotFoundError
from tarn_core.ids import CollegeId, DelegationRequestId, UnitId, UserId
from tarn_core.ports.repositories import UnitRepository, UserRepository
from tarn_core.services._support import Runtime
from tarn_core.services.access import require
from tarn_core.services.transfers import check_reason

WAITING = "A request to move this unit is waiting for your admin already."
CLOSED = "This request has been answered already."


class DelegationService:
    def __init__(self, *, units: UnitRepository, users: UserRepository, runtime: Runtime) -> None:
        self._units = units
        self._users = users
        self._rt = runtime

    def request(
        self, college_id: CollegeId, actor_id: UserId, unit_id: UnitId, *, reason: str
    ) -> DelegationRequest:
        """Another SME's unit is not found. Raises ``IllegalTransitionError`` for a unit that
        is not open, ``AlreadyExistsError`` while a request for it waits."""
        require(self._users.get(college_id, actor_id), Capability.REQUEST_REASSIGNMENT)
        unit = self._units.get(college_id, unit_id)
        if unit.owner_id != actor_id:
            raise NotFoundError(f"unit {unit_id}")
        if unit.state is not UnitState.OPEN:
            raise IllegalTransitionError("Only an open unit can be moved to another SME.")
        reason = check_reason(reason)
        if self._units.pending_request(college_id, unit.id) is not None:
            raise AlreadyExistsError(WAITING)
        made = DelegationRequest(
            id=self._rt.new_id(DelegationRequestId),
            college_id=college_id,
            unit_id=unit.id,
            requested_by=actor_id,
            reason=reason,
            requested_at=self._rt.clock.now(),
        )
        self._units.save_request(college_id, made)
        self._rt.record(
            college_id,
            actor_id,
            AuditAction.DELEGATION_REQUESTED,
            after={"request_id": str(made.id), "unit_id": str(unit.id), "reason": reason},
        )
        return made

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
        return closed
