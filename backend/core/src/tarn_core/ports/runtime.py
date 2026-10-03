"""Ports for time, identity generation and the audit log."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from tarn_core.domain.audit import AuditEvent


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware UTC."""
        ...


class IdGenerator(Protocol):
    def new(self) -> UUID: ...


class AuditSink(Protocol):
    """Append only: there is no update or delete."""

    def append(self, event: AuditEvent) -> None: ...
