"""Smoke test for tarn_worker."""

import threading

from tarn_adapters.config import Settings
from tarn_worker.main import run


def test_run_returns_once_stop_is_set() -> None:
    stop = threading.Event()
    stop.set()
    run(Settings(_env_file=None, device="cpu"), stop, heartbeat_s=0.01)
