"""Teacher accounts, managed by the college admin: ``/api/v1/accounts``."""

from uuid import UUID

from fastapi import APIRouter, status

from tarn_api.backends import unit_of_work
from tarn_api.schemas import AccountOut, ErrorOut, InviteIn, UserOut
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.identity import IdentityStatus
from tarn_core.domain.tenancy import Role
from tarn_core.ids import UserId
from tarn_core.services.auth import AccountView

router = APIRouter(
    prefix="/api/v1/accounts",
    tags=["accounts"],
    responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
)


def _out(view: AccountView) -> AccountOut:
    return AccountOut(
        user=UserOut.of(view.user),
        status=view.status.value,
        locked_until=view.locked_until,
        force_reset=view.force_reset,
    )


@router.get("", response_model=list[AccountOut], summary="Accounts of my college (admin)")
def list_accounts(who: PrincipalDep, backends: BackendsDep) -> list[AccountOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN)
        views = unit.accounts.list_accounts(who.college_id, who.user_id)
    return [_out(v) for v in views]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountOut,
    responses={409: {"model": ErrorOut}},
    summary="Create a teacher account and email an invitation (admin)",
)
def invite_teacher(body: InviteIn, who: PrincipalDep, backends: BackendsDep) -> AccountOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN)
        user = unit.accounts.invite_teacher(
            who.college_id, who.user_id, display_name=body.display_name, email=body.email
        )
    return AccountOut(
        user=UserOut.of(user),
        status=IdentityStatus.PENDING.value,
        locked_until=None,
        force_reset=False,
    )


def _set_active(who: PrincipalDep, backends: BackendsDep, user_id: UUID, active: bool) -> UserOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN)
        user = unit.accounts.set_active(who.college_id, who.user_id, UserId(user_id), active=active)
    return UserOut.of(user)


@router.post("/{user_id}/disable", response_model=UserOut, summary="Disable a teacher (admin)")
def disable(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> UserOut:
    return _set_active(who, backends, user_id, False)


@router.post("/{user_id}/enable", response_model=UserOut, summary="Enable a teacher (admin)")
def enable(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> UserOut:
    return _set_active(who, backends, user_id, True)


@router.post(
    "/{user_id}/force-reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Require a new password at the next sign-in; ends sessions (admin)",
)
def force_reset(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> None:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN)
        unit.accounts.force_reset(who.college_id, who.user_id, UserId(user_id))


@router.post(
    "/{user_id}/unlock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a lockout (admin)",
)
def unlock(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> None:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, Role.ADMIN)
        unit.accounts.unlock(who.college_id, who.user_id, UserId(user_id))
