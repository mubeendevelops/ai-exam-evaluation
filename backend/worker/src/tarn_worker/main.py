"""Worker entry point: ``python -m tarn_worker`` or ``tarn-worker``.

The worker reports the compute device, serves its health endpoint, runs its scheduled jobs and
takes booklet jobs from the PostgreSQL queue one at a time (page cleaning, P9), until
SIGTERM/SIGINT. A job in progress finishes its current step before the worker stops.

Scheduled jobs: the encrypted identity backup of every tenant
(``TARN_IDENTITY_BACKUP_INTERVAL_HOURS``, 0 = off)."""

import signal
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import FrameType
from uuid import uuid4

import structlog

from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.logging_setup import configure_logging
from tarn_adapters.runtime import SystemClock
from tarn_core.ports.runtime import Clock
from tarn_worker.health import start_health_server

HEARTBEAT_SECONDS = 60.0


@dataclass
class PeriodicJob:
    """Runs ``action`` when ``interval`` has passed since its last start (at once on the first
    check). A failing run is logged and retried at the next interval."""

    name: str
    interval: timedelta
    action: Callable[[], None]
    clock: Clock
    last_run: datetime | None = field(default=None)

    def run_if_due(self) -> bool:
        now = self.clock.now()
        if self.last_run is not None and now - self.last_run < self.interval:
            return False
        self.last_run = now
        log = structlog.get_logger("tarn_worker")
        try:
            self.action()
            log.info("job.done", job=self.name)
        except Exception as exc:  # keep the worker alive; the next interval retries
            log.error("job.failed", job=self.name, error=type(exc).__name__)
        return True


def identity_backup_job(settings: Settings, clock: Clock) -> PeriodicJob:
    def backup() -> None:
        from tarn_adapters.auth.crypto import AesGcmCipher
        from tarn_adapters.auth.wiring import key_manager
        from tarn_adapters.identity.backup import export_all
        from tarn_adapters.identity.database import IdentityDatabase

        db = IdentityDatabase(settings.identity_app_database_url, pool_size=1)
        try:
            run = export_all(
                db,
                keys=key_manager(settings),
                cipher=AesGcmCipher(),
                clock=clock,
                out_dir=settings.identity_backup_dir,
                keep=settings.identity_backup_keep,
            )
        finally:
            db.dispose()
        log = structlog.get_logger("tarn_worker")
        # Ids and counts only: no names or emails in logs.
        log.info("identity_backup.written", tenants=len(run.written), failed=len(run.failed))
        if run.failed:
            raise RuntimeError(f"{len(run.failed)} tenant backups failed")

    return PeriodicJob(
        name="identity_backup",
        interval=timedelta(hours=settings.identity_backup_interval_hours),
        action=backup,
        clock=clock,
    )


def scheduled_jobs(settings: Settings, clock: Clock) -> list[PeriodicJob]:
    jobs = []
    if settings.identity_backup_interval_hours > 0:
        jobs.append(identity_backup_job(settings, clock))
    return jobs


def booklet_runner(settings: Settings, clock: Clock) -> Callable[[], bool]:
    """The queue consumer: each call processes at most one booklet job; True if it did."""
    from tarn_adapters.blob.minio_client import make_client
    from tarn_adapters.blob.minio_store import MinioBlobStore
    from tarn_adapters.imaging.cleaner import OpenCvPageCleaner
    from tarn_adapters.imaging.pdf import PyMuPdfSplitter
    from tarn_adapters.ocr.wiring import build_ocr
    from tarn_adapters.postgres.database import PostgresDatabase
    from tarn_adapters.postgres.jobs import JobSettings
    from tarn_adapters.runtime import UuidGenerator
    from tarn_core.errors import EngineFailedError
    from tarn_core.services.pipeline import QualityPolicy
    from tarn_worker.booklets import BookletJobRunner, OcrKit

    log = structlog.get_logger("tarn_worker")
    kit: OcrKit | None = None
    try:
        ocr = build_ocr(settings)
    except EngineFailedError as error:
        # Page cleaning still runs; reading jobs fail visibly until OCR is available.
        log.error("ocr.unavailable", reason=str(error))
    else:
        log.info(
            "ocr.engines",
            engines=list(ocr.engines),
            skipped=ocr.skipped,
            device=ocr.device.kind,
            layout=f"{ocr.layout.ref.name} {ocr.layout.ref.version}",
        )
        if not ocr.engines:
            log.error("ocr.no_engines", skipped=ocr.skipped)
        kit = OcrKit(
            layout=ocr.layout,
            engines=ocr.engines,
            transform=ocr.transform,
            word_list=ocr.word_list,
            settings=ocr.settings,
            orientation=ocr.orientation,
        )

    runner = BookletJobRunner(
        db=PostgresDatabase(
            settings.app_database_url,
            pool_size=3,
            job_settings=JobSettings(
                max_attempts=settings.job_max_attempts,
                lease_seconds=settings.job_lease_seconds,
                backoff_seconds=settings.job_backoff_seconds,
            ),
        ),
        blobs=MinioBlobStore(make_client(settings), settings.blob_bucket),
        splitter=PyMuPdfSplitter(),
        cleaner=OpenCvPageCleaner(
            max_edge_px=settings.page_max_edge_px, max_bytes=settings.page_max_bytes
        ),
        clock=clock,
        ids=UuidGenerator(),
        policy=QualityPolicy(
            min_sharpness=settings.quality_min_sharpness,
            max_glare_share=settings.quality_max_glare_share,
            min_page_edge_px=settings.quality_min_page_edge_px,
        ),
        max_pages=settings.upload_max_pages,
        worker=f"worker-{uuid4().hex[:8]}",
        ocr=kit,
        heartbeat_seconds=settings.job_lease_seconds / 3,
    )
    return runner.run_one


def _safe(tick: Callable[[], bool]) -> bool:
    """A failing tick (the database is down) is logged and retried at the next poll."""
    try:
        return tick()
    except Exception as exc:
        structlog.get_logger("tarn_worker").error("queue.tick_failed", error=type(exc).__name__)
        return False


def run(
    settings: Settings,
    stop: threading.Event,
    heartbeat_s: float = HEARTBEAT_SECONDS,
    jobs: Sequence[PeriodicJob] = (),
    tick: Callable[[], bool] | None = None,
) -> None:
    log = structlog.get_logger("tarn_worker")
    device = detect_device(settings.device)
    log.info("worker.started", device=device.kind, device_name=device.name, detail=device.detail)
    health = (
        start_health_server(settings.worker_health_host, settings.worker_health_port, device.kind)
        if settings.worker_health_port > 0
        else None
    )
    quiet_since = 0.0
    try:
        while True:
            for job in jobs:
                job.run_if_due()
            if tick is not None and _safe(tick):
                if stop.is_set():
                    break
                continue  # more may be waiting: look again at once
            if tick is None:
                if stop.wait(heartbeat_s):
                    break
                log.info("worker.idle")
                continue
            if stop.wait(settings.worker_poll_seconds):
                break
            quiet_since += settings.worker_poll_seconds
            if quiet_since >= heartbeat_s:
                quiet_since = 0.0
                log.info("worker.idle")
    finally:
        if health is not None:
            health.shutdown()
            health.server_close()
    log.info("worker.stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")
    stop = threading.Event()

    def _request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    clock = SystemClock()
    run(settings, stop, jobs=scheduled_jobs(settings, clock), tick=booklet_runner(settings, clock))
