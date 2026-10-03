"""Connectivity check used by ``tarn doctor``."""

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class DatabaseStatus:
    server_version: str
    pgvector_version: str | None


def check_database(dsn: str, *, timeout_s: int = 5) -> DatabaseStatus:
    with psycopg.connect(dsn, connect_timeout=timeout_s) as conn:
        row = conn.execute("SHOW server_version").fetchone()
        vec = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
    return DatabaseStatus(
        server_version=str(row[0]) if row else "unknown",
        pgvector_version=str(vec[0]) if vec else None,
    )
