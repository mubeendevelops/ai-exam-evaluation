"""SME accounts and their subjects, managed by the college admin: ``/api/v1/accounts``
(D159, D160, D177). The college has one admin account (D161); the Governor is created by a Tarn
operator (``tarn tenants governor``)."""

from uuid import UUID

from fastapi import APIRouter, status

from tarn_api.backends import unit_of_work
from tarn_api.schemas import AccountOut, AccountSubjectsOut, ErrorOut, InviteIn, UserOut
from tarn_api.security import BackendsDep, PrincipalDep, current_user
from tarn_core.domain.identity import IdentityStatus
from tarn_core.domain.personas import Capability
from tarn_core.ids import SubjectId, UserId
from tarn_core.services.auth import AccountView

MANAGE = Capability.MANAGE_SMES

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
        subject_ids=sorted(view.subject_ids, key=str),
    )


@router.get("", response_model=list[AccountOut], summary="Accounts of my college (admin)")
def list_accounts(who: PrincipalDep, backends: BackendsDep) -> list[AccountOut]:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        views = unit.accounts.list_accounts(who.college_id, who.user_id)
    return [_out(v) for v in views]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountOut,
    responses={409: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Create an SME account, grant its subjects and email an invitation (admin)",
)
def invite_sme(body: InviteIn, who: PrincipalDep, backends: BackendsDep) -> AccountOut:
    subjects = [SubjectId(s) for s in dict.fromkeys(body.subject_ids)]
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        unit.entitlements.check_grantable(who.college_id, subjects)
        user = unit.accounts.invite_sme(
            who.college_id, who.user_id, display_name=body.display_name, email=body.email
        )
        granted: frozenset[SubjectId] = frozenset()
        for subject in subjects:
            granted = unit.entitlements.grant(who.college_id, who.user_id, user.id, subject)
    return AccountOut(
        user=UserOut.of(user),
        status=IdentityStatus.PENDING.value,
        locked_until=None,
        force_reset=False,
        subject_ids=sorted(granted, key=str),
    )


def _set_active(who: PrincipalDep, backends: BackendsDep, user_id: UUID, active: bool) -> UserOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        user = unit.accounts.set_active(who.college_id, who.user_id, UserId(user_id), active=active)
    return UserOut.of(user)


@router.post("/{user_id}/disable", response_model=UserOut, summary="Disable an SME (admin)")
def disable(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> UserOut:
    return _set_active(who, backends, user_id, False)


@router.post("/{user_id}/enable", response_model=UserOut, summary="Enable an SME (admin)")
def enable(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> UserOut:
    return _set_active(who, backends, user_id, True)


@router.post(
    "/{user_id}/force-reset",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Require a new password at the next sign-in; ends sessions (admin)",
)
def force_reset(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> None:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        unit.accounts.force_reset(who.college_id, who.user_id, UserId(user_id))


@router.post(
    "/{user_id}/unlock",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a lockout (admin)",
)
def unlock(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> None:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        unit.accounts.unlock(who.college_id, who.user_id, UserId(user_id))


# --- subjects of an SME (D160) -----------------------------------------------------------------


def _subjects_out(user_id: UUID, subjects: frozenset[SubjectId]) -> AccountSubjectsOut:
    return AccountSubjectsOut(user_id=user_id, subject_ids=sorted(subjects, key=str))


@router.get(
    "/{user_id}/subjects",
    response_model=AccountSubjectsOut,
    responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="The subjects of an SME (admin)",
)
def sme_subjects(user_id: UUID, who: PrincipalDep, backends: BackendsDep) -> AccountSubjectsOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        found = unit.entitlements.subjects_of(who.college_id, who.user_id, UserId(user_id))
    return _subjects_out(user_id, found)


@router.post(
    "/{user_id}/subjects/{subject_id}",
    response_model=AccountSubjectsOut,
    responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}},
    summary="Grant a subject to an SME (admin)",
    description="A retired subject cannot be granted (422). Granting a held subject changes "
    "nothing. Audited.",
)
def grant_subject(
    user_id: UUID, subject_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> AccountSubjectsOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        found = unit.entitlements.grant(
            who.college_id, who.user_id, UserId(user_id), SubjectId(subject_id)
        )
    return _subjects_out(user_id, found)


@router.delete(
    "/{user_id}/subjects/{subject_id}",
    response_model=AccountSubjectsOut,
    responses={404: {"model": ErrorOut}, 409: {"model": ErrorOut}},
    summary="Take a subject away from an SME (admin)",
    description="An SME keeps at least one subject (409 for the last one). Audited.",
)
def revoke_subject(
    user_id: UUID, subject_id: UUID, who: PrincipalDep, backends: BackendsDep
) -> AccountSubjectsOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who, MANAGE)
        found = unit.entitlements.revoke(
            who.college_id, who.user_id, UserId(user_id), SubjectId(subject_id)
        )
    return _subjects_out(user_id, found)
