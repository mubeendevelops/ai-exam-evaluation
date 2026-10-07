"""Sign-in, sessions, passwords and recovery codes: ``/api/v1/auth``."""

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from tarn_adapters.auth.passwords import bundled_bloom_bytes
from tarn_api.backends import Backends, unit_of_work
from tarn_api.ratelimit import (
    FORGOT_ACCOUNT,
    FORGOT_IP,
    LOGIN_ACCOUNT,
    LOGIN_IP,
    RECOVER_ACCOUNT,
    RECOVER_IP,
    TOKEN_IP,
    account_key,
    check,
    ip_of,
    per_ip,
)
from tarn_api.schemas import (
    ChangePasswordIn,
    CurrentPasswordIn,
    ErrorOut,
    ForgotIn,
    InviteAcceptIn,
    LoginIn,
    MeOut,
    PolicyErrorOut,
    RecoverIn,
    RecoveryCodesOut,
    ResetIn,
    ResetRequiredOut,
    TokenOut,
    UserOut,
)
from tarn_api.security import (
    BackendsDep,
    PrincipalDep,
    clear_refresh_cookie,
    current_user,
    issue_access_token,
    set_refresh_cookie,
)
from tarn_core.domain.identity import AuthPolicy, TenantRecord
from tarn_core.domain.tenancy import normalise_institution_id
from tarn_core.errors import TokenError
from tarn_core.ids import AuthSessionId
from tarn_core.services.auth import Refreshed, ResetRequired, SignedIn
from tarn_core.services.passwords import token_college, token_session

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SIGN_IN_FAILED = (
    "Sign-in failed. Check the Institution ID, email and password. After several failed "
    "attempts the account is locked for a few minutes; you can also reset your password."
)
_401: dict[int | str, dict[str, Any]] = {401: {"model": ErrorOut}}
_429: dict[int | str, dict[str, Any]] = {
    429: {"model": ErrorOut, "description": "Too many attempts from this address or account."}
}
_422: dict[int | str, dict[str, Any]] = {
    422: {"model": PolicyErrorOut, "description": "The new password breaks the policy."}
}


def find_tenant(backends: Backends, institution_id: str) -> TenantRecord | None:
    with backends.identity() as store:
        return store.find_tenant(normalise_institution_id(institution_id))


@router.post(
    "/login",
    response_model=TokenOut | ResetRequiredOut,
    responses={**_401, **_429},
    summary="Sign in with Institution ID, email and password",
)
def login(
    body: LoginIn, request: Request, response: Response, backends: BackendsDep
) -> TokenOut | ResetRequiredOut:
    check(
        request,
        (LOGIN_IP, ip_of(request)),
        (LOGIN_ACCOUNT, account_key(body.institution_id, body.email)),
    )
    tenant = find_tenant(backends, body.institution_id)
    if tenant is None:
        # As much work as a real attempt, so unknown institutions are not revealed by timing.
        backends.kit.hasher.hash(body.password, AuthPolicy().hash_params)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=SIGN_IN_FAILED)
    with unit_of_work(backends, tenant.college_id) as unit:
        outcome = unit.auth.login(
            tenant.college_id, body.email, body.password, remember=body.remember
        )
    if isinstance(outcome, ResetRequired):
        return ResetRequiredOut(reset_token=outcome.reset_token)
    if not isinstance(outcome, SignedIn):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=SIGN_IN_FAILED)
    token, expires_in = issue_access_token(backends, outcome.user, outcome.session)
    set_refresh_cookie(response, backends, outcome.refresh_token, outcome.session)
    return TokenOut(access_token=token, expires_in=expires_in, user=UserOut.of(outcome.user))


@router.post(
    "/refresh",
    response_model=TokenOut,
    responses=_401,
    summary="New access token from the refresh cookie (rotates the cookie)",
)
def refresh(
    response: Response,
    backends: BackendsDep,
    tarn_refresh: Annotated[str | None, Cookie(include_in_schema=False)] = None,
) -> TokenOut | JSONResponse:
    def refused() -> JSONResponse:
        answer = JSONResponse({"detail": "Sign in again."}, status.HTTP_401_UNAUTHORIZED)
        clear_refresh_cookie(answer, backends)
        return answer

    if not tarn_refresh:
        return refused()
    try:
        college_id = token_college(tarn_refresh)
        session_id = AuthSessionId(token_session(tarn_refresh))
    except TokenError:
        return refused()
    with unit_of_work(backends, college_id) as unit:
        outcome = unit.auth.refresh(college_id, session_id, tarn_refresh)
    if not isinstance(outcome, Refreshed):
        return refused()
    token, expires_in = issue_access_token(backends, outcome.user, outcome.session)
    set_refresh_cookie(response, backends, outcome.refresh_token, outcome.session)
    return TokenOut(access_token=token, expires_in=expires_in, user=UserOut.of(outcome.user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, responses=_401)
def logout(who: PrincipalDep, response: Response, backends: BackendsDep) -> None:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who)
        unit.auth.logout(who.college_id, who.user_id, who.session_id)
    clear_refresh_cookie(response, backends)


@router.get("/me", response_model=MeOut, responses=_401)
def me(who: PrincipalDep, backends: BackendsDep) -> MeOut:
    with unit_of_work(backends, who.college_id) as unit:
        user = current_user(unit, who)
        tenant = unit.identity.get_tenant(who.college_id)
        left = unit.auth.recovery_codes_left(who.college_id, who.user_id)
    return MeOut(
        user=UserOut.of(user),
        institution_id=tenant.institution_id,
        college_name=tenant.name,
        recovery_codes_left=left,
    )


# --- forgot password, reset link, recovery codes ----------------------------------------------


@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    responses=_429,
    summary="Email a single-use reset link (30 minutes); never says whether the email exists",
)
def forgot_password(body: ForgotIn, request: Request, backends: BackendsDep) -> None:
    check(
        request,
        (FORGOT_IP, ip_of(request)),
        (FORGOT_ACCOUNT, account_key(body.institution_id, body.email)),
    )
    tenant = find_tenant(backends, body.institution_id)
    if tenant is None:
        return
    with unit_of_work(backends, tenant.college_id) as unit:
        unit.auth.request_reset(tenant.college_id, body.email)


@router.post(
    "/password/reset",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={400: {"model": ErrorOut}, **_422, **_429},
    summary="Set a new password with a reset link token (single use)",
    dependencies=[Depends(per_ip(TOKEN_IP))],
)
def reset_password(body: ResetIn, backends: BackendsDep) -> None:
    college_id = token_college(body.token)
    with unit_of_work(backends, college_id) as unit:
        unit.auth.reset_password(college_id, body.token, body.new_password)


@router.post(
    "/password/recover",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_401, **_422, **_429},
    summary="Set a new password with a one-time recovery code",
)
def recover_password(body: RecoverIn, request: Request, backends: BackendsDep) -> None:
    check(
        request,
        (RECOVER_IP, ip_of(request)),
        (RECOVER_ACCOUNT, account_key(body.institution_id, body.email)),
    )
    tenant = find_tenant(backends, body.institution_id)
    if tenant is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Recovery failed.")
    with unit_of_work(backends, tenant.college_id) as unit:
        refused = unit.auth.recover(
            tenant.college_id, body.email, body.recovery_code, body.new_password
        )
    if refused is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Recovery failed.")


@router.post(
    "/password/change",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_401, 403: {"model": ErrorOut}, **_422},
)
def change_password(body: ChangePasswordIn, who: PrincipalDep, backends: BackendsDep) -> None:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who)
        unit.auth.change_password(
            who.college_id, who.user_id, body.current_password, body.new_password
        )


@router.post(
    "/recovery-codes",
    status_code=status.HTTP_201_CREATED,
    response_model=RecoveryCodesOut,
    responses={**_401, 403: {"model": ErrorOut}},
    summary="Issue 10 new one-time recovery codes (replaces the old set; shown once)",
)
def issue_recovery_codes(
    body: CurrentPasswordIn, who: PrincipalDep, backends: BackendsDep
) -> RecoveryCodesOut:
    with unit_of_work(backends, who.college_id) as unit:
        current_user(unit, who)
        codes = unit.auth.issue_recovery_codes(who.college_id, who.user_id, body.current_password)
    return RecoveryCodesOut(codes=list(codes))


@router.post(
    "/invitations/accept",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={400: {"model": ErrorOut}, **_422, **_429},
    summary="A new teacher sets a first password from the invitation link",
    dependencies=[Depends(per_ip(TOKEN_IP))],
)
def accept_invitation(body: InviteAcceptIn, backends: BackendsDep) -> None:
    college_id = token_college(body.token)
    with unit_of_work(backends, college_id) as unit:
        unit.auth.accept_invite(college_id, body.token, body.password)


@router.get(
    "/password-bloom",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
    summary="Bloom filter of common passwords (a hint for the browser; the server decides)",
)
def password_bloom() -> Response:
    return Response(
        content=bundled_bloom_bytes(),
        media_type="application/octet-stream",
        headers={"Cache-Control": "public, max-age=86400"},
    )
