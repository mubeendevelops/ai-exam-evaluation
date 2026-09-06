"""db.py — Postgres connection helper, reads standard PG* env vars.

get_connection() is a stateless factory (no pooling, no module-level shared
state), so it's safe to call from multiple concurrent callers (e.g. FastAPI
request handlers) — each call/close is fully independent. There's no
connection pooling yet; revisit with a pool (or an async engine) if/when
per-request connection overhead becomes a measured problem.
"""
import contextlib
import os
from typing import Iterator

import psycopg2
import psycopg2.extensions


def get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "ai_evaluation"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )


def get_admin_connection() -> psycopg2.extensions.connection:
    """Owner-role connection, for schema work only (migrations, scripts/migrate.py,
    pg_dump, scripts/reset_and_seed_db.sh) — never for application reads/writes.

    Reads PGADMIN_USER/PGADMIN_PASSWORD, falling back to PGUSER/PGPASSWORD
    when those are unset (a disposable container where PGUSER already IS the
    owner, before migration 016 has created the non-superuser application
    role). This connection bypasses RLS (§6) by virtue of the role, not by
    any GUC this module sets.
    """
    return psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "ai_evaluation"),
        user=os.environ.get("PGADMIN_USER") or os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGADMIN_PASSWORD") or os.environ.get("PGPASSWORD", ""),
    )


@contextlib.contextmanager
def transaction(dry_run: bool = False) -> Iterator[psycopg2.extensions.cursor]:
    """Opens a fresh connection, yields a cursor for the caller to run
    queries against, then commits (or rolls back, if dry_run=True) on clean
    exit, rolls back and re-raises on any exception, and always closes the
    connection.

    Replaces the `conn = get_connection(); try: ... finally: conn.close()`
    boilerplate every write script previously repeated by hand. The caller
    still owns any result printing / error messaging around the `with`
    block — this only owns the connection lifecycle.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            yield cur
    except Exception:
        conn.rollback()
        conn.close()
        raise
    else:
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        conn.close()
