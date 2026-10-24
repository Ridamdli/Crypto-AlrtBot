"""Alembic environment.

The URL comes from DATABASE_URL (via src.db.engine.database_url(), which pins
the psycopg3 driver), falling back to `sqlalchemy.url` in alembic.ini. That
keeps one source of truth: the same env var the app uses.
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

from src.db import models  # noqa: F401  (registers tables on Base.metadata)
from src.db.base import Base
from src.db.engine import database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    url = database_url() or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "Set DATABASE_URL (or sqlalchemy.url in alembic.ini) before running Alembic"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
