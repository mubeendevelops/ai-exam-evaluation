"""``tarn`` command-line interface."""

import typer

from tarn_adapters.blob.minio_client import ensure_bucket, make_client
from tarn_adapters.compute import detect_device
from tarn_adapters.config import get_settings
from tarn_adapters.postgres.health import check_database
from tarn_cli import __version__

app = typer.Typer(help="Tarn AI Evaluation command-line interface.", no_args_is_help=True)


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
        db = check_database(settings.database_url)
        vector = db.pgvector_version or "MISSING"
        typer.echo(f"postgres    : {db.server_version}, pgvector {vector}")
        failed = db.pgvector_version is None
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
