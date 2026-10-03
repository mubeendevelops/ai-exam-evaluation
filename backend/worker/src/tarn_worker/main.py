"""Worker entry point: ``python -m tarn_worker`` or ``tarn-worker``.

For now it only reports the compute device and idles until SIGTERM/SIGINT. The
PostgreSQL-backed job queue (procrastinate, confirmed in P9) replaces the idle loop.
"""

import signal
import threading
from types import FrameType

import structlog

from tarn_adapters.compute import detect_device
from tarn_adapters.config import Settings, get_settings
from tarn_adapters.logging_setup import configure_logging

HEARTBEAT_SECONDS = 60.0


def run(settings: Settings, stop: threading.Event, heartbeat_s: float = HEARTBEAT_SECONDS) -> None:
    log = structlog.get_logger("tarn_worker")
    device = detect_device(settings.device)
    log.info("worker.started", device=device.kind, device_name=device.name, detail=device.detail)
    while not stop.wait(heartbeat_s):
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
    run(settings, stop)
