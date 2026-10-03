"""``tarn`` command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from tarn_adapters.auth.crypto import AesGcmCipher, RoutingKeyManager
from tarn_adapters.auth.wiring import key_manager
from tarn_adapters.blob.minio_client import ensure_bucket, make_client
from tarn_adapters.blob.unavailable import NoBlobStore
from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.identity import migrate as identity_migrate
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.health import check_database
from tarn_adapters.runtime import SystemClock
from tarn_cli import __version__

app = typer.Typer(help="Tarn AI Evaluation command-line interface.", no_args_is_help=True)
db = typer.Typer(help="Database schema and roles (uses the owner role, TARN_DATABASE_URL).")
identity = typer.Typer(
    help="The identity database (credentials, tenants): schema, role, encrypted backups."
)
tenants = typer.Typer(help="Tarn operator commands: list and approve tenant registrations.")
app.add_typer(db, name="db")
app.add_typer(identity, name="identity")
app.add_typer(tenants, name="tenants")


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def doctor(
    services: bool = typer.Option(False, "--services", help="Also check PostgreSQL and storage."),
) -> None:
    """Show the environment, the compute device and (optionally) service connectivity."""
    settings = get_settings()
    device = detect_device(settings.device)
    typer.echo(f"environment : {settings.env}")
    typer.echo(f"device      : {device.kind} ({device.name}) - {device.detail}")
    typer.echo(f"cloud OCR   : {'enabled' if settings.cloud_ocr_enabled else 'disabled'}")
    if not services:
        return
    failed = False
    try:
        status = check_database(settings.database_url)
        vector = status.pgvector_version or "MISSING"
        revision = migrate.current_revision(settings.database_url) or "none (run make migrate)"
        typer.echo(f"postgres    : {status.server_version}, pgvector {vector}, schema {revision}")
        failed = status.pgvector_version is None
        ident = identity_migrate.current_revision(settings.identity_database_url)
        typer.echo(f"identity db : schema {ident or 'none (run make migrate)'}")
        failed = failed or ident is None
    except Exception as exc:  # report any connectivity failure, then exit non-zero
        typer.echo(f"postgres    : FAILED ({type(exc).__name__})")
        failed = True
    try:
        exists = make_client(settings).bucket_exists(settings.blob_bucket)
        typer.echo(f"storage     : bucket '{settings.blob_bucket}' {'ok' if exists else 'MISSING'}")
        failed = failed or not exists
    except Exception as exc:
        typer.echo(f"storage     : FAILED ({type(exc).__name__})")
        failed = True
    if failed:
        raise typer.Exit(code=1)


@app.command("storage-init")
def storage_init() -> None:
    """Create the object-storage bucket if it does not exist."""
    settings = get_settings()
    created = ensure_bucket(make_client(settings), settings.blob_bucket)
    typer.echo(f"bucket '{settings.blob_bucket}' {'created' if created else 'already exists'}")


@db.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head")) -> None:
    """Apply migrations up to REVISION (default: head)."""
    migrate.upgrade(get_settings().database_url, revision)
    typer.echo(f"schema at {migrate.current_revision(get_settings().database_url)}")


@db.command("downgrade")
def db_downgrade(revision: str = typer.Argument(..., help="Target revision, e.g. base.")) -> None:
    """Revert migrations down to REVISION. Drops data: development only."""
    settings = get_settings()
    if settings.env == "production":
        typer.echo("refusing to downgrade in production")
        raise typer.Exit(code=1)
    migrate.downgrade(settings.database_url, revision)
    typer.echo(f"schema at {migrate.current_revision(settings.database_url) or 'base'}")


@db.command("app-login")
def db_app_login() -> None:
    """Let the application role (tarn_app) log in with the password in TARN_APP_DATABASE_URL."""
    settings = get_settings()
    migrate.grant_app_login(settings.database_url, settings.app_database_url)
    typer.echo("tarn_app can log in")


# --- identity database ------------------------------------------------------------------------


@identity.command("upgrade")
def identity_upgrade(revision: str = typer.Argument("head")) -> None:
    """Create the identity database if missing, then migrate it to REVISION."""
    settings = get_settings()
    if identity_migrate.ensure_database(settings.identity_database_url):
        typer.echo("identity database created")
    identity_migrate.upgrade(settings.identity_database_url, revision)
    typer.echo(
        f"identity schema at {identity_migrate.current_revision(settings.identity_database_url)}"
    )


@identity.command("downgrade")
def identity_downgrade(revision: str = typer.Argument(..., help="e.g. base")) -> None:
    """Revert identity migrations. Drops credentials: development only."""
    settings = get_settings()
    if settings.env == "production":
        typer.echo("refusing to downgrade in production")
        raise typer.Exit(code=1)
    identity_migrate.downgrade(settings.identity_database_url, revision)
    typer.echo("identity schema reverted")


@identity.command("app-login")
def identity_app_login() -> None:
    """Let tarn_auth log in with the password in TARN_IDENTITY_APP_DATABASE_URL."""
    settings = get_settings()
    identity_migrate.grant_app_login(
        settings.identity_database_url, settings.identity_app_database_url
    )
    typer.echo("tarn_auth can log in")


def _crypto(settings: Settings) -> tuple[RoutingKeyManager, AesGcmCipher, SystemClock]:
    return key_manager(settings), AesGcmCipher(), SystemClock()


@identity.command("export")
def identity_export(
    out: Annotated[
        Path | None, typer.Option(help="Directory (default TARN_IDENTITY_BACKUP_DIR).")
    ] = None,
) -> None:
    """Write an encrypted backup bundle of every tenant's identity records."""
    from tarn_adapters.identity.backup import export_all
    from tarn_adapters.identity.database import IdentityDatabase

    settings = get_settings()
    keys, cipher, clock = _crypto(settings)
    db = IdentityDatabase(settings.identity_app_database_url)
    try:
        run = export_all(
            db,
            keys=keys,
            cipher=cipher,
            clock=clock,
            out_dir=out or settings.identity_backup_dir,
            keep=settings.identity_backup_keep,
        )
    finally:
        db.dispose()
    for written in run.written:
        typer.echo(f"{written.tenant.institution_id}: {written.path}")
    for tenant, error in run.failed:
        typer.echo(f"{tenant.institution_id}: FAILED ({error})")
    if run.failed:
        raise typer.Exit(code=1)


@identity.command("import")
def identity_import(
    bundle: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Restore one tenant's identity records from an encrypted backup bundle. Sessions and
    emailed links are not restored: everyone signs in again."""
    from tarn_adapters.identity.backup import restore_file
    from tarn_adapters.identity.database import IdentityDatabase

    settings = get_settings()
    keys, cipher, clock = _crypto(settings)
    db = IdentityDatabase(settings.identity_app_database_url)
    try:
        report = restore_file(db, bundle, keys=keys, cipher=cipher, clock=clock)
    finally:
        db.dispose()
    typer.echo(f"restored {report.tenant.institution_id}: {report.identities} identities")


# --- development seed -------------------------------------------------------------------------


@app.command()
def seed() -> None:
    """Load the development seed data: two demo colleges, the sample papers, keys and rosters.

    Safe to run again. Needs the stack (make up) and the schemas (make migrate)."""
    from tarn_adapters.seed import run_seed
    from tarn_core.seed import SeedError

    settings = get_settings()
    try:
        summary = run_seed(settings)
    except SeedError as error:
        typer.echo(f"seed: {error}")
        raise typer.Exit(code=1) from None
    for label, counts in (("created", summary.report.created), ("kept", summary.report.kept)):
        line = ", ".join(f"{n} {what}" for what, n in counts.items()) or "nothing"
        typer.echo(f"{label:8}: {line}")
    typer.echo("")
    typer.echo(f"Sign in at {settings.public_url} (development password: {summary.password})")
    for college, _accounts in summary.colleges:
        typer.echo(f"  {college.institution_id:9} {college.name}")
        typer.echo(f"    admin   {college.admin.email}")
        for teacher in college.teachers:
            typer.echo(f"    teacher {teacher.email}")


# --- tenants (Tarn operators) -----------------------------------------------------------------


@tenants.command("list")
def tenants_list(
    status: str | None = typer.Option(None, help="e.g. PENDING_APPROVAL"),
) -> None:
    """List registered tenants."""
    from tarn_adapters.identity.database import IdentityDatabase
    from tarn_core.domain.identity import TenantStatus

    settings = get_settings()
    db = IdentityDatabase(settings.identity_app_database_url)
    try:
        with db.session() as store:
            rows = store.list_tenants(TenantStatus(status) if status else None)
    finally:
        db.dispose()
    for t in rows:
        verified = "verified" if t.email_verified_at else "unverified"
        approved = f"approved by {t.approved_by}" if t.approved_at else "not approved"
        typer.echo(f"{t.institution_id:<20} {t.status.value:<20} {verified}, {approved}  {t.name}")


@tenants.command("approve")
def tenants_approve(
    institution_id: str = typer.Argument(...),
    operator: str = typer.Option(..., help="Who approves (recorded in the audit log)."),
) -> None:
    """Approve a tenant registration. It becomes active once its email is verified."""
    from tarn_adapters.auth.wiring import auth_kit
    from tarn_adapters.identity.database import IdentityDatabase
    from tarn_adapters.postgres.database import PostgresDatabase
    from tarn_adapters.runtime import UuidGenerator
    from tarn_core.services.registration import RegistrationService

    settings = get_settings()
    identity_db = IdentityDatabase(settings.identity_app_database_url)
    app_db = PostgresDatabase(settings.app_database_url)
    try:
        with identity_db.session() as store:
            tenant = store.find_tenant(institution_id)
        if tenant is None:
            typer.echo(f"no tenant {institution_id}")
            raise typer.Exit(code=1)
        with (
            identity_db.session() as store,
            app_db.session(
                tenant.college_id, ids=UuidGenerator(), clock=SystemClock(), blobs=NoBlobStore()
            ) as session,
        ):
            approved = RegistrationService(
                identity=store,
                users=session.users,
                colleges=session.colleges,
                kit=auth_kit(settings),
                runtime=session.runtime,
            ).approve(tenant.college_id, operator=operator)
    finally:
        identity_db.dispose()
        app_db.dispose()
    typer.echo(f"{approved.institution_id}: {approved.status.value}")
