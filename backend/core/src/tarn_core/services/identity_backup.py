"""Encrypted backups of one tenant's identity records (P4).

A bundle is a JSON document. Its readable part names the tenant, the KMS key and the
tenant's *wrapped* data key; the records themselves (tenant registry entry and every identity
record, in the published schemas) are sealed with the tenant's data key. Restoring needs
access to the KMS key; the bundle alone reveals nothing but the Institution ID.

Sessions and emailed links are not backed up: they are short-lived, and a restore signs
everyone out."""

import base64
import json
from dataclasses import dataclass

from tarn_core.domain.common import JsonValue
from tarn_core.domain.identity import TenantRecord
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId
from tarn_core.ports.identity import Cipher, IdentityStore, KeyManager, RecordValidator
from tarn_core.ports.runtime import Clock
from tarn_core.services.envelope import TenantVault, context_for
from tarn_core.services.identity_records import (
    FullIdentity,
    identity_from_json,
    identity_to_json,
    ordered_codes,
    tenant_from_json,
    tenant_to_json,
)

FORMAT = "tarn.identity-backup"
FORMAT_VERSION = 1


def _aad(tenant_id: str, created_at: str) -> str:
    return f"{FORMAT}|{FORMAT_VERSION}|{tenant_id}|{created_at}"


@dataclass(frozen=True, slots=True, kw_only=True)
class RestoreReport:
    tenant: TenantRecord
    identities: int


class IdentityBackupService:
    def __init__(
        self,
        *,
        identity: IdentityStore,
        keys: KeyManager,
        cipher: Cipher,
        clock: Clock,
        validator: RecordValidator | None = None,
    ) -> None:
        self._identity = identity
        self._keys = keys
        self._cipher = cipher
        self._clock = clock
        self._validator = validator

    def export(self, college_id: CollegeId) -> bytes:
        tenant = self._identity.get_tenant(college_id)
        records: list[JsonValue] = []
        for identity in self._identity.list_identities(college_id):
            full = FullIdentity(
                identity=identity,
                counters=self._identity.counters(college_id, identity.user_id),
                codes=ordered_codes(self._identity.recovery_codes(college_id, identity.user_id)),
            )
            records.append(identity_to_json(full))
        tenant_json = tenant_to_json(tenant)
        if self._validator is not None:
            self._validator.validate_tenant(tenant_json)
            for record in records:
                self._validator.validate_identity(record)
        created_at = self._clock.now().isoformat().replace("+00:00", "Z")
        inner = json.dumps({"tenant": tenant_json, "identities": records}, sort_keys=True)
        vault = TenantVault.for_tenant(tenant, self._keys, self._cipher)
        sealed = vault.seal_bytes(inner.encode(), _aad(str(college_id), created_at))
        outer = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "tenant_id": str(college_id),
            "institution_id": tenant.institution_id,
            "created_at": created_at,
            "kms_key_ref": tenant.kms_key_ref,
            "wrapped_data_key": base64.b64encode(tenant.wrapped_data_key).decode(),
            "data_key_version": tenant.data_key_version,
            "identity_count": len(records),
            "ciphertext": base64.b64encode(sealed).decode(),
        }
        return json.dumps(outer, indent=2, sort_keys=True).encode()

    def restore(self, bundle: bytes) -> RestoreReport:
        """Insert or overwrite the tenant and its identity records, counters and recovery
        codes. Raises InvariantError for a bundle that is not valid or was altered."""
        try:
            outer = json.loads(bundle)
        except ValueError as exc:
            raise InvariantError("the backup is not JSON") from exc
        if not isinstance(outer, dict) or outer.get("format") != FORMAT:
            raise InvariantError("not a Tarn identity backup")
        if outer.get("format_version") != FORMAT_VERSION:
            raise InvariantError("unsupported backup version")
        try:
            tenant_id = str(outer["tenant_id"])
            created_at = str(outer["created_at"])
            key_ref = str(outer["kms_key_ref"])
            wrapped = base64.b64decode(str(outer["wrapped_data_key"]))
            version = int(outer["data_key_version"])
            sealed = base64.b64decode(str(outer["ciphertext"]))
        except (KeyError, ValueError) as exc:
            raise InvariantError("the backup is missing a field") from exc
        # The readable header names the key; the sealed tenant record must agree with it.
        probe = TenantVault(
            key_ref=key_ref,
            wrapped_key=wrapped,
            key_version=version,
            context=context_for(tenant_id),
            keys=self._keys,
            cipher=self._cipher,
        )
        try:
            inner = json.loads(probe.open_bytes(sealed, _aad(tenant_id, created_at)))
        except ValueError as exc:
            raise InvariantError("the backup cannot be decrypted or was altered") from exc
        tenant_json = inner.get("tenant")
        records = inner.get("identities")
        if not isinstance(records, list):
            raise InvariantError("the backup has no identity list")
        if self._validator is not None:
            self._validator.validate_tenant(tenant_json)
            for record in records:
                self._validator.validate_identity(record)
        tenant = tenant_from_json(tenant_json)
        if (
            str(tenant.college_id) != tenant_id
            or tenant.kms_key_ref != key_ref
            or tenant.wrapped_data_key != wrapped
        ):
            raise InvariantError("the sealed tenant record does not match the bundle")
        fulls = [identity_from_json(r) for r in records]
        for full in fulls:
            if full.identity.college_id != tenant.college_id:
                raise InvariantError("an identity record belongs to another tenant")
        self._identity.save_tenant(tenant)
        for full in fulls:
            uid = full.identity.user_id
            self._identity.save_identity(tenant.college_id, full.identity)
            self._identity.set_counters(tenant.college_id, uid, full.counters)
            self._identity.replace_recovery_codes(tenant.college_id, uid, full.codes)
        return RestoreReport(tenant=tenant, identities=len(fulls))
