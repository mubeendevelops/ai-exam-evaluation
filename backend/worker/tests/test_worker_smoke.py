"""Smoke test for tarn_worker."""

import threading

from tarn_adapters.config import Settings
from tarn_worker.main import run


def test_run_returns_once_stop_is_set() -> None:
    stop = threading.Event()
    stop.set()
    run(Settings(_env_file=None, device="cpu"), stop, heartbeat_s=0.01)


def test_periodic_job_runs_once_per_interval_and_survives_failures() -> None:
    from datetime import timedelta

    from tarn_core.testing import FixedClock
    from tarn_worker.main import PeriodicJob, scheduled_jobs

    clock = FixedClock()
    calls: list[int] = []

    def action() -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("boom")

    job = PeriodicJob(name="t", interval=timedelta(hours=24), action=action, clock=clock)
    assert job.run_if_due()  # first check runs at once
    assert not job.run_if_due()
    clock.advance(hours=24)
    assert job.run_if_due()  # raises inside, logged, worker keeps going
    clock.advance(hours=23)
    assert not job.run_if_due()
    assert len(calls) == 2
    settings = Settings(_env_file=None, identity_backup_interval_hours=0)
    assert scheduled_jobs(settings, clock) == []
    assert [j.name for j in scheduled_jobs(Settings(_env_file=None), clock)] == ["identity_backup"]


def test_run_starts_due_jobs() -> None:
    from datetime import timedelta

    from tarn_core.testing import FixedClock
    from tarn_worker.main import PeriodicJob

    ran: list[str] = []
    job = PeriodicJob(
        name="t", interval=timedelta(hours=1), action=lambda: ran.append("x"), clock=FixedClock()
    )
    stop = threading.Event()
    stop.set()
    run(Settings(_env_file=None, device="cpu"), stop, heartbeat_s=0.01, jobs=[job])
    assert ran == ["x"]
