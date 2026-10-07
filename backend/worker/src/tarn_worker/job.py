"""Cloud Run job entry point: ``python -m tarn_worker.job`` (or ``tarn-worker-job``).

A Cloud Run *job* runs a container to completion, unlike the always-on worker service of the
development stack. This entry builds the same queue consumer as ``tarn_worker.main`` and takes
booklet jobs from the PostgreSQL queue, one at a time, until the queue has been empty for
``TARN_WORKER_JOB_IDLE_SECONDS`` (default 0: exit as soon as nothing is waiting) or
``TARN_WORKER_JOB_MAX_SECONDS`` have passed, then exits with code 0. Cloud Scheduler starts the
job on a schedule, and the API or an event can start it when a booklet is queued. A SIGTERM
(Cloud Run stopping the task) finishes the step in progress and exits too; the queue's leases
hand an unfinished job to the next run, so nothing is lost.

The scheduled identity backup is not run here (a job has no persistent disk): the identity
database is backed up by Cloud SQL (daily backups with point-in-time recovery). The health
endpoint is not served either: Cloud Run reports the task's state."""

import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType

import structlog

from tarn_adapters.config import Settings, get_settings
from tarn_adapters.logging_setup import configure_logging
from tarn_adapters.runtime import SystemClock


@dataclass(frozen=True, slots=True)
class JobSummary:
    processed: int
    """Queue ticks that did work (a job's stage, or a reaped lease)."""
    reason: str
    """Why the job ended: ``idle`` (queue empty), ``time`` (the time limit), ``stopped`` or
    ``unavailable`` (the queue never answered: a failed task, so Cloud Run retries it)."""


def drain(
    tick: Callable[[], bool],
    *,
    stop: threading.Event,
    max_seconds: float,
    idle_seconds: float = 0.0,
    poll_seconds: float = 2.0,
    now: Callable[[], float] = time.monotonic,
) -> JobSummary:
    """Call ``tick`` (one queue step; True when it did something) until the queue has been empty
    for ``idle_seconds``, ``max_seconds`` have passed, or ``stop`` is set. A tick that raises
    (the database is down) is logged and counted as empty, so a short outage does not end the
    job early; if the queue never answered by the end, the reason is ``unavailable``."""
    log = structlog.get_logger("tarn_worker")
    started = now()
    quiet_since: float | None = None
    processed = 0
    answered = False
    failed = False

    def ended(reason: str) -> JobSummary:
        return JobSummary(processed, "unavailable" if failed and not answered else reason)

    while True:
        if stop.is_set():
            return ended("stopped")
        if now() - started >= max_seconds:
            return ended("time")
        try:
            did = tick()
            answered = True
        except Exception as exc:
            log.error("queue.tick_failed", error=type(exc).__name__)
            did, failed = False, True
        if did:
            processed += 1
            quiet_since = None
            continue
        if quiet_since is None:
            quiet_since = now()
        if now() - quiet_since >= idle_seconds:
            return ended("idle")
        stop.wait(poll_seconds)


def run_job(settings: Settings, tick: Callable[[], bool], stop: threading.Event) -> JobSummary:
    log = structlog.get_logger("tarn_worker")
    log.info("job.started", max_seconds=settings.worker_job_max_seconds)
    summary = drain(
        tick,
        stop=stop,
        max_seconds=settings.worker_job_max_seconds,
        idle_seconds=settings.worker_job_idle_seconds,
        poll_seconds=settings.worker_poll_seconds,
    )
    log.info("job.finished", processed=summary.processed, reason=summary.reason)
    return summary


def main() -> None:
    from tarn_worker.main import booklet_runner

    settings = get_settings()
    configure_logging(settings.log_level, json=settings.env == "production")
    stop = threading.Event()

    def _request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    summary = run_job(settings, booklet_runner(settings, SystemClock()), stop)
    sys.exit(1 if summary.reason == "unavailable" else 0)


if __name__ == "__main__":
    main()
