"""JSON forms of tenant-registry and identity records.

They follow ``docs/auth/tenant-registry.schema.json`` and
``docs/auth/identity-record.schema.json``, which were written from the two examples in
``docs/auth/`` with provider-neutral names (``kms_key_ref``, not ``kms_key_arn``)."""

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID

from tarn_core.domain.common import JsonValue
from tarn_core.domain.identity import (
    AuthPolicy,
    IdentityRecord,
    IdentityStatus,
    LoginCounters,
    RecoveryCode,
    TenantRecord,
    TenantStatus,
)
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, UserId

RECORD_VERSION = "1.0"
RECOVERY_CODE = "SECURE_RECOVERY_CODE"


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _parse_time(value: JsonValue) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvariantError("a time must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise InvariantError("a time must carry its offset")
    return parsed


def _required_time(value: JsonValue) -> datetime:
    parsed = _parse_time(value)
    if parsed is None:
        raise InvariantError("a required time is missing")
    return parsed


def _obj(value: JsonValue, what: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, dict):
        raise InvariantError(f"{what} must be an object")
    return value


def _str(value: JsonValue, what: str) -> str:
    if not isinstance(value, str):
        raise InvariantError(f"{what} must be a string")
    return value


def _int(value: JsonValue, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvariantError(f"{what} must be an integer")
    return value


# --- tenant registry ------------------------------------------------------------------------


def tenant_to_json(tenant: TenantRecord) -> dict[str, JsonValue]:
    p = tenant.policy
    return {
        "tenant_id": str(tenant.college_id),
        "tenant_name": tenant.name,
        "kms_key_ref": tenant.kms_key_ref,
        "auth_policy": {
            "min_password_length": p.min_password_length,
            "argon_time_cost": p.argon_time_cost,
            "argon_memory_cost": p.argon_memory_cost,
            "argon_parallelism": p.argon_parallelism,
            "max_failed_logins": p.max_failed_logins,
            "lockout_minutes": p.lockout_minutes,
        },
        "institution_id": tenant.institution_id,
        "status": tenant.status.value,
        "approval_required": tenant.approval_required,
        "data_key": {
            "wrapped": base64.b64encode(tenant.wrapped_data_key).decode(),
            "version": tenant.data_key_version,
        },
        "created_at": _time(tenant.created_at),
        "updated_at": _time(tenant.updated_at),
        "email_verified_at": _time(tenant.email_verified_at),
        "approved_at": _time(tenant.approved_at),
        "approved_by": tenant.approved_by,
    }


def tenant_from_json(value: JsonValue) -> TenantRecord:
    """Restores need the Tarn extensions (institution_id, data_key, ...), not only the
    fields of the original example."""
    record = _obj(value, "tenant record")
    policy = _obj(record.get("auth_policy"), "auth_policy")
    defaults = AuthPolicy()
    data_key = _obj(record.get("data_key"), "data_key")
    try:
        return TenantRecord(
            college_id=CollegeId(UUID(_str(record.get("tenant_id"), "tenant_id"))),
            institution_id=_str(record.get("institution_id"), "institution_id"),
            name=_str(record.get("tenant_name"), "tenant_name"),
            kms_key_ref=_str(record.get("kms_key_ref"), "kms_key_ref"),
            wrapped_data_key=base64.b64decode(_str(data_key.get("wrapped"), "data_key.wrapped")),
            data_key_version=_int(data_key.get("version"), "data_key.version"),
            policy=AuthPolicy(
                min_password_length=_int(
                    policy.get("min_password_length", defaults.min_password_length), "policy"
                ),
                argon_time_cost=_int(
                    policy.get("argon_time_cost", defaults.argon_time_cost), "policy"
                ),
                argon_memory_cost=_int(
                    policy.get("argon_memory_cost", defaults.argon_memory_cost), "policy"
                ),
                argon_parallelism=_int(
                    policy.get("argon_parallelism", defaults.argon_parallelism), "policy"
                ),
                max_failed_logins=_int(
                    policy.get("max_failed_logins", defaults.max_failed_logins), "policy"
                ),
                lockout_minutes=_int(
                    policy.get("lockout_minutes", defaults.lockout_minutes), "policy"
                ),
            ),
            status=TenantStatus(_str(record.get("status"), "status")),
            approval_required=bool(record.get("approval_required", True)),
            created_at=_required_time(record.get("created_at")),
            updated_at=_required_time(record.get("updated_at")),
            email_verified_at=_parse_time(record.get("email_verified_at")),
            approved_at=_parse_time(record.get("approved_at")),
            approved_by=cast(str | None, record.get("approved_by")),
        )
    except ValueError as exc:  # bad UUID, base64 or enum value
        raise InvariantError(f"tenant record is not valid: {type(exc).__name__}") from exc


# --- identity records -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FullIdentity:
    """Everything a backup holds for one user."""

    identity: IdentityRecord
    counters: LoginCounters
    codes: tuple[RecoveryCode, ...]


def identity_to_json(full: FullIdentity) -> dict[str, JsonValue]:
    i = full.identity
    factors: list[JsonValue] = [
        {
            "type": RECOVERY_CODE,
            "code_hash": c.code_hash,
            "created_at": _time(c.created_at),
            "used": c.used,
            "used_at": _time(c.used_at),
        }
        for c in sorted(full.codes, key=lambda c: c.ordinal)
    ]
    factors += list(i.other_recovery_factors)
    return {
        "version": RECORD_VERSION,
        "tenant_id": str(i.college_id),
        "user_id": str(i.user_id),
        "identity": {
            "login_handle": i.login_handle,
            "email_canonical": i.email_canonical,
            "status": i.status.value,
            "email_verified_at": _time(i.email_verified_at),
        },
        "authentication": {
            "password_hash": i.password_hash,
            "last_password_change": _time(i.last_password_change),
            "force_reset": i.force_reset,
        },
        "recovery_support": {
            "recovery_version": f"v{i.recovery_version}",
            "recovery_factors": factors,
        },
        "audit": {
            "created_at": _time(i.created_at),
            "updated_at": _time(i.updated_at),
            "failed_login_attempts": full.counters.failed_login_attempts,
            "lockout_until": _time(full.counters.lockout_until),
        },
    }


def identity_from_json(value: JsonValue) -> FullIdentity:
    record = _obj(value, "identity record")
    if record.get("version") != RECORD_VERSION:
        raise InvariantError("unsupported identity record version")
    ident = _obj(record.get("identity"), "identity")
    auth = _obj(record.get("authentication"), "authentication")
    recovery = _obj(record.get("recovery_support"), "recovery_support")
    audit = _obj(record.get("audit"), "audit")
    # encrypted_master_recovery_token (in the original example) is accepted and ignored: Tarn
    # does not use it (O9, removed on 2026-10-03).
    version = _str(recovery.get("recovery_version"), "recovery_version")
    if not version.startswith("v") or not version[1:].isdigit():
        raise InvariantError("recovery_version must look like v1")
    raw_factors = recovery.get("recovery_factors", [])
    if not isinstance(raw_factors, list):
        raise InvariantError("recovery_factors must be a list")
    codes: list[RecoveryCode] = []
    others: list[JsonValue] = []
    for factor in raw_factors:
        f = _obj(factor, "recovery factor")
        if f.get("type") == RECOVERY_CODE:
            used_at = _parse_time(f.get("used_at"))
            created = _required_time(f.get("created_at"))
            if f.get("used") is True and used_at is None:
                used_at = created  # the example format has only the flag
            codes.append(
                RecoveryCode(
                    ordinal=len(codes),
                    code_hash=_str(f.get("code_hash"), "code_hash"),
                    created_at=created,
                    used_at=used_at,
                )
            )
        else:
            others.append(factor)  # e.g. SHAMIR_SECRET_SHARE: kept, not used (not decided)
    try:
        college_id = CollegeId(UUID(_str(record.get("tenant_id"), "tenant_id")))
        user_id = UserId(UUID(_str(record.get("user_id"), "user_id")))
        status = IdentityStatus(_str(ident.get("status"), "status"))
    except ValueError as exc:
        raise InvariantError(f"identity record is not valid: {type(exc).__name__}") from exc
    identity = IdentityRecord(
        user_id=user_id,
        college_id=college_id,
        login_handle=_str(ident.get("login_handle"), "login_handle"),
        email_canonical=_str(ident.get("email_canonical"), "email_canonical"),
        status=status,
        email_verified_at=_parse_time(ident.get("email_verified_at")),
        password_hash=cast(str | None, auth.get("password_hash")),
        last_password_change=_parse_time(auth.get("last_password_change")),
        force_reset=bool(auth.get("force_reset", False)),
        recovery_version=int(version[1:]),
        other_recovery_factors=tuple(others),
        created_at=_required_time(audit.get("created_at")),
        updated_at=_required_time(audit.get("updated_at")),
    )
    counters = LoginCounters(
        failed_login_attempts=_int(audit.get("failed_login_attempts", 0), "failed_login_attempts"),
        lockout_until=_parse_time(audit.get("lockout_until")),
    )
    return FullIdentity(identity=identity, counters=counters, codes=tuple(codes))


def ordered_codes(codes: Sequence[RecoveryCode]) -> tuple[RecoveryCode, ...]:
    return tuple(sorted(codes, key=lambda c: c.ordinal))
