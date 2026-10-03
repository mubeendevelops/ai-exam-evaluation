"""Fixtures for tests against the dockerised PostgreSQL (``make up``, then
``make test-integration``).

One throwaway database (``tarn_test_<hex>``) per test run: created and migrated with the
owner role, used through the application role ``tarn_app``, dropped at the end. Development
data is never touched. Every test seeds its own two colleges with random ids, so tests do
not see each other's college data."""

from collections.abc import Iterator

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.testing import (
    Opener,
    TestDatabase,
    World,
    create_test_database,
    drop_test_database,
    seed_world,
)


@pytest.fixture(scope="session")
def test_database() -> Iterator[TestDatabase]:
    settings = Settings()
    db = create_test_database(settings.database_url, settings.app_database_url)
    try:
        yield db
    finally:
        drop_test_database(settings.database_url, db.name)


@pytest.fixture(scope="session")
def database(test_database: TestDatabase) -> Iterator[PostgresDatabase]:
    db = PostgresDatabase(test_database.app_url)
    yield db
    db.dispose()


@pytest.fixture
def session(database: PostgresDatabase) -> Opener:
    return Opener(database)


@pytest.fixture
def world(session: Opener) -> World:
    return seed_world(session)
