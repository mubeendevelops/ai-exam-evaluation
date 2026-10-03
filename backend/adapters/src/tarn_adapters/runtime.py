"""Production clock and id generator."""

from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    """Random (version 4) UUIDs."""

    def new(self) -> UUID:
        return uuid4()
