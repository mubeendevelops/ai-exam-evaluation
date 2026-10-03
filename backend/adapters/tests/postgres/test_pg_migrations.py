"""Migrations run up and down, and the query tables match the migrated schema."""

import pytest

from tarn_adapters.config import Settings
from tarn_adapters.postgres import metadata as m
from tarn_adapters.postgres import migrate
from tarn_adapters.postgres.testing import TestDatabase, create_test_database, drop_test_database

pytestmark = pytest.mark.integration

_TABLES = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
)


def test_upgrade_downgrade_upgrade() -> None:
    settings = Settings()
    db = create_test_database(settings.database_url, settings.app_database_url)
    try:
        assert migrate.current_revision(db.owner_url) == "0003"
        migrate.downgrade(db.owner_url, "base")
        assert migrate.current_revision(db.owner_url) is None
        with db.owner() as conn:
            assert conn.execute(_TABLES).fetchall() == []
            functions = conn.execute(
                "SELECT count(*) FROM pg_proc WHERE proname LIKE 'tarn\\_%'"
            ).fetchone()
            assert functions == (0,)
            # The role is cluster-wide and stays.
            assert conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'tarn_app'").fetchone()
        migrate.upgrade(db.owner_url)
        assert migrate.current_revision(db.owner_url) == "0003"
        with db.owner() as conn:
            names = {r[0] for r in conn.execute(_TABLES).fetchall()}
        assert names == set(m.metadata.tables)
    finally:
        drop_test_database(settings.database_url, db.name)


def test_metadata_matches_migrated_schema(test_database: TestDatabase) -> None:
    with test_database.owner() as conn:
        rows = conn.execute(
            "SELECT table_name, column_name, is_nullable = 'YES' FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name <> 'alembic_version'"
        ).fetchall()
    in_db = {(str(t), str(c), bool(n)) for t, c, n in rows}
    in_metadata = {
        (table.name, column.name, bool(column.nullable))
        for table in m.metadata.tables.values()
        for column in table.columns
    }
    assert in_db == in_metadata
