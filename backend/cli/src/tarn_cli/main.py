"""``tarn`` command-line interface."""

import typer

from tarn_adapters.blob.minio_client import ensure_bucket, make_client
from tarn_adapters.compute import detect_device
from tarn_adapters.config import get_settings
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.health import check_database
from tarn_cli import __version__

app = typer.Typer(help="Tarn AI Evaluation command-line interface.", no_args_is_help=True)
db = typer.Typer(help="Database schema and roles (uses the owner role, TARN_DATABASE_URL).")
app.add_typer(db, name="db")


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
