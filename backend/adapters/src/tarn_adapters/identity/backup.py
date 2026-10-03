"""Encrypted identity backups on disk: one JSON bundle per tenant per run, under
``<backup dir>/<tenant id>/<UTC time>.json`` (mode 600), keeping the newest N per tenant.

Production writes to a Cloud Storage bucket with its own retention (P20); the bundle format
is the same."""

import os
from dataclasses import dataclass
from pathlib import Path

from tarn_adapters.auth.schemas import JsonSchemaValidator
from tarn_adapters.identity.database import IdentityDatabase
from tarn_core.domain.identity import TenantRecord
from tarn_core.ports.identity import Cipher, IdentityStore, KeyManager
from tarn_core.ports.runtime import Clock
from tarn_core.services.identity_backup import IdentityBackupService, RestoreReport


@dataclass(frozen=True, slots=True)
class Written:
    tenant: TenantRecord
    path: Path


@dataclass(frozen=True, slots=True)
class BackupRun:
    written: list[Written]
    failed: list[tuple[TenantRecord, str]]
    """Tenants that could not be backed up, with the error type (never its message)."""


def _service(
    store: IdentityStore, keys: KeyManager, cipher: Cipher, clock: Clock
) -> IdentityBackupService:
    return IdentityBackupService(
        identity=store,
        keys=keys,
        cipher=cipher,
        clock=clock,
        validator=JsonSchemaValidator(),
    )


def export_all(
    db: IdentityDatabase,
    *,
    keys: KeyManager,
    cipher: Cipher,
    clock: Clock,
    out_dir: Path,
    keep: int = 14,
) -> BackupRun:
    """Back up every tenant, each in its own transaction (one college per session). One
    tenant failing (say, its KMS key is unavailable) does not stop the others."""
    with db.session() as store:
        tenants = list(store.list_tenants())
    written: list[Written] = []
    failed: list[tuple[TenantRecord, str]] = []
    stamp = clock.now().strftime("%Y%m%dT%H%M%S%fZ")
    for tenant in tenants:
        try:
            with db.session() as store:
                bundle = _service(store, keys, cipher, clock).export(tenant.college_id)
        except Exception as exc:  # report and go on with the next tenant
            failed.append((tenant, type(exc).__name__))
            continue
        folder = out_dir / str(tenant.college_id)
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = folder / f"{stamp}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(bundle)
        for old in sorted(folder.glob("*.json"))[:-keep] if keep > 0 else []:
            old.unlink()
        written.append(Written(tenant=tenant, path=path))
    return BackupRun(written=written, failed=failed)


def restore_file(
    db: IdentityDatabase, path: Path, *, keys: KeyManager, cipher: Cipher, clock: Clock
) -> RestoreReport:
    with db.session() as store:
        return _service(store, keys, cipher, clock).restore(path.read_bytes())
