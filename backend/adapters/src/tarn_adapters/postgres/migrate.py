"""Schema migrations and the application role's login, run with the owner role's URL."""

from pathlib import Path
from urllib.parse import unquote

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from tarn_adapters.postgres.database import libpq_url

APP_ROLE = "tarn_app"
_SCRIPTS = Path(__file__).with_name("migrations")


def _config(owner_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(_SCRIPTS))
    # ConfigParser interpolation: a literal % in a password must be doubled.
    config.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    return config


def upgrade(owner_url: str, revision: str = "head") -> None:
    command.upgrade(_config(owner_url), revision)


def downgrade(owner_url: str, revision: str = "base") -> None:
    command.downgrade(_config(owner_url), revision)


def current_revision(owner_url: str) -> str | None:
    with psycopg.connect(libpq_url(owner_url)) as conn:
        exists = conn.execute("SELECT to_regclass('alembic_version') IS NOT NULL").fetchone()
        if not exists or not exists[0]:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return str(row[0]) if row else None


def grant_app_login(owner_url: str, app_url: str) -> None:
    """Let ``tarn_app`` log in with the password in ``app_url`` (the role itself is created,
    without login, by the first migration). Production sets this through its secret store."""
    app = make_url(app_url)
    if app.username != APP_ROLE:
        raise ValueError(f"the application database URL must log in as {APP_ROLE}")
    if not app.password:
        raise ValueError("the application database URL has no password")
    password = unquote(str(app.password))
    with psycopg.connect(libpq_url(owner_url), autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(password)
            )
        )
