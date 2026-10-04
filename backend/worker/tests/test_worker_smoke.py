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


def test_a_worker_without_ocr_fails_reading_jobs_and_finally_their_booklets() -> None:
    from dataclasses import replace
    from types import SimpleNamespace
    from typing import Any, cast

    import pytest

    from tarn_core.domain.booklet import BookletStatus
    from tarn_core.ports.jobs import JOB_PREPARE_BOOKLET, JOB_READ_BOOKLET
    from tarn_core.testing import InMemory
    from tarn_core.testing.builders import add_college, ci_shaped_blueprint, make_services
    from tarn_worker.booklets import BookletJobRunner, OcrUnavailableError

    runner = BookletJobRunner(
        db=cast(Any, None),
        blobs=cast(Any, None),
        splitter=cast(Any, None),
        cleaner=cast(Any, None),
        clock=cast(Any, None),
        ids=cast(Any, None),
        policy=cast(Any, None),
        max_pages=1,
        ocr=None,
    )
    assert runner._stepper(JOB_PREPARE_BOOKLET) is not None
    no_ocr = runner._stepper(JOB_READ_BOOKLET)
    assert no_ocr is not None

    mem = InMemory()
    college = add_college(mem, "A")
    blueprint = ci_shaped_blueprint(mem, college)
    booklet = (
        make_services(mem)
        .booklets.register(
            college.id,
            college.teacher.id,
            student_id=college.students[0].id,
            blueprint_id=blueprint.id,
            file_sha256="a" * 64,
        )
        .booklet
    )
    mem.booklets.save(college.id, replace(booklet, status=BookletStatus.PAGES_READY, version=2))
    stepper = no_ocr(cast(Any, SimpleNamespace(booklets=mem.booklets, runtime=mem.runtime)))
    with pytest.raises(OcrUnavailableError):
        stepper.step(college.id, booklet.id)
    stepper.abandon(college.id, booklet.id)
    failed = mem.booklets.get(college.id, booklet.id)
    assert failed.status is BookletStatus.FAILED and failed.failure_reason == "reading_failed"
