"""Tenant registration, as in MainLogin.html "Tenant Registration": ``/api/v1/registrations``.
Approval by a Tarn operator happens in the CLI (``tarn tenants approve``), not over HTTP."""

from fastapi import APIRouter, Depends, Query, status

from tarn_api.backends import unit_of_work
from tarn_api.ratelimit import AVAILABILITY_IP, REGISTER_IP, TOKEN_IP, per_ip
from tarn_api.schemas import (
    AvailabilityOut,
    ErrorOut,
    PolicyErrorOut,
    RegistrationIn,
    RegistrationOut,
    TenantStatusOut,
    VerifyEmailIn,
)
from tarn_api.security import BackendsDep
from tarn_core.ids import CollegeId
from tarn_core.services.passwords import token_college
from tarn_core.services.registration import check_availability

router = APIRouter(prefix="/api/v1/registrations", tags=["registration"])


@router.get(
    "/availability",
    response_model=AvailabilityOut,
    responses={429: {"model": ErrorOut}},
    summary="Is this Institution ID well formed and free?",
    dependencies=[Depends(per_ip(AVAILABILITY_IP))],
)
def availability(
    backends: BackendsDep, institution_id: str = Query(max_length=64)
) -> AvailabilityOut:
    with backends.identity() as store:
        result = check_availability(store, institution_id)
    return AvailabilityOut(
        institution_id=result.institution_id,
        valid=result.valid,
        available=result.available,
        problem=result.problem,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RegistrationOut,
    responses={
        409: {"model": ErrorOut, "description": "The Institution ID is taken."},
        422: {"model": PolicyErrorOut},
        429: {"model": ErrorOut},
    },
    summary="Register a college and its first admin (both pending until verified/approved)",
    dependencies=[Depends(per_ip(REGISTER_IP))],
)
def register(body: RegistrationIn, backends: BackendsDep) -> RegistrationOut:
    college_id = CollegeId(backends.ids.new())
    with unit_of_work(backends, college_id) as unit:
        tenant = unit.registration.register(
            college_id,
            institution_id=body.institution_id,
            institution_name=body.institution_name,
            admin_name=body.admin_name,
            email=body.email,
            password=body.password,
        )
    return RegistrationOut(
        institution_id=tenant.institution_id,
        status=tenant.status.value,
        approval_required=tenant.approval_required,
    )


@router.post(
    "/verify-email",
    response_model=TenantStatusOut,
    responses={400: {"model": ErrorOut}, 429: {"model": ErrorOut}},
    summary="Verify the admin's email with the emailed link",
    dependencies=[Depends(per_ip(TOKEN_IP))],
)
def verify_email(body: VerifyEmailIn, backends: BackendsDep) -> TenantStatusOut:
    college_id = token_college(body.token)
    with unit_of_work(backends, college_id) as unit:
        tenant = unit.registration.verify_email(college_id, body.token)
    return TenantStatusOut(institution_id=tenant.institution_id, status=tenant.status.value)
