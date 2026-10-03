"""Smoke test for tarn_worker."""

import threading
from collections.abc import Iterator

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


def test_health_endpoint_answers_while_the_worker_runs() -> None:
    import json
    import urllib.error
    import urllib.request

    from tarn_worker.health import start_health_server

    server = start_health_server("127.0.0.1", 0, "cpu")
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
            assert json.load(response) == {"status": "ok", "device": "cpu"}
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=3)
        except urllib.error.HTTPError as err:
            assert err.code == 404
        else:  # pragma: no cover
            raise AssertionError("expected 404")
    finally:
        server.shutdown()
        server.server_close()


def test_run_takes_queued_work_until_none_is_left_and_survives_a_failing_tick() -> None:
    settings = Settings(_env_file=None, device="cpu", worker_poll_seconds=0.01)
    stop = threading.Event()
    answers: Iterator[bool | RuntimeError] = iter(
        [True, True, RuntimeError("database down"), False, False]
    )
    seen: list[str] = []

    def tick() -> bool:
        answer = next(answers, None)
        if answer is None:
            stop.set()
            return False
        if isinstance(answer, RuntimeError):
            seen.append("error")
            raise answer
        seen.append("busy" if answer else "idle")
        return bool(answer)

    run(settings, stop, heartbeat_s=60, tick=tick)
    # Two busy ticks are taken back to back, the failing one is logged and the loop goes on.
    assert seen[:4] == ["busy", "busy", "error", "idle"]
    assert stop.is_set()


def test_run_stops_after_the_job_in_hand_when_asked() -> None:
    settings = Settings(_env_file=None, device="cpu", worker_poll_seconds=5)
    stop = threading.Event()
    calls = []

    def tick() -> bool:
        calls.append(1)
        stop.set()  # SIGTERM arrives while a job runs
        return True

    run(settings, stop, heartbeat_s=60, tick=tick)
    assert calls == [1]  # it did not start another
