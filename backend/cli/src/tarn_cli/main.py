"""``tarn`` command-line interface."""

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from tarn_adapters.auth.crypto import AesGcmCipher, RoutingKeyManager
from tarn_adapters.auth.wiring import key_manager
from tarn_adapters.blob.unavailable import NoBlobStore
from tarn_adapters.blob.wiring import bucket_exists, init_bucket
from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.identity import migrate as identity_migrate
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.health import check_database
from tarn_adapters.runtime import SystemClock
from tarn_cli import __version__
from tarn_cli.evaluate import evaluate_command, exams_command

if TYPE_CHECKING:
    from tarn_adapters.ocr.wiring import OcrSetup
    from tarn_adapters.scoring.sets import CalibrationSet
    from tarn_core.ports.engines import WordList

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
truth = typer.Typer(help="Ground truth for the OCR benchmark (P11): pre-fill, transcribe, status.")
bench = typer.Typer(help="Benchmarks (P11, P12).")
score = typer.Typer(help="Text scoring (P13): model weights, calibration sets, benchmark, fit.")
score_models = typer.Typer(help="Sentence-embedding model weights for scoring.")
score.add_typer(score_models, name="models")
diagram = typer.Typer(help="Diagram recognition (P14): data, detector training, benchmark, use.")
diagram_data = typer.Typer(help="Detector training and test material (under var/diagrams).")
diagram_models = typer.Typer(help="The detector's base weights.")
diagram.add_typer(diagram_data, name="data")
diagram.add_typer(diagram_models, name="models")
app.add_typer(db, name="db")
app.add_typer(pages, name="pages")
app.add_typer(ocr, name="ocr")
app.add_typer(truth, name="truth")
app.add_typer(bench, name="bench")
app.add_typer(score, name="score")
app.add_typer(diagram, name="diagram")
app.add_typer(identity, name="identity")
app.add_typer(tenants, name="tenants")


app.command("evaluate")(evaluate_command)
app.command("exams")(exams_command)


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
    llm = "off"
    if settings.llm_scorer_enabled:
        n = len(settings.groq_keys)
        llm = f"on ({n} key{'s' if n != 1 else ''}; only colleges switched on are sent)"
    typer.echo(f"LLM scorer  : {llm}")
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
        exists = bucket_exists(settings)
        typer.echo(
            f"storage     : {settings.blob_backend} bucket '{settings.blob_bucket}' "
            f"{'ok' if exists else 'MISSING'}"
        )
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
    typer.echo(f"bucket '{settings.blob_bucket}' {init_bucket(settings)}")


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


@tenants.command("llm")
def tenants_llm(
    institution_id: str = typer.Argument(...),
    on: Annotated[
        bool,
        typer.Option(
            "--on/--off",
            help="--on lets the LLM scorer read this college's answers; --off (the default) "
            "keeps them local.",
        ),
    ] = False,
    operator: str = typer.Option(..., help="Who changes it (recorded in the audit log)."),
) -> None:
    """Switch the LLM scorer (P19) on or off for one college. On: the text of its answers (no
    names or USNs) goes to the LLM provider for a second opinion on each criterion. The worker
    also needs TARN_LLM_SCORER_ENABLED and a key; see CLAUDE.md for the warnings."""
    from tarn_adapters.identity.database import IdentityDatabase
    from tarn_adapters.postgres.database import PostgresDatabase
    from tarn_adapters.runtime import UuidGenerator
    from tarn_core.services.college_settings import CollegeSettings

    settings = get_settings()
    identity_db = IdentityDatabase(settings.identity_app_database_url)
    app_db = PostgresDatabase(settings.app_database_url)
    try:
        with identity_db.session() as store:
            tenant = store.find_tenant(institution_id)
        if tenant is None:
            typer.echo(f"no tenant {institution_id}")
            raise typer.Exit(code=1)
        with app_db.session(
            tenant.college_id, ids=UuidGenerator(), clock=SystemClock(), blobs=NoBlobStore()
        ) as session:
            college = CollegeSettings(
                colleges=session.colleges, runtime=session.runtime
            ).set_llm_scoring(tenant.college_id, on, operator=operator)
    finally:
        identity_db.dispose()
        app_db.dispose()
    typer.echo(f"{institution_id}: LLM scoring {'on' if college.llm_scoring else 'off'}")
    if college.llm_scoring and not settings.llm_scorer_enabled:
        typer.echo("note: the worker's TARN_LLM_SCORER_ENABLED is still false: nothing is sent")


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
    """Download the weights of every local engine and of the segmentation embedder into
    TARN_MODEL_DIR (once; then offline) and run each on a generated line, reporting the time and
    the GPU memory used."""
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
    from tarn_adapters.embed.wiring import build_embedder

    embedding = build_embedder(get_settings())
    if embedding.fallback_reason:
        typer.echo(f"embedder: trigram ({embedding.fallback_reason})")
    started = time.perf_counter()
    try:
        embedding.embedder.embed(["Tarn model check"])
    except Exception as error:  # report it like an engine
        typer.echo(f"embedder: FAILED ({type(error).__name__}: {error})")
    else:
        ref = embedding.embedder.ref
        typer.echo(
            f"embedder {ref.name} {ref.version} ({embedding.embedder.dimension} dimensions): "
            f"{time.perf_counter() - started:.1f}s"
        )


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


# --- ground truth and the OCR benchmark (P11) ----------------------------------------------------


def _page_numbers(spec: str) -> list[int]:
    """``3,5,7-9`` → [3, 5, 7, 8, 9] (1-based)."""
    numbers: list[int] = []
    try:
        for part in spec.split(","):
            low, dash, high = part.strip().partition("-")
            first, last = int(low), int(high) if dash else int(low)
            if last < first:
                raise ValueError(part)
            numbers += range(first, last + 1)
    except ValueError:
        raise typer.BadParameter("pages are like 3,5,7-9") from None
    if not numbers or min(numbers) < 1:
        raise typer.BadParameter("pages count from 1")
    return numbers


def _repo_root() -> Path:
    here = Path.cwd().resolve()
    for folder in (here, *here.parents):
        if (folder / "docs").is_dir() and (folder / "backend").is_dir():
            return folder
    return here


@truth.command("prefill")
def truth_prefill(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    set_dir: Annotated[Path, typer.Option("--set", help="The ground-truth folder.")],
    label: Annotated[
        str, typer.Option(help="Neutral name of the source, e.g. b-ci2 (page ids are LABEL-pNN).")
    ],
    capture: Annotated[str, typer.Option(help="scanning_app, clean_scan or phone_photo.")],
    pages: Annotated[str, typer.Option(help="Pages to take (1-based), e.g. 3,5,7-9.")],
    replace: Annotated[
        bool, typer.Option(help="Redo pages that are only pre-filled (verified work is kept).")
    ] = False,
    max_pages: Annotated[int, typer.Option(help="Page limit per file.")] = 60,
) -> None:
    """Clean, orient and read pages of a PDF or image with every engine and store them as
    ground-truth pages whose lines are pre-filled with the selector's reading, for a person to
    correct (`tarn truth transcribe`). The set is student data: keep it under var/."""
    from tarn_adapters.groundtruth.prefill import prefill_file
    from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore
    from tarn_core.domain.groundtruth import CaptureType
    from tarn_core.errors import DomainError
    from tarn_core.services.groundtruth import GroundTruthService

    try:
        capture_type = CaptureType(capture)
    except ValueError:
        raise typer.BadParameter(
            f"one of {', '.join(c.value for c in CaptureType)}", param_hint="--capture"
        ) from None
    wanted = _page_numbers(pages)
    service = GroundTruthService(DirectoryGroundTruthStore(set_dir))
    setup = _ocr_setup(get_settings())
    try:
        for stored in prefill_file(
            file,
            setup,
            service,
            label=label,
            capture=capture_type,
            pages=wanted,
            replace=replace,
            max_pages=max_pages,
        ):
            typer.echo(f"{stored.id}: {len(stored.lines)} lines pre-filled ({capture_type.value})")
    except DomainError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(1) from None


@truth.command("transcribe")
def truth_transcribe(
    set_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    port: Annotated[int, typer.Option(help="Port on 127.0.0.1 (0: any free one).")] = 0,
    open_browser: Annotated[
        bool, typer.Option("--open", help="Open the page in a browser.")
    ] = False,
) -> None:
    """Serve the transcription page for a ground-truth folder on 127.0.0.1 (student data: it
    never leaves this computer). The printed address carries a one-time token. Ctrl+C stops."""
    from tarn_adapters.groundtruth.server import TranscriptionServer
    from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore

    store = DirectoryGroundTruthStore(set_dir)
    if not store.page_ids():
        typer.echo("error: the folder has no pages (tarn truth prefill first)", err=True)
        raise typer.Exit(1)
    server = TranscriptionServer(store, port=port)
    typer.echo(f"transcription page: {server.url}")
    typer.echo("Ctrl+C to stop")
    if open_browser:
        import webbrowser

        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        typer.echo("stopped")


@truth.command("status")
def truth_status(set_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Counts only: pages and lines by status, verified lines by capture type and class."""
    from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore
    from tarn_core.services.groundtruth import GroundTruthService

    progress = GroundTruthService(DirectoryGroundTruthStore(set_dir)).progress()
    typer.echo(
        f"{progress.pages} pages, {progress.lines} lines: {progress.verified} verified, "
        f"{progress.prefilled} to check, {progress.ignored} ignored"
    )
    typer.echo(
        "verified by capture: "
        + (", ".join(f"{k.value} {v}" for k, v in progress.by_capture.items()) or "none")
    )
    typer.echo(
        "verified by class  : "
        + (", ".join(f"{k.value} {v}" for k, v in progress.by_class.items()) or "none")
    )


@bench.command("ocr")
def bench_ocr(
    set_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    out: Annotated[
        Path | None,
        typer.Option(help="Report file (default: docs/benchmarks/ocr-<date>[-<label>].md)."),
    ] = None,
    label: Annotated[str, typer.Option(help="Names the run (a model or setting compared).")] = "",
    cpu_timing: Annotated[
        bool, typer.Option(help="Also time the first pages with the engines forced onto the CPU.")
    ] = True,
    cpu_pages: Annotated[int, typer.Option(help="Pages timed on the CPU.")] = 3,
) -> None:
    """Run every enabled engine and the selector on a ground-truth folder and write the report:
    character and word error rates per engine, content class and capture type, the selector
    against the best single engine, line-detection coverage and seconds per page (the device
    in use, and the CPU). Numbers only: no text goes into the report. Only verified lines count
    as truth; the rest of the set is still read for timing and coverage."""
    from tarn_adapters.compute import CPU_ONLY, GPU_SLOT
    from tarn_adapters.groundtruth.store import DirectoryGroundTruthStore
    from tarn_adapters.ocr.bench import collect
    from tarn_adapters.ocr.wiring import build_ocr
    from tarn_core.services.ocr.benchmark import SELECTOR, build_report
    from tarn_core.services.ocr.benchmark_report import render
    from tarn_core.services.ocr.selector import Lexicon

    settings = get_settings()
    store = DirectoryGroundTruthStore(set_dir)
    if not store.page_ids():
        typer.echo("error: the folder has no pages (tarn truth prefill first)", err=True)
        raise typer.Exit(1)
    setup = _ocr_setup(settings)
    now = SystemClock().now()
    date = now.date().isoformat()

    def cpu_setup() -> "OcrSetup":
        GPU_SLOT.free()
        typer.echo("timing on the CPU")
        return build_ocr(settings, device=CPU_ONLY, layout=setup.layout)

    run = collect(
        setup,
        store,
        settings=settings,
        date=date,
        label=label,
        cpu_setup=cpu_setup if cpu_timing else None,
        cpu_pages=cpu_pages,
        progress=typer.echo,
    )
    report = build_report(run, lexicon=Lexicon(frozenset(), setup.word_list), now=now)
    name = f"ocr-{date}" + (f"-{label}" if label else "") + ".md"
    target = out or _repo_root() / "docs" / "benchmarks" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(report), encoding="utf-8")
    accuracy = report.accuracy
    if accuracy is None:
        typer.echo("no verified lines yet: report has timing, coverage and engine output only")
    else:
        overall = accuracy.methods[SELECTOR].groups.get("all")
        cer = overall.cer if overall is not None else None
        typer.echo(
            f"{accuracy.verified_lines} verified lines; selector CER "
            f"{'n/a' if cer is None else f'{cer * 100:.1f} %'}"
        )
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")
    typer.echo(f"report written to {target}")


@bench.command("segment")
def bench_segment(
    root: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    out: Annotated[
        Path | None,
        typer.Option(help="Report file (default: docs/benchmarks/segmentation-<date>.md)."),
    ] = None,
    compare_trigram: Annotated[
        bool, typer.Option(help="Also run the model-free trigram embedder.")
    ] = True,
    note: Annotated[
        list[str] | None, typer.Option(help="A finding to state in the report (repeatable).")
    ] = None,
) -> None:
    """Segment the sample booklets of a benchmark folder (one sub-folder per booklet with
    ``booklet.json``, optional ``truth.json``; the OCR is read once and cached there) against
    the seeded papers, and write the report: written order, answer starts, continuations and
    page order against the labelling. Question labels and counts only: no text."""
    from dataclasses import asdict

    from tarn_adapters.embed.wiring import TRIGRAM, build_embedder
    from tarn_adapters.segmentation.bench import discover, ensure_ocr, seeded_papers, segment
    from tarn_core.services.segmentation.benchmark import EmbedderRuns, render_report
    from tarn_core.services.segmentation.segmenter import SegmentationPolicy
    from tarn_core.services.segmentation.similarity import TrigramEmbedder

    settings = get_settings()
    booklets = discover(root, _repo_root())
    if not booklets:
        typer.echo("error: no booklet folders (with booklet.json) found", err=True)
        raise typer.Exit(1)
    papers = seeded_papers()
    setup_cache: list[OcrSetup] = []

    def ocr_setup() -> "OcrSetup":
        if not setup_cache:
            setup_cache.append(_ocr_setup(settings))
        return setup_cache[0]

    cached = {b.label: ensure_ocr(b, papers, ocr_setup, typer.echo) for b in booklets}
    policy = SegmentationPolicy()
    embedders = [build_embedder(settings).embedder]
    if compare_trigram and settings.embedding_model != TRIGRAM:
        embedders.append(TrigramEmbedder())
    compared = []
    for embedder in embedders:
        runs = [
            run
            for b in booklets
            if (run := segment(b, cached[b.label], papers, embedder, policy)) is not None
        ]
        compared.append(
            EmbedderRuns(name=f"{embedder.ref.name} {embedder.ref.version}", runs=tuple(runs))
        )
        typer.echo(f"{embedder.ref.name}: {len(runs)} booklets segmented")
    unlabelled = [
        f"{b.label}: no paper known, {len(cached[b.label])} pages read, not segmented"
        if b.paper is None
        else f"{b.label}: no labelling (truth.json), segmented but not scored"
        for b in booklets
        if b.paper is None or b.truth is None
    ]
    date = SystemClock().now().date().isoformat()
    labellers: dict[str, list[str]] = {}
    for b in booklets:
        if b.truth is not None:
            labellers.setdefault(b.labelled_by or "not stated", []).append(b.label)
    provenance = [
        f"Truth of {', '.join(names)}: {who}." for who, names in sorted(labellers.items())
    ]
    report = render_report(
        date=date,
        compared=compared,
        policy={k: v for k, v in asdict(policy).items() if isinstance(v, (int, float))},
        unlabelled=unlabelled,
        notes=[*provenance, *(note or [])],
    )
    target = out or _repo_root() / "docs" / "benchmarks" / f"segmentation-{date}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    typer.echo(f"report written to {target}")


# --- text scoring (P13) --------------------------------------------------------------------------


def _scoring_cache(settings: Settings) -> Path:
    return settings.model_dir / "huggingface"


@score_models.command("fetch")
def score_models_fetch(
    models: Annotated[
        list[str] | None,
        typer.Argument(help="Model ids (default: TARN_SCORING_EMBEDDING_MODEL)."),
    ] = None,
    candidates: Annotated[
        bool, typer.Option(help="Fetch every benchmark candidate (tarn score bench).")
    ] = False,
) -> None:
    """Download sentence-model weights into TARN_MODEL_DIR (model files only: no student text
    is sent), then load each from the local files and embed a fixed sentence."""
    import time

    from tarn_adapters.embed.sentence import SentenceTransformerEmbedder
    from tarn_adapters.embed.wiring import MODEL_ID, TRIGRAM
    from tarn_adapters.scoring.bench import CANDIDATES

    settings = get_settings()
    names = list(CANDIDATES) if candidates else models or [settings.scoring_embedding_model]
    cache_dir = _scoring_cache(settings)
    for name in names:
        if name == TRIGRAM:
            continue
        if not MODEL_ID.fullmatch(name):
            typer.echo(f"{name}: not a model id, skipped", err=True)
            continue
        started = time.perf_counter()
        try:
            SentenceTransformerEmbedder(name, cache_dir=cache_dir).embed(["Tarn model check"])
            local = SentenceTransformerEmbedder(name, cache_dir=cache_dir, local_files_only=True)
            local.embed(["Tarn model check"])
        except Exception as error:  # report and go on with the next model
            typer.echo(f"{name}: FAILED ({type(error).__name__}: {error})")
            continue
        typer.echo(
            f"{name}: {local.dimension} dimensions, ready offline "
            f"({time.perf_counter() - started:.1f}s)"
        )


@score.command("samples")
def score_samples(
    root: Annotated[
        Path, typer.Argument(exists=True, file_okay=False, help="The segmentation benchmark.")
    ] = Path("../var/segmentation"),
    out: Annotated[Path, typer.Option(help="The calibration set folder.")] = Path(
        "../var/scoring/samples"
    ),
) -> None:
    """Cut the sample booklets (cached OCR of `tarn bench segment`) into answers with the
    segmenter and write them as a calibration set (student data: stays under var/). Marks go
    in marks.json beside answers.jsonl."""
    from tarn_adapters.embed.wiring import build_embedder
    from tarn_adapters.scoring.samples import build_samples
    from tarn_adapters.segmentation.bench import seeded_papers

    settings = get_settings()
    path, count = build_samples(
        root, _repo_root(), out, seeded_papers(), build_embedder(settings).embedder, typer.echo
    )
    typer.echo(f"{count} answers written to {path}")


@score.command("import")
def score_import(
    dataset: Annotated[str, typer.Argument(help="Dataset: mohler.")],
    path: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    out: Annotated[
        Path | None, typer.Option(help="Set folder (default: ../var/scoring/<dataset>).")
    ] = None,
) -> None:
    """Turn a public human-marked dataset (var/datasets/, fetched by you after reading its
    terms) into a calibration set: questions.json + answers.jsonl with the human marks."""
    from tarn_adapters.scoring.mohler import import_mohler

    if dataset != "mohler":
        raise typer.BadParameter("known datasets: mohler")
    target = out or Path("../var/scoring") / dataset
    questions, answers = import_mohler(path, target)
    typer.echo(f"{questions} questions, {answers} marked answers written to {target}")


def _load_set(folder: Path) -> "CalibrationSet":
    from tarn_adapters.scoring.sets import load_set
    from tarn_core.testing import InMemory
    from tarn_core.testing.seed_world import seed_in_memory

    seeded = None
    if not (folder / "questions.json").exists():
        seeded = InMemory()
        seed_in_memory(seeded)
    data = load_set(folder, seeded)
    typer.echo(
        f"set {data.name}: {len(data.answers)} marked answers ({data.marked_by}), "
        f"{data.unmarked} unmarked, {len(data.questions)} questions"
    )
    if not data.answers:
        typer.echo("error: no marked answers in the set", err=True)
        raise typer.Exit(1)
    return data


def _word_list(settings: Settings) -> "WordList":
    from tarn_adapters.ocr.words import FileWordList

    return FileWordList.load(settings.english_words)


@score.command("bench")
def score_bench(
    set_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    model: Annotated[
        list[str] | None, typer.Option(help="Models to compare (default: every candidate).")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="Report file (default: docs/benchmarks/scoring-models-<date>.md)."),
    ] = None,
    note: Annotated[list[str] | None, typer.Option(help="A finding to state (repeatable).")] = None,
) -> None:
    """Compare sentence-embedding models on a calibration set (local weights only) and write
    the report: rank correlation with the marks, cross-validated and fitted mean absolute
    difference, time per answer. Question codes and numbers only."""
    from tarn_adapters.scoring.bench import CANDIDATES, bench
    from tarn_core.services.scoring.calibration import render_models

    settings = get_settings()
    data = _load_set(set_dir)
    results, baseline = bench(
        model or list(CANDIDATES),
        data,
        _scoring_cache(settings),
        typer.echo,
        word_list=_word_list(settings),
    )
    date = SystemClock().now().date().isoformat()
    notes = [
        f"Set `{data.name}`: {len(data.answers)} answers to {len(data.questions)} questions, "
        f"marked by {data.marked_by}. Models run on the CPU from local files.",
        "Cross-validated: the bands are fitted with each group of questions held out (5 groups) "
        "and measured on it; fitted: fitted and measured on every answer. Balanced MAE: the mean "
        "of the mean absolute differences of the low, middle and high thirds of the marks (the "
        "fitting objective); models are ranked by its cross-validated value.",
        *(note or []),
    ]
    report = render_models(f"Scoring models, {date}", notes, results, baseline)
    target = out or _repo_root() / "docs" / "benchmarks" / f"scoring-models-{date}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    typer.echo(f"report written to {target}")


@score.command("calibrate")
def score_calibrate(
    set_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    model: Annotated[
        str | None, typer.Option(help="Embedding model (default: TARN_SCORING_EMBEDDING_MODEL).")
    ] = None,
    max_flag_rate: Annotated[
        float, typer.Option(min=0.0, max=1.0, help="Most answers the flags may mark.")
    ] = 0.4,
    save: Annotated[
        bool, typer.Option(help="Store the fit as the next scoring calibration (owner role).")
    ] = False,
    out: Annotated[
        Path | None,
        typer.Option(help="Report file (default: docs/benchmarks/scoring-<set>-<date>.md)."),
    ] = None,
    note: Annotated[list[str] | None, typer.Option(help="A finding to state (repeatable).")] = None,
) -> None:
    """Fit the semantic credit bands and the flag thresholds on teacher-marked answers and
    report, per question, the mean absolute difference from the teacher and the share within
    ½ mark. With --save, store them as the next scoring calibration of the model (Tarn
    operator: uses the owner role, TARN_DATABASE_URL)."""
    from tarn_adapters.scoring.bench import local_embedder
    from tarn_core.domain.scoring import ScoringCalibration
    from tarn_core.services.scoring.calibration import accuracy, features, fit, render_accuracy

    settings = get_settings()
    name = model or settings.scoring_embedding_model
    data = _load_set(set_dir)
    embedder = local_embedder(name, _scoring_cache(settings))
    items = features(data.questions, data.answers, embedder, word_list=_word_list(settings))
    fitted = fit(items, max_flag_rate=max_flag_rate)
    report = accuracy(items, fitted.policy)
    p = fitted.policy
    typer.echo(
        f"bands {p.half:.3f} / {p.full:.3f}, margin {p.margin:.3f}, relevance "
        f"{p.relevance_min:.3f} / {p.relevance_soft:.3f}: MAE {report.overall.mean_abs_diff:.3f},"
        f" within ½ {report.overall.within_half:.0%}, flagged {fitted.rate:.0%}, "
        f"disagreements flagged {fitted.recall:.0%}"
    )
    date = SystemClock().now().date().isoformat()
    notes = [
        f"Set `{data.name}`: {len(data.answers)} answers to {len(data.questions)} questions, "
        f"marked by {data.marked_by}; model `{name}`. Fitted and measured on the same answers "
        "(in-sample): `tarn score bench` gives the cross-validated difference.",
        *(note or []),
    ]
    text = render_accuracy(
        f"Scoring accuracy, {data.name}, {date}", notes, report, p, flag_rate=fitted.rate
    )
    target = out or _repo_root() / "docs" / "benchmarks" / f"scoring-{data.name}-{date}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    typer.echo(f"report written to {target}")
    if not save:
        return
    from tarn_adapters.postgres.database import PostgresDatabase
    from tarn_adapters.runtime import UuidGenerator

    database = PostgresDatabase(settings.database_url, pool_size=1)
    try:
        with database.session(
            None, ids=UuidGenerator(), clock=SystemClock(), blobs=NoBlobStore()
        ) as session:
            ref_name = embedder.ref.name
            previous = session.scoring_calibrations.latest(ref_name)
            calibration = ScoringCalibration(
                embedder=ref_name,
                version=1 if previous is None else previous.version + 1,
                half=p.half,
                full=p.full,
                margin=p.margin,
                relevance_min=p.relevance_min,
                relevance_soft=p.relevance_soft,
                samples=len(items),
                mean_abs_diff=report.overall.mean_abs_diff,
                fitted_at=SystemClock().now(),
            )
            session.scoring_calibrations.save(calibration)
            typer.echo(f"saved scoring calibration {ref_name} v{calibration.version}")
    finally:
        database.dispose()


# --- diagrams (P14) ---------------------------------------------------------------------------


def _diagram_root() -> Path:
    return _repo_root() / "var" / "diagrams"


@diagram_data.command("prepare")
def diagram_data_prepare(
    fc: Annotated[
        Path | None,
        typer.Option(help="Unpacked FC database offline (default var/datasets/fc-offline)."),
    ] = None,
    fc3b: Annotated[
        Path | None, typer.Option(help="Flowchart 3b (default var/datasets/flowchart-3b).")
    ] = None,
) -> None:
    """Convert the downloaded public sets (scripts/datasets/fetch.py) into manifests under
    var/diagrams/data: FC_A and FC_B (with their graphs) and Flowchart 3b."""
    from tarn_adapters.diagram.dataset import write_manifest
    from tarn_adapters.diagram.sources import ImageSize, read_fc, read_voc

    datasets = _repo_root() / "var" / "datasets"
    out = _diagram_root() / "data"
    size = ImageSize()
    for name, folder, reader in (
        ("fc", fc or datasets / "fc-offline", read_fc),
        ("fc3b", fc3b or datasets / "flowchart-3b", read_voc),
    ):
        if not folder.is_dir():
            typer.echo(f"{name}: {folder} not found (fetch it with scripts/datasets/fetch.py)")
            continue
        samples = list(reader(folder, size))
        count = write_manifest(out / f"{name}.jsonl", samples)
        splits = {s: sum(1 for x in samples if x.split == s) for s in ("train", "val", "test")}
        objects = sum(len(x.objects) for x in samples)
        typer.echo(f"{name}: {count} images ({splits}), {objects} objects")


@diagram_data.command("synth")
def diagram_data_synth(
    count: Annotated[int, typer.Option(min=1, help="Drawings to make.")] = 3000,
    seed: Annotated[int, typer.Option(help="Same seed, same set.")] = 14,
) -> None:
    """Draw synthetic flowcharts, block diagrams, trees and networks with exact labels into
    var/diagrams/synthetic (80 % train, 10 % val, 10 % test)."""
    from tarn_adapters.diagram.synth import write_synthetic

    manifest = write_synthetic(_diagram_root() / "synthetic", count, seed=seed)
    typer.echo(f"{count} drawings, manifest {manifest}")


@diagram_data.command("pack")
def diagram_data_pack(
    max_side: Annotated[int, typer.Option(min=640, help="Longer image side in the bundle.")] = 1280,
) -> None:
    """Bundle the prepared images, one manifest and the training code into
    var/diagrams/colab-bundle.zip for a cloud GPU (scripts/colab/train_diagram_detector.ipynb).
    Public and synthetic material only."""
    import tarn_adapters
    import tarn_core
    from tarn_adapters.diagram.pack import pack

    manifests = _manifests()
    if not manifests:
        typer.echo("no data: run `tarn diagram data prepare` and/or `tarn diagram data synth`")
        raise typer.Exit(1)
    roots = [Path(tarn_core.__file__).parent, Path(tarn_adapters.__file__).parent]
    pack(manifests, _diagram_root() / "colab-bundle.zip", roots, max_side=max_side, log=typer.echo)


def _manifests() -> list[Path]:
    root = _diagram_root()
    found = [p for p in (root / "data" / "fc.jsonl", root / "data" / "fc3b.jsonl") if p.exists()]
    synthetic = root / "synthetic" / "manifest.jsonl"
    return [*found, *([synthetic] if synthetic.exists() else [])]


@diagram_models.command("fetch")
def diagram_models_fetch() -> None:
    """Download the detector's base weights (PekingU/rtdetr_r18vd_coco_o365, Apache-2.0) into
    TARN_MODEL_DIR. Model files only: nothing is sent."""
    from transformers import RTDetrForObjectDetection

    from tarn_adapters.diagram.train import BASE_MODEL

    settings = get_settings()
    model = RTDetrForObjectDetection.from_pretrained(
        BASE_MODEL, cache_dir=str(settings.model_dir / "huggingface")
    )
    params = sum(p.numel() for p in model.parameters()) / 1e6
    typer.echo(f"{BASE_MODEL}: {params:.1f} M parameters, cached")


@diagram.command("train")
def diagram_train(
    epochs: Annotated[int, typer.Option(min=1)] = 12,
    steps: Annotated[int, typer.Option(min=1, help="Optimiser steps per epoch.")] = 600,
    size: Annotated[int, typer.Option(min=320, help="Input side in pixels.")] = 1024,
    batch: Annotated[int, typer.Option(min=1)] = 2,
    accumulate: Annotated[int, typer.Option(min=1)] = 4,
    version: Annotated[str, typer.Option(help="Model version (folder shape-detector-v<N>).")] = "1",
) -> None:
    """Fine-tune the shape-and-arrow detector on the prepared manifests (resumes a stopped run
    from its checkpoint) and publish the best epoch to TARN_MODEL_DIR/diagram."""
    from tarn_adapters.diagram.train import TrainConfig, train

    settings = get_settings()
    manifests = _manifests()
    if not manifests:
        typer.echo("no data: run `tarn diagram data prepare` and/or `tarn diagram data synth`")
        raise typer.Exit(1)
    out = settings.model_dir / "diagram" / f"shape-detector-v{version}"
    cfg = TrainConfig(
        manifests=manifests,
        out=out,
        size=size,
        batch=batch,
        accumulate=accumulate,
        epochs=epochs,
        steps=steps,
        version=version,
        cache_dir=settings.model_dir / "huggingface",
    )
    typer.echo(f"published {train(cfg, log=typer.echo)}")


@diagram.command("publish")
def diagram_publish(
    source: Annotated[
        Path,
        typer.Argument(
            exists=True, file_okay=False, help="A training folder: model-best/ and checkpoint.pt."
        ),
    ],
    batch: Annotated[int, typer.Option(help="Batch the run used.")] = 8,
    steps: Annotated[int, typer.Option(help="Steps per epoch the run used.")] = 500,
    epochs: Annotated[int, typer.Option(help="Epochs the run planned.")] = 40,
    amp: Annotated[bool, typer.Option(help="The run used fp16 autocast.")] = True,
    version: Annotated[str, typer.Option()] = "1",
) -> None:
    """Publish the best epoch of a training run that did not finish (e.g. a Colab session cut
    off by its GPU limit) to TARN_MODEL_DIR/diagram/shape-detector-v<N>, with the manifest the
    finished run would have written (history from the checkpoint)."""
    import shutil

    import torch

    from tarn_adapters.diagram.dataset import read_manifest
    from tarn_adapters.diagram.train import TrainConfig, finish

    settings = get_settings()
    state = torch.load(source / "checkpoint.pt", map_location="cpu", weights_only=False)
    history = state["history"]
    out = settings.model_dir / "diagram" / f"shape-detector-v{version}"
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "model-best", out / "model-best", dirs_exist_ok=True)
    counts: dict[str, int] = {}
    for manifest in _manifests():
        for sample in read_manifest(manifest):
            if sample.split == "train":
                counts[sample.source] = counts.get(sample.source, 0) + 1
    cfg = TrainConfig(
        manifests=[],
        out=out,
        batch=batch,
        accumulate=1,
        epochs=epochs,
        steps=steps,
        version=version,
        amp=amp,
    )
    finish(cfg, history, counts, "cuda (Colab)")
    best = min(history, key=lambda h: h["val_loss"])
    typer.echo(
        f"published {out}: {len(history)} of {epochs} epochs run; best epoch {best['epoch']} "
        f"(val_loss {best['val_loss']})"
    )


@diagram.command("bench")
def diagram_bench(
    limit: Annotated[int, typer.Option(min=1, help="Test images per source at most.")] = 400,
    out: Annotated[Path | None, typer.Option(help="Report file.")] = None,
) -> None:
    """Measure the trained detector on the test splits and write
    docs/benchmarks/diagram-detector-<date>.md (precision, recall and AP per class; arrow
    ends; edges end to end; timing; GPU memory)."""
    import json
    from datetime import date

    from tarn_adapters.diagram.bench import evaluate, render_report
    from tarn_adapters.diagram.dataset import read_manifest
    from tarn_adapters.diagram.detector import MANIFEST, ShapeDetector
    from tarn_adapters.diagram.wiring import model_path

    settings = get_settings()
    folder = model_path(settings)
    if not (folder / MANIFEST).exists():
        typer.echo(f"no trained detector at {folder}")
        raise typer.Exit(1)
    device = detect_device(settings.device)
    detector = ShapeDetector(folder, device=device, threshold=settings.diagram_threshold)
    items = []
    for manifest in _manifests():
        per_source: dict[str, int] = {}
        for sample in read_manifest(manifest):
            if sample.split != "test" or per_source.get(sample.source, 0) >= limit:
                continue
            per_source[sample.source] = per_source.get(sample.source, 0) + 1
            items.append((manifest, sample))
    results = evaluate(detector, items, threshold=settings.diagram_threshold)
    text = render_report(
        results,
        model=folder.name,
        manifest=json.loads((folder / MANIFEST).read_text()),
        threshold=settings.diagram_threshold,
        device=f"{device.kind} ({device.name})",
        peak_gpu_gb=detector.peak_gpu_bytes / 1e9 if detector.peak_gpu_bytes else None,
    )
    target = out or _repo_root() / "docs" / "benchmarks" / f"diagram-detector-{date.today()}.md"
    target.write_text(text, encoding="utf-8")
    typer.echo(f"report written to {target}")


@diagram.command("recognize")
def diagram_recognize(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    labels: Annotated[bool, typer.Option(help="Read the labels with the OCR engines.")] = True,
) -> None:
    """Recognise one diagram image (a reference PNG) and print its graph JSON. Not for student
    pages outside the pipeline."""
    import json

    from tarn_adapters.diagram.wiring import build_recognizers
    from tarn_adapters.ocr.batch import page_ocr
    from tarn_core.domain.diagram import DiagramKind, DiagramText
    from tarn_core.services.diagrams.build import GraphBuilder
    from tarn_core.services.diagrams.graph_json import graph_to_json
    from tarn_core.services.diagrams.service import page_texts
    from tarn_core.services.ocr.selector import Lexicon

    settings = get_settings()
    setup = build_recognizers(settings)
    recognizer = setup.recognizers.get(DiagramKind.FLOWCHART)
    if recognizer is None:
        typer.echo(f"no recognizer: {setup.skipped}")
        raise typer.Exit(1)
    data = path.read_bytes()
    detection = recognizer.recognize(data)
    texts: list[DiagramText] = []
    engines: tuple[str, ...] = ()
    if labels:
        text = page_ocr(_ocr_setup(settings)).read(
            data, Lexicon(word_list=None), cuts=[s.box for s in detection.shapes]
        )
        texts, engines = page_texts(text), text.engines
    graph = GraphBuilder().build(detection, texts, recognizer=recognizer.ref, label_engines=engines)
    typer.echo(json.dumps(graph_to_json(graph), indent=2))


@diagram.command("references")
def diagram_references(
    every: Annotated[
        bool, typer.Option("--all", help="Also re-read diagrams already recognised or edited.")
    ] = False,
) -> None:
    """Recognise the reference diagrams that are still pending (or failed): what the worker's
    `diagram.reference` job does, for diagrams uploaded before P14 (the seed's PNGs). Each one
    is read in a session of its owning college and stored as the next version."""
    from tarn_adapters.blob.wiring import build_blob_store
    from tarn_adapters.diagram.wiring import build_recognizers
    from tarn_adapters.ocr.batch import page_ocr
    from tarn_adapters.postgres.database import PostgresDatabase
    from tarn_adapters.runtime import UuidGenerator
    from tarn_core.domain.content import ReferenceDiagram
    from tarn_core.domain.diagram import RecognitionState
    from tarn_core.services.diagrams.service import ReferenceDiagrams
    from tarn_core.services.question_bank import QuestionBankService

    settings = get_settings()
    setup = build_recognizers(settings)
    if not setup.recognizers:
        typer.echo(f"no recognizer: {setup.skipped}")
        raise typer.Exit(1)
    labels = page_ocr(_ocr_setup(settings))
    blobs = build_blob_store(settings)
    database = PostgresDatabase(settings.app_database_url, pool_size=1)
    ids, clock = UuidGenerator(), SystemClock()
    try:
        with database.session(None, ids=ids, clock=clock, blobs=blobs) as s:
            todo = [
                d
                for d in s.content.latest(ReferenceDiagram)
                if every or d.recognition in (RecognitionState.PENDING, RecognitionState.FAILED)
            ]
        for d in todo:
            owner = d.meta.owning_college_id
            with database.session(owner, ids=ids, clock=clock, blobs=blobs) as s:
                bank = QuestionBankService(
                    content=s.content,
                    users=s.users,
                    colleges=s.colleges,
                    blobs=blobs,
                    runtime=s.runtime,
                )
                new = ReferenceDiagrams(
                    content=s.content,
                    blobs=blobs,
                    runtime=s.runtime,
                    recognizers=setup.recognizers,
                    labels=labels,
                    glossary=bank,
                ).recognize(owner, d.meta.created_by, d.id)
            typer.echo(
                f"{d.id} ({d.kind.value}): {new.recognition.value}, {len(new.graph.nodes)} nodes, "
                f"{len(new.graph.edges)} edges, v{new.meta.version}"
            )
    finally:
        database.dispose()
