"""P4: encrypted identity backup export/import round trip, and the JSON record forms."""

import base64
import json
from dataclasses import replace
from typing import Any

import pytest

from tarn_core.domain.common import JsonValue
from tarn_core.domain.identity import LoginCounters
from tarn_core.errors import InvariantError
from tarn_core.ids import CollegeId, UserId
from tarn_core.services.auth import SignedIn
from tarn_core.services.identity_backup import IdentityBackupService
from tarn_core.services.identity_records import identity_from_json, tenant_to_json
from tarn_core.testing import InMemory, MemoryIdentityStore
from tarn_core.testing.auth_world import ADMIN_PASSWORD, TEACHER_PASSWORD, AuthWorld

ADMIN = "admin@a.example"
TEACHER = "t@a.example"


def _service(mem: InMemory, store: MemoryIdentityStore) -> IdentityBackupService:
    return IdentityBackupService(identity=store, keys=mem.keys, cipher=mem.cipher, clock=mem.clock)


def _world() -> tuple[AuthWorld, CollegeId, UserId, UserId]:
    w = AuthWorld(InMemory())
    cid, admin = w.register("COLLEGE_A", ADMIN)
    teacher = w.invite(cid, admin, TEACHER)
    w.auth.issue_recovery_codes(cid, admin, ADMIN_PASSWORD)
    w.auth.login(cid, TEACHER, "wrong password 1")  # one failed attempt on record
    return w, cid, admin, teacher


def test_round_trip_restores_every_record_exactly() -> None:
    w, cid, admin, teacher = _world()
    shamir: dict[str, JsonValue] = {
        "type": "SHAMIR_SECRET_SHARE",
        "threshold": 2,
        "total_shares": 3,
        "encrypted_share_blob": "enc:v1:1:AAAA",
    }
    store = w.mem.identity
    store.identities[admin] = replace(store.identities[admin], other_recovery_factors=(shamir,))
    bundle = _service(w.mem, store).export(cid)
    text = bundle.decode()
    # Nothing readable but the header: no emails, hashes or tokens.
    for secret in (ADMIN, TEACHER, "fake$", "enc:v1:1:AAAA"):
        assert secret not in text
    fresh = MemoryIdentityStore()
    report = _service(w.mem, fresh).restore(bundle)
    assert report.identities == 2
    assert fresh.tenants == store.tenants
    assert fresh.identities == store.identities
    for user in (admin, teacher):
        assert fresh.counters(cid, user) == store.counters(cid, user)
        assert fresh.recovery_codes(cid, user) == store.recovery_codes(cid, user)
    assert fresh.counters(cid, teacher).failed_login_attempts == 1
    assert fresh.identities[admin].other_recovery_factors == (shamir,)
    # The restored store signs people in.
    w2 = AuthWorld(w.mem)
    w2.mem.identity = fresh
    assert isinstance(w2.auth.login(cid, TEACHER, TEACHER_PASSWORD), SignedIn)


def test_tampered_or_foreign_bundles_are_refused() -> None:
    w, cid, _, _ = _world()
    bundle = json.loads(_service(w.mem, w.mem.identity).export(cid))
    sealed = bytearray(base64.b64decode(bundle["ciphertext"]))
    sealed[-1] ^= 1
    altered = bundle | {"ciphertext": base64.b64encode(bytes(sealed)).decode()}
    with pytest.raises(InvariantError, match="decrypted or was altered"):
        _service(w.mem, MemoryIdentityStore()).restore(json.dumps(altered).encode())
    moved = bundle | {"created_at": "2030-01-01T00:00:00Z"}  # header bound by the AAD
    with pytest.raises(InvariantError):
        _service(w.mem, MemoryIdentityStore()).restore(json.dumps(moved).encode())
    with pytest.raises(InvariantError, match="not a Tarn"):
        _service(w.mem, MemoryIdentityStore()).restore(b'{"format": "other"}')
    with pytest.raises(InvariantError, match="not JSON"):
        _service(w.mem, MemoryIdentityStore()).restore(b"\x00")


def test_restore_needs_the_kms_key() -> None:
    from tarn_core.testing import FakeKeyManager

    w, cid, _, _ = _world()
    bundle = _service(w.mem, w.mem.identity).export(cid)
    other_kms = IdentityBackupService(
        identity=MemoryIdentityStore(),
        keys=FakeKeyManager(b"another"),
        cipher=w.mem.cipher,
        clock=w.mem.clock,
    )
    with pytest.raises(InvariantError):
        other_kms.restore(bundle)


def test_example_record_shape_is_read() -> None:
    """The boss's example (docs/auth/identity-record.example.json), with UUID ids."""
    example: dict[str, Any] = {
        "version": "1.0",
        "tenant_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000002",
        "identity": {
            "login_handle": "evaluator@institution.edu",
            "email_canonical": "evaluator@institution.edu",
            "status": "ACTIVE",
        },
        "authentication": {
            "password_hash": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdF9iYXNlNjQ$...",
            "last_password_change": "2026-09-29T08:00:00Z",
            "force_reset": False,
        },
        "recovery_support": {
            "recovery_version": "v1",
            "encrypted_master_recovery_token": "enc:v1:kms-key-id:AQICAHg...",
            "recovery_factors": [
                {
                    "type": "SECURE_RECOVERY_CODE",
                    "code_hash": "$argon2id$v=19$m=65536,t=3,p=4$...",
                    "created_at": "2026-09-29T08:00:00Z",
                    "used": False,
                },
                {
                    "type": "SHAMIR_SECRET_SHARE",
                    "threshold": 2,
                    "total_shares": 3,
                    "encrypted_share_blob": "enc:v1:kms-key-id:B12389...",
                },
            ],
        },
        "audit": {
            "created_at": "2026-01-15T10:00:00Z",
            "updated_at": "2026-09-29T08:00:00Z",
            "failed_login_attempts": 0,
            "lockout_until": None,
        },
    }
    full = identity_from_json(example)
    assert full.identity.recovery_version == 1
    assert len(full.codes) == 1 and not full.codes[0].used
    assert full.identity.other_recovery_factors == (
        example["recovery_support"]["recovery_factors"][1],
    )
    assert full.counters == LoginCounters()


def test_tenant_json_uses_provider_neutral_names() -> None:
    w, cid, _, _ = _world()
    record = tenant_to_json(w.tenant(cid))
    assert "kms_key_ref" in record and "kms_key_arn" not in record
