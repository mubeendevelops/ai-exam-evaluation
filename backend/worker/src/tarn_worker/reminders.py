"""The daily due-date sweep (P37, D242): queue one ``notify.due`` job per college, and run one.

``queue_reminders`` is called by the worker's daily ``PeriodicJob`` (development, worker VM) and
by ``python -m tarn_worker.job --reminders`` (Cloud Scheduler on Google Cloud). Each college's
job is enqueued in that college's own session with the key ``notify.due:<UTC date>``, so a
second call on the same day adds no job while the first is waiting, and the queue serves the
colleges in turn like every other job. Running a job twice adds no notice either: the notices
are unique per unit, kind and day (``ReminderSweep``)."""

from datetime import UTC

import structlog

from tarn_adapters.postgres.database import PostgresDatabase, PostgresSession
from tarn_core.ids import CollegeId
from tarn_core.ports.identity import Mailer
from tarn_core.ports.jobs import JOB_NOTIFY_DUE
from tarn_core.ports.runtime import Clock, IdGenerator
from tarn_core.ports.storage import BlobStore
from tarn_core.services.reminders import ReminderSweep, SweepResult


def reminder_key(clock: Clock) -> str:
    return f"{JOB_NOTIFY_DUE}:{clock.now().astimezone(UTC).date().isoformat()}"


def queue_reminders(
    db: PostgresDatabase, *, clock: Clock, ids: IdGenerator, blobs: BlobStore
) -> int:
    """Queue today's sweep for every college; how many colleges. A college that fails is
    logged and skipped (the next day's run catches up: notices are written for the window)."""
    log = structlog.get_logger("tarn_worker")
    key = reminder_key(clock)
    queued = 0
    for college_id in db.college_ids():
        try:
            with db.session(college_id, ids=ids, clock=clock, blobs=blobs) as session:
                session.jobs.enqueue(college_id, JOB_NOTIFY_DUE, {}, key=key)
            queued += 1
        except Exception as error:
            log.error("reminders.queue_failed", college=str(college_id), error=type(error).__name__)
    log.info("reminders.queued", colleges=queued)
    return queued


def sweep(session: PostgresSession, college_id: CollegeId, mailer: Mailer) -> SweepResult:
    return ReminderSweep(
        units=session.units,
        users=session.users,
        content=session.content,
        notifications=session.notifications,
        mailer=mailer,
        runtime=session.runtime,
    ).run(college_id)
