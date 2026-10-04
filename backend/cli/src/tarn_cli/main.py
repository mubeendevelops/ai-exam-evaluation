"""``tarn`` command-line interface."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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

if TYPE_CHECKING:
    from tarn_adapters.ocr.wiring import OcrSetup

app = typer.Typer(help="Tarn AI Evaluation command-line interface.", no_args_is_help=True)
db = typer.Typer(help="Database schema and roles (uses the owner role, TARN_DATABASE_URL).")
identity = typer.Typer(
    help="The identity database (credentials, tenants): schema, role, encrypted backups."
)
tenants = typer.Typer(help="Tarn operator commands: list and approve tenant registrations.")
pages = typer.Typer(help="Page cleaning without a database or queue.")
ocr = typer.Typer(help="OCR engines (P10): models, reading files, calibration.")
ocr_models = typer.Typer(help="Model weights and the compute device.")
ocr.add_typer(ocr_models, name="models")
app.add_typer(db, name="db")
app.add_typer(pages, name="pages")
app.add_typer(ocr, name="ocr")
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


@pages.command("check")
def pages_check(
    paths: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, readable=True)],
    out: Annotated[
        Path | None, typer.Option(help="Write the cleaned pages and report.json here.")
    ] = None,
    max_pages: Annotated[int, typer.Option(help="Page limit per file.")] = 60,
) -> None:
    """Run PDFs or images through splitting, cleaning and the quality gate; print one line per
    page (sharpness, glare, what was done). Flags: C cropped, P perspective corrected,
    N neighbouring page removed, ? direction of a quarter turn guessed."""
    from tarn_adapters.imaging.batch import check_files, write_report
    from tarn_core.errors import DomainError

    results = []
    try:
        for check in check_files(paths, out, max_pages=max_pages):
            typer.echo(check.line())
            results.append(check)
    except DomainError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    flagged = sum(1 for c in results if c.reasons)
    typer.echo(f"{len(results)} pages, {flagged} flagged for retake")
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        write_report(results, out / "report.json")


# --- OCR (P10) ----------------------------------------------------------------------------------


def _ocr_setup(settings: Settings) -> "OcrSetup":
    from tarn_adapters.ocr.wiring import build_ocr

    setup = build_ocr(settings)
    typer.echo(
        f"device {setup.device.kind} ({setup.device.detail}); engines: "
        f"{', '.join(setup.engines) or 'none'}"
    )
    for name, reason in setup.skipped.items():
        typer.echo(f"  skipped {name}: {reason}")
    return setup


@ocr_models.command("check")
def ocr_models_check() -> None:
    """Show the device, the configured engines, which can run, and which are left out why."""
    _ocr_setup(get_settings())


@ocr_models.command("fetch")
def ocr_models_fetch() -> None:
    """Download the weights of every local engine into TARN_MODEL_DIR (once; then offline) and
    read a generated line with each, reporting the time and the GPU memory used."""
    import time

    import cv2
    import numpy as np

    from tarn_adapters.ocr.images import encode_jpeg
    from tarn_core.domain.common import Box

    setup = _ocr_setup(get_settings())
    line = np.full((64, 640, 3), 255, np.uint8)
    cv2.putText(line, "Tarn model check 42", (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
    image = encode_jpeg(line)
    box = [Box(x0=0, y0=0, x1=640, y1=64)]
    started = time.perf_counter()
    setup.layout.detect(image)
    typer.echo(f"layout {setup.layout.ref.version}: {time.perf_counter() - started:.1f}s")
    for name, engine in setup.engines.items():
        started = time.perf_counter()
        try:
            readings = engine.read(image, box)
        except Exception as error:  # report and go on with the next engine
            typer.echo(f"{name}: FAILED ({type(error).__name__}: {error})")
            continue
        text = readings[0].text if readings else ""
        typer.echo(f"{name} {engine.ref.version}: {text!r} in {time.perf_counter() - started:.1f}s")
    if setup.device.kind == "cuda":
        import torch

        typer.echo(f"GPU memory peak: {torch.cuda.max_memory_allocated() // 2**20} MB")


@ocr.command("read")
def ocr_read(
    paths: Annotated[list[Path], typer.Argument(exists=True, dir_okay=False, readable=True)],
    out: Annotated[Path | None, typer.Option(help="Write report.json here.")] = None,
    text: Annotated[
        Path | None,
        typer.Option(
            help="Also write the recognised text per booklet here. It is student data: "
            "use a local, git-ignored folder."
        ),
    ] = None,
    max_pages: Annotated[int, typer.Option(help="Page limit per file.")] = 60,
) -> None:
    """Clean and read PDFs or images page by page without the stack: one line per page with
    the orientation decision, lines, flagged lines, mean line score and failed engines. No
    recognised text is printed."""
    from tarn_adapters.ocr.batch import read_files, write_report
    from tarn_core.errors import DomainError

    setup = _ocr_setup(get_settings())
    results = []
    try:
        for page in read_files(paths, setup, text_dir=text, max_pages=max_pages):
            typer.echo(page.line())
            results.append(page)
    except DomainError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    lines = sum(r.lines for r in results)
    flagged = sum(r.flagged for r in results)
    turned = sum(r.turned for r in results)
    typer.echo(f"{len(results)} pages, {lines} lines, {flagged} flagged, {turned} turned 180")
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        write_report(results, out / "report.json")


@ocr.command("calibrate")
def ocr_calibrate(
    manifest: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    min_samples: Annotated[int, typer.Option(help="Lines needed per engine and class.")] = 30,
    dry_run: Annotated[bool, typer.Option(help="Print the fit; write nothing.")] = False,
) -> None:
    """Fit isotonic calibrations and engine weights from hand-transcribed lines and store them
    as the next versions in ocr_calibrations (Tarn operator: uses the owner role,
    TARN_DATABASE_URL). MANIFEST is JSON lines: {"image", "box"?, "text", "class"}."""
    from tarn_adapters.ocr.batch import read_ground_truth, read_manifest
    from tarn_adapters.postgres.database import PostgresDatabase
    from tarn_adapters.runtime import UuidGenerator
    from tarn_core.errors import DomainError
    from tarn_core.services.ocr.calibration import fit_calibrations

    settings = get_settings()
    try:
        truth = read_manifest(manifest)
    except DomainError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None
    setup = _ocr_setup(settings)
    samples = read_ground_truth(truth, setup)
    typer.echo(f"{len(truth)} ground-truth lines, {len(samples)} readings")
    database = PostgresDatabase(settings.database_url, pool_size=1)
    try:
        with database.session(
            None, ids=UuidGenerator(), clock=SystemClock(), blobs=NoBlobStore()
        ) as session:
            previous = {(c.engine, c.content_class): c for c in session.calibrations.latest()}
            fitted = fit_calibrations(
                samples,
                previous=previous,
                fitted_at=SystemClock().now(),
                min_samples=min_samples,
            )
            for cal in fitted:
                typer.echo(
                    f"{cal.engine:<10} {cal.content_class.value:<8} v{cal.version}  "
                    f"n={cal.samples:<5} CER {cal.error_rate or 0:.3f}  weight {cal.weight:.3f}  "
                    f"{len(cal.xs)} breakpoints"
                )
                if not dry_run:
                    session.calibrations.save(cal)
            if not fitted:
                typer.echo(f"nothing fitted: no engine and class has {min_samples} lines")
    finally:
        database.dispose()
