"""The job queue on PostgreSQL (D61): ``JobQueue`` over the ``jobs`` table.

*Enqueue* runs in a college transaction (so a booklet and its job commit together, and row
security keeps the job in its college). *Claim, heartbeat, succeed, fail, reap* run in a
scheduler transaction (``PostgresDatabase.scheduler()``): no college is set, ``app.scheduler``
is, and the second policy of ``jobs`` lets it see every college's rows.

Claiming is one statement under an advisory lock: the job of the college served least recently
first (round robin across colleges, oldest job within one), only when fewer than
``max_running`` jobs hold a live lease. A job that stops heartbeating becomes claimable again
when its lease ends, until its attempts are used up; failures are retried after a backoff that
doubles with each attempt."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Connection, text

from tarn_core.domain.common import JsonValue
from tarn_core.errors import NotFoundError
from tarn_core.ids import CollegeId, JobId
from tarn_core.ports.jobs import Job

_CLAIM_LOCK = 7_140_001  # arbitrary advisory-lock key: claimers take turns


@dataclass(frozen=True, slots=True)
class JobSettings:
    max_attempts: int = 3
    lease_seconds: float = 120.0
    backoff_seconds: float = 5.0
    max_backoff_seconds: float = 300.0
    max_running: int = 1


def _job(row: Any) -> Job:
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    return Job(
        id=JobId(row.id),
        college_id=CollegeId(row.college_id),
        kind=row.kind,
        payload=payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
    )


class PgJobQueue:
    def __init__(self, conn: Connection, settings: JobSettings | None = None) -> None:
        self._conn = conn
        self._s = settings or JobSettings()

    def enqueue(
        self,
        college_id: CollegeId,
        kind: str,
        payload: Mapping[str, JsonValue],
        *,
        key: str | None = None,
    ) -> JobId:
        job_id = uuid4()
        row = self._conn.execute(
            text(
                """
                INSERT INTO jobs (id, college_id, kind, payload, dedupe_key, status, max_attempts)
                VALUES (:id, :college, :kind, CAST(:payload AS jsonb), :key, 'queued', :max)
                ON CONFLICT (college_id, dedupe_key)
                    WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'running')
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "id": job_id,
                "college": college_id,
                "kind": kind,
                "payload": json.dumps(dict(payload)),
                "key": key,
                "max": self._s.max_attempts,
            },
        ).first()
        if row is not None:
            return JobId(row.id)
        existing = self._conn.execute(
            text(
                "SELECT id FROM jobs WHERE college_id = :college AND dedupe_key = :key "
                "AND status IN ('queued', 'running')"
            ),
            {"college": college_id, "key": key},
        ).first()
        if existing is None:  # the live job finished between the two statements
            return self.enqueue(college_id, kind, payload, key=key)
        return JobId(existing.id)

    def claim(self, worker: str) -> Job | None:
        self._conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CLAIM_LOCK})
        row = self._conn.execute(
            text(
                """
                UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_by = :worker,
                    locked_until = now() + make_interval(secs => :lease), started_at = now()
                WHERE id = (
                    SELECT j.id FROM jobs j
                    WHERE (SELECT count(*) FROM jobs r
                           WHERE r.status = 'running' AND r.locked_until > now()) < :running
                      AND ((j.status = 'queued' AND j.run_after <= now())
                           OR (j.status = 'running' AND j.locked_until <= now()
                               AND j.attempts < j.max_attempts))
                    ORDER BY (SELECT max(s.started_at) FROM jobs s
                              WHERE s.college_id = j.college_id) NULLS FIRST, j.seq
                    FOR UPDATE OF j SKIP LOCKED
                    LIMIT 1)
                RETURNING id, college_id, kind, payload, attempts, max_attempts
                """
            ),
            {"worker": worker, "lease": self._s.lease_seconds, "running": self._s.max_running},
        ).first()
        return None if row is None else _job(row)

    def reap(self) -> list[Job]:
        """Jobs whose worker vanished on their last attempt: finished as failed and returned,
        so the caller can end whatever they were working on."""
        rows = self._conn.execute(
            text(
                """
                UPDATE jobs SET status = 'failed', finished_at = now(), last_error = 'lease expired'
                WHERE status = 'running' AND locked_until <= now() AND attempts >= max_attempts
                RETURNING id, college_id, kind, payload, attempts, max_attempts
                """
            )
        ).all()
        return [_job(r) for r in rows]

    def heartbeat(self, job_id: JobId) -> None:
        self._one(
            "UPDATE jobs SET locked_until = now() + make_interval(secs => :lease) "
            "WHERE id = :id AND status = 'running'",
            {"id": job_id, "lease": self._s.lease_seconds},
            job_id,
        )

    def succeed(self, job_id: JobId) -> None:
        self._one(
            "UPDATE jobs SET status = 'succeeded', finished_at = now(), locked_until = NULL "
            "WHERE id = :id AND status = 'running'",
            {"id": job_id},
            job_id,
        )

    def fail(self, job_id: JobId, error: str, *, retry: bool) -> bool:
        row = self._conn.execute(
            text(
                """
                UPDATE jobs SET last_error = :error, locked_until = NULL,
                    status = CASE WHEN :retry AND attempts < max_attempts
                                  THEN 'queued' ELSE 'failed' END,
                    run_after = CASE WHEN :retry AND attempts < max_attempts
                        THEN now() + make_interval(
                            secs => least(:backoff * power(2, attempts - 1), :cap))
                        ELSE run_after END,
                    finished_at = CASE WHEN :retry AND attempts < max_attempts
                                       THEN NULL ELSE now() END
                WHERE id = :id AND status = 'running'
                RETURNING status
                """
            ),
            {
                "id": job_id,
                "error": error[:200],
                "retry": retry,
                "backoff": self._s.backoff_seconds,
                "cap": self._s.max_backoff_seconds,
            },
        ).first()
        if row is None:
            raise NotFoundError(f"running job {job_id}")
        return bool(row.status == "queued")

    def _one(self, sql: str, params: dict[str, Any], job_id: UUID) -> None:
        if self._conn.execute(text(sql), params).rowcount != 1:
            raise NotFoundError(f"running job {job_id}")
