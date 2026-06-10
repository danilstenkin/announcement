from logging.config import fileConfig
import os
import sys

from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "app"),
)

from models.base import Base
import models  # noqa: F401


MANAGED_SCHEMA = "cchub_announcements"

target_metadata = Base.metadata

db_url = os.environ.get("DATABASE_URL")
if db_url:
    db_url = db_url.replace("+asyncpg", "+psycopg2")
    # set_main_option stores into configparser, which treats % as interpolation
    # syntax — URL-encoded passwords (e.g. %23 for #) would raise. Escape to %%;
    # configparser un-escapes back to a single % when the value is read.
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == MANAGED_SCHEMA

    if type_ == "table":
        return parent_names.get("schema_name") == MANAGED_SCHEMA

    return True


def include_object(object, name, type_, reflected, compare_to):
      # Не удаляем таблицы, которые уже есть в БД, но не описаны в SQLAlchemy models.
      # Это защищает чужие таблицы вроде chat_messages, messages_feedback и view-зависимости.
    if type_ == "table" and reflected and compare_to is None:
        return False

    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema=MANAGED_SCHEMA,
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
        # Alembic stores its version table in MANAGED_SCHEMA, so the schema must
        # exist before migrations (and the version table) are created.
        connection.execute(
            text(f"CREATE SCHEMA IF NOT EXISTS {MANAGED_SCHEMA}")
        )
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=MANAGED_SCHEMA,
            include_schemas=True,
            include_name=include_name,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
