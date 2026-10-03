"""A throwaway identity database for integration tests (``make test-integration``):
created and migrated with the owner role, used through ``tarn_auth``, dropped at the end."""

from uuid import uuid4

import psycopg
from psycopg import sql

from tarn_adapters.identity import migrate
from tarn_adapters.postgres.database import libpq_url
from tarn_adapters.postgres.testing import TestDatabase, drop_test_database, with_database


def create_test_identity_database(owner_url: str, app_url: str) -> TestDatabase:
    name = f"tarn_identity_test_{uuid4().hex[:12]}"
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    db = TestDatabase(
        name=name, owner_url=with_database(owner_url, name), app_url=with_database(app_url, name)
    )
    migrate.upgrade(db.owner_url)
    migrate.grant_app_login(db.owner_url, db.app_url)
    return db


__all__ = ["create_test_identity_database", "drop_test_database"]
