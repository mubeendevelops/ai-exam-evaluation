"""Request and response bodies (the OpenAPI contract, ``docs/api/openapi.json``)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from tarn_core.domain.tenancy import User

Email = Field(min_length=3, max_length=320, examples=["evaluator@institution.edu"])
Password = Field(min_length=1, max_length=1024)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class ErrorOut(BaseModel):
    detail: str


class PolicyErrorOut(BaseModel):
    detail: str
    reasons: list[str]


# --- sign-in ----------------------------------------------------------------------------------


class LoginIn(_In):
    institution_id: str = Field(
        min_length=1,
        max_length=64,
        description="Institution / Org Domain ID; compared upper-cased.",
        examples=["TARN_INST_01"],
    )
    email: str = Email
    password: str = Password
    remember: bool = Field(False, description="Remember session: a persistent, longer session.")


class UserOut(BaseModel):
    id: UUID
    college_id: UUID
    display_name: str
    email: str
    role: Literal["admin", "teacher"]
    active: bool

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=user.id,
            college_id=user.college_id,
            display_name=user.display_name,
            email=user.email,
            role=user.role.value,
            active=user.active,
        )


class TokenOut(BaseModel):
    status: Literal["signed_in"] = "signed_in"
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  (OAuth2 token type)
    expires_in: int = Field(description="Seconds until the access token expires.")
    user: UserOut


class ResetRequiredOut(BaseModel):
    """The password is right, but an administrator requires a new one first."""

    status: Literal["reset_required"] = "reset_required"
    reset_token: str = Field(description="Use with POST /api/v1/auth/password/reset.")


class MeOut(BaseModel):
    user: UserOut
    institution_id: str
    college_name: str
    recovery_codes_left: int


# --- passwords --------------------------------------------------------------------------------


class ForgotIn(_In):
    institution_id: str = Field(min_length=1, max_length=64)
    email: str = Email


class ResetIn(_In):
    token: str = Field(min_length=10, max_length=256)
    new_password: str = Password


class RecoverIn(_In):
    institution_id: str = Field(min_length=1, max_length=64)
    email: str = Email
    recovery_code: str = Field(min_length=16, max_length=64, examples=["ABCD-EFGH-IJKL-MNOP"])
    new_password: str = Password


class ChangePasswordIn(_In):
    current_password: str = Password
    new_password: str = Password


class CurrentPasswordIn(_In):
    current_password: str = Password


class RecoveryCodesOut(BaseModel):
    codes: list[str] = Field(description="Shown once. Only their hashes are stored.")


class InviteAcceptIn(_In):
    token: str = Field(min_length=10, max_length=256)
    password: str = Password


# --- registration -----------------------------------------------------------------------------


class AvailabilityOut(BaseModel):
    institution_id: str
    valid: bool
    available: bool
    problem: str | None = None


class RegistrationIn(_In):
    institution_id: str = Field(
        min_length=1,
        max_length=20,
        description="Upper-case letters, digits and _ - * &; at most 20; no spaces.",
        examples=["TARN_INST_01&*"],
    )
    institution_name: str | None = Field(None, max_length=200)
    admin_name: str = Field(min_length=1, max_length=200, examples=["Dr. Sarah Connor"])
    email: str = Email
    password: str = Password


class RegistrationOut(BaseModel):
    institution_id: str
    status: str
    approval_required: bool


class VerifyEmailIn(_In):
    token: str = Field(min_length=10, max_length=256)


class TenantStatusOut(BaseModel):
    institution_id: str
    status: str


# --- accounts ---------------------------------------------------------------------------------


class AccountOut(BaseModel):
    user: UserOut
    status: Literal["PENDING", "ACTIVE", "DISABLED"]
    locked_until: datetime | None
    force_reset: bool


class InviteIn(_In):
    display_name: str = Field(min_length=1, max_length=200)
    email: str = Email


# --- roster -----------------------------------------------------------------------------------


class RowErrorOut(BaseModel):
    line: int
    field: str
    message: str


class RosterReportOut(BaseModel):
    imported: bool = Field(description="False if any row has an error: then nothing is written.")
    rows: int
    created: int
    updated: int
    unchanged: int
    errors: list[RowErrorOut]


class StudentOut(BaseModel):
    id: UUID
    name: str
    usn: str
    class_section: str
