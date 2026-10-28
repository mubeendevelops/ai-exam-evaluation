"""The delegation inbox (P27, D163): the admin lists every request and declines with a note
(a transfer of the unit closes a request as done: ``POST /units/{id}/transfer``); an SME lists
their own requests and withdraws a pending one."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from tarn_api.backends import unit_of_work
from tarn_api.routes.units import delegation_out
from tarn_api.schemas import DeclineIn, DelegationRequestOut, DelegationStateName, ErrorOut
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.personas import Capability
from tarn_core.domain.unit import RequestState
from tarn_core.ids import DelegationRequestId

router = APIRouter(
    prefix="/api/v1/delegation-requests",
    tags=["units"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)


@router.get(
    "",
    response_model=list[DelegationRequestOut],
    summary="Delegation requests, newest first: every one for the admin, your own for an SME",
)
def list_requests(
    who: PrincipalDep,
    backends: BackendsDep,
    state: Annotated[DelegationStateName | None, Query()] = None,
) -> list[DelegationRequestOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.REDISTRIBUTE_UNITS, Capability.REQUEST_REASSIGNMENT)
        found = unit.delegation.list(
            who.college_id, who.user_id, state=None if state is None else RequestState(state)
        )
        return [delegation_out(unit, who.college_id, r) for r in found]


@router.post(
    "/{request_id}/decline",
    response_model=DelegationRequestOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Keep the unit with its SME and say why (admin)",
)
def decline(
    request_id: UUID, body: DeclineIn, who: PrincipalDep, backends: BackendsDep
) -> DelegationRequestOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.REDISTRIBUTE_UNITS)
        closed = unit.delegation.decline(
            who.college_id, who.user_id, DelegationRequestId(request_id), note=body.note
        )
        return delegation_out(unit, who.college_id, closed)


@router.post(
    "/{request_id}/withdraw",
    response_model=DelegationRequestOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
    summary="Take back your own pending request (SME)",
)
def withdraw(request_id: UUID, who: PrincipalDep, backends: BackendsDep) -> DelegationRequestOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Capability.REQUEST_REASSIGNMENT)
        closed = unit.delegation.withdraw(
            who.college_id, who.user_id, DelegationRequestId(request_id)
        )
        return delegation_out(unit, who.college_id, closed)
