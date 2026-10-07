"""The Cloud Run job entry: take queued work until the queue is empty or time runs out."""

import threading
from collections.abc import Callable

import pytest

from tarn_adapters.config import Settings
from tarn_worker.job import JobSummary, drain, run_job


class Clock:
    """A clock that moves one second each time it is read."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        self.t += 1.0
        return self.t


def script(*answers: bool | Exception) -> Callable[[], bool]:
    queue = list(answers)

    def tick() -> bool:
        if not queue:
            return False
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return tick


def run(tick: Callable[[], bool], **kw: float) -> JobSummary:
    return drain(
        tick,
        stop=kw.pop("stop", threading.Event()),  # type: ignore[arg-type]
        max_seconds=kw.pop("max_seconds", 1000.0),
        poll_seconds=0.0,
        now=Clock(),
        **kw,
    )


def test_an_empty_queue_ends_the_job_at_once() -> None:
    assert run(script()) == JobSummary(0, "idle")


def test_it_works_until_nothing_is_left() -> None:
    assert run(script(True, True, True)) == JobSummary(3, "idle")


def test_it_waits_for_late_work_when_asked_to() -> None:
    # empty, empty, then work arrives inside the idle window, then quiet for good
    summary = run(script(False, False, True), idle_seconds=5.0)
    assert summary == JobSummary(1, "idle")
    assert run(script(False, True), idle_seconds=0.0) == JobSummary(0, "idle")  # no waiting


def test_the_time_limit_ends_a_busy_job() -> None:
    assert run(lambda: True, max_seconds=10.0).reason == "time"


def test_a_stop_signal_ends_the_job_between_steps() -> None:
    stop = threading.Event()
    stop.set()
    assert run(script(True), stop=stop) == JobSummary(0, "stopped")  # type: ignore[arg-type]


def test_a_short_outage_does_not_end_the_job_but_a_dead_queue_fails_it() -> None:
    blip = script(RuntimeError("db down"), True, True)
    assert run(blip, idle_seconds=2.0) == JobSummary(2, "idle")  # the blip did not end the job
    summary = run(lambda: (_ for _ in ()).throw(ConnectionError("down")), max_seconds=5.0)
    assert summary == JobSummary(0, "unavailable")  # Cloud Run sees a failed task and retries


def test_run_job_uses_the_settings_limits() -> None:
    settings = Settings(_env_file=None, worker_job_max_seconds=0.001, worker_poll_seconds=0.001)
    calls: list[int] = []

    def tick() -> bool:
        calls.append(1)
        return True

    summary = run_job(settings, tick, threading.Event())
    assert summary.reason == "time" and summary.processed == len(calls)
    with pytest.raises(ValueError):
        Settings(_env_file=None, worker_job_max_seconds=0)
