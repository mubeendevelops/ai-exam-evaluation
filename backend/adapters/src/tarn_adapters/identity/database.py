"""Connections to the identity database. Connect with ``tarn_auth``
(``TARN_IDENTITY_APP_DATABASE_URL``), never the owner."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine

from tarn_adapters.identity.store import PgIdentityStore
from tarn_adapters.postgres.database import sqlalchemy_url


class IdentityDatabase:
    def __init__(self, url: str, *, pool_size: int = 5) -> None:
        self.engine: Engine = create_engine(
            sqlalchemy_url(url), pool_size=pool_size, pool_pre_ping=True
        )

    @contextmanager
    def session(self) -> Iterator[PgIdentityStore]:
        """One transaction; commits on success, rolls back on any exception. The first
        per-tenant call binds it to that college."""
        with self.engine.begin() as conn:
            yield PgIdentityStore(conn)

    def dispose(self) -> None:
        self.engine.dispose()
