"""Alembic environment.

Migrations run against ``system_database_url`` — the role that owns the tables
and may create row level security policies.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.models import Base  # noqa: F401 - registers every table

config = context.config
config.set_main_option("sqlalchemy.url", settings.effective_system_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

#: Tables that exist in the database but deliberately have no ORM model.
#: ``document_embedding`` holds a pgvector column, and SQLAlchemy has no
#: built-in type for it, so migration 0003 manages it in raw SQL. Without this
#: filter, autogenerate would propose dropping it on every run.
UNMANAGED_TABLES = {"document_embedding"}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    if type_ == "table" and name in UNMANAGED_TABLES:
        return False
    if type_ == "index" and getattr(obj, "table", None) is not None:
        return obj.table.name not in UNMANAGED_TABLES
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
