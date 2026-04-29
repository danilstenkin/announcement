from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool
import sqlalchemy as sa  # Нужен для sa.text() — выполнение raw SQL

from alembic import context

# Объект конфигурации Alembic — читает настройки из alembic.ini
config = context.config

# Настройка логирования из alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Добавляем путь к моделям в sys.path, чтобы Alembic мог их импортировать
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "app"))
from models.base import Base

# target_metadata — Alembic сравнивает метаданные моделей с текущим состоянием БД
# и на основе разницы генерирует миграции (autogenerate)
target_metadata = Base.metadata

# Если задана переменная окружения DATABASE_URL — используем её вместо alembic.ini
# Заменяем asyncpg на psycopg2, т.к. Alembic работает синхронно
db_url = os.environ.get("DATABASE_URL")
if db_url:
    db_url = db_url.replace("+asyncpg", "+psycopg2")
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    """Офлайн-режим: генерирует SQL без подключения к БД.
    Полезно для ревью миграций или применения вручную."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Онлайн-режим: подключается к БД и применяет миграции напрямую."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Создаём схему если её нет — без этого CREATE TABLE упадёт,
        # т.к. PostgreSQL не создаёт схемы автоматически
        # connection.execute(sa.text("CREATE SCHEMA IF NOT EXISTS cchub_announcements"))
        # connection.commit()

        # Связываем соединение с метаданными моделей
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="cchub_announcements",
        )

        # Выполняем все миграции внутри транзакции
        with context.begin_transaction():
            context.run_migrations()


# Выбираем режим запуска — офлайн или онлайн
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
