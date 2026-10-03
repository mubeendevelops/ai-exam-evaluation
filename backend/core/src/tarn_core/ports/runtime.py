"""Ports for time, identity generation, background jobs and the audit log."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from tarn_core.domain.audit import AuditEvent
from tarn_core.domain.common import JsonValue
from tarn_core.ids import CollegeId, JobId


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC."""
        ...


class IdGenerator(Protocol):
    def new(self) -> UUID: ...


class JobQueue(Protocol):
    """Run work later, retry on failure. Jobs belong to one college."""

    def enqueue(
        self, college_id: CollegeId, kind: str, payload: Mapping[str, JsonValue]
    ) -> JobId: ...


class AuditSink(Protocol):
    """Append only: there is no update or delete."""

    def append(self, event: AuditEvent) -> None: ...
