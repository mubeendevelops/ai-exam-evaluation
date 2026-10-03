"""Fixtures for tests against the dockerised PostgreSQL (``make up``, then
``make test-integration``).

One throwaway database (``tarn_test_<hex>``) per test run: created and migrated with the
owner role, used through the application role ``tarn_app``, dropped at the end. Development
data is never touched. Every test seeds its own two colleges with random ids, so tests do
not see each other's college data."""

from collections.abc import Iterator

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.identity.database import IdentityDatabase
from tarn_adapters.identity.testing import create_test_identity_database
from tarn_adapters.postgres.database import PostgresDatabase
from tarn_adapters.postgres.jobs import JobSettings
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


@pytest.fixture(scope="session")
def identity_test_database() -> Iterator[TestDatabase]:
    """A throwaway identity database (P4), used through ``tarn_auth``."""
    settings = Settings()
    db = create_test_identity_database(settings.database_url, settings.identity_app_database_url)
    try:
        yield db
    finally:
        drop_test_database(settings.database_url, db.name)


@pytest.fixture(scope="session")
def identity_db(identity_test_database: TestDatabase) -> Iterator[IdentityDatabase]:
    db = IdentityDatabase(identity_test_database.app_url)
    yield db
    db.dispose()


@pytest.fixture
def fresh() -> Iterator[tuple[TestDatabase, PostgresDatabase]]:
    """A database of its own: the job queue is global (a claim takes any college's job), so a
    database shared with other tests would hand out their jobs."""
    settings = Settings()
    test_db = create_test_database(settings.database_url, settings.app_database_url)
    db = PostgresDatabase(
        test_db.app_url,
        job_settings=JobSettings(
            max_attempts=3, lease_seconds=60, backoff_seconds=5, max_running=1
        ),
    )
    try:
        yield test_db, db
    finally:
        db.dispose()
        drop_test_database(settings.database_url, test_db.name)
