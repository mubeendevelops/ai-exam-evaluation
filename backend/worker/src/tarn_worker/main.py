"""Worker entry point: ``python -m tarn_worker`` or ``tarn-worker``.

Until the PostgreSQL-backed job queue arrives (procrastinate, confirmed in P9) the worker
reports the compute device, runs its scheduled jobs and idles until SIGTERM/SIGINT.

Scheduled jobs: the encrypted identity backup of every tenant
(``TARN_IDENTITY_BACKUP_INTERVAL_HOURS``, 0 = off)."""

import signal
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import FrameType

import structlog

from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.logging_setup import configure_logging
from tarn_adapters.runtime import SystemClock
from tarn_core.ports.runtime import Clock

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


def run(
    settings: Settings,
    stop: threading.Event,
    heartbeat_s: float = HEARTBEAT_SECONDS,
    jobs: Sequence[PeriodicJob] = (),
) -> None:
    log = structlog.get_logger("tarn_worker")
    device = detect_device(settings.device)
    log.info("worker.started", device=device.kind, device_name=device.name, detail=device.detail)
    while True:
        for job in jobs:
            job.run_if_due()
        if stop.wait(heartbeat_s):
            break
        log.info("worker.idle")
    log.info("worker.stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")
    stop = threading.Event()

    def _request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    run(settings, stop, jobs=scheduled_jobs(settings, SystemClock()))
