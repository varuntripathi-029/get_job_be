"""Alembic environment — async engine, config from app.config."""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app import db_url
from app.config import settings

# Importing app.models registers every table on Base.metadata. Without this,
# autogenerate silently produces an empty migration.
from app.models import Base  # noqa: F401

config = context.config

# Same libpq -> asyncpg translation the app engine applies, so a hosted URL that
# works at runtime also works for migrations.
DATABASE_URL, CONNECT_ARGS = db_url.normalize(settings.database_url)
config.set_main_option("sqlalchemy.url", DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Postgres rewrites expression indexes when it stores them: 'english' becomes
# 'english'::regconfig and string operands pick up ::text casts. Alembic
# compares the reflected text against the model's raw SQL, so ix_jobs_fts is
# reported as changed on every run even when nothing changed — which makes
# `alembic check` permanently red and every autogenerate emit a spurious
# drop/create. Comparison is skipped for it; the index is defined once in
# 0001_initial and any real change to it should be written by hand.
_UNCOMPARABLE_INDEXES = {"ix_jobs_fts"}


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "index" and name in _UNCOMPARABLE_INDEXES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # jobs.embedding / resumes.embedding need pgvector before any table is built.
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    connection.commit()

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # async_engine_from_config reads only the URL, so anything that cannot
        # be expressed there has to be handed over explicitly.
        connect_args=CONNECT_ARGS,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
