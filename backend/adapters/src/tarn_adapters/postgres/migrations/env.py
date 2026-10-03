"""Alembic environment. Run through ``tarn_adapters.postgres.migrate`` (``tarn db upgrade``),
which passes the owner database URL; there is no alembic.ini."""

from alembic import context
from sqlalchemy import create_engine, pool

from tarn_adapters.postgres.database import sqlalchemy_url

config = context.config
url = config.get_main_option("sqlalchemy.url")
if url is None:
    raise RuntimeError("run migrations through tarn_adapters.postgres.migrate")

engine = create_engine(sqlalchemy_url(url), poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()
