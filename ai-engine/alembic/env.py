"""Alembic environment for the postgres-meta schema.

The DB URL is assembled from the same POSTGRES_META_* environment variables the
services already use (see docker-compose.yml). We read them straight from the
environment rather than importing the app's Settings, so the one-shot
`db-migrate` container stays decoupled from the rest of the app config (it needs
no OpenAI key, Trino host, etc. just to create tables).

Migrations are hand-written raw SQL (op.execute), so there is no model metadata
to autogenerate against — target_metadata stays None.
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    host = os.getenv("POSTGRES_META_HOST", "localhost")
    port = os.getenv("POSTGRES_META_PORT", "5432")
    db = os.getenv("POSTGRES_META_DB", "analytics_meta")
    user = os.getenv("POSTGRES_META_USER", "meta_user")
    password = os.getenv("POSTGRES_META_PASSWORD", "meta_pass_2024")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


target_metadata = None


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
