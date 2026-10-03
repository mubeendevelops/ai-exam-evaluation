"""Create and migrate the identity database, and give ``tarn_auth`` its login. Uses the
identity database's owner URL (``TARN_IDENTITY_DATABASE_URL``)."""

from pathlib import Path
from urllib.parse import unquote

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from tarn_adapters.postgres.database import libpq_url

APP_ROLE = "tarn_auth"
_SCRIPTS = Path(__file__).with_name("migrations")


def _config(owner_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_SCRIPTS))
    config.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    return config


def ensure_database(owner_url: str) -> bool:
    """Create the database named in ``owner_url`` if it is missing (development: the dev
    owner is a superuser). Returns True if it was created."""
    url = make_url(owner_url)
    name = url.database
    if not name:
        raise ValueError("the identity database URL names no database")
    admin = url.set(database="postgres")
    with psycopg.connect(
        libpq_url(admin.render_as_string(hide_password=False)), autocommit=True
    ) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists:
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return True


def upgrade(owner_url: str, revision: str = "head") -> None:
    command.upgrade(_config(owner_url), revision)


def downgrade(owner_url: str, revision: str = "base") -> None:
    command.downgrade(_config(owner_url), revision)


def current_revision(owner_url: str) -> str | None:
    with psycopg.connect(libpq_url(owner_url)) as conn:
        found = conn.execute(
            "SELECT to_regclass('identity_alembic_version') IS NOT NULL"
        ).fetchone()
        if not found or not found[0]:
            return None
        row = conn.execute("SELECT version_num FROM identity_alembic_version").fetchone()
    return str(row[0]) if row else None


def grant_app_login(owner_url: str, app_url: str) -> None:
    app = make_url(app_url)
    if app.username != APP_ROLE:
        raise ValueError(f"the identity application URL must log in as {APP_ROLE}")
    if not app.password:
        raise ValueError("the identity application URL has no password")
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(unquote(str(app.password)))
            )
        )
