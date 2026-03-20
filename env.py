"""
Alembic environment configuration.
"""
 
from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
import os
import sys
 
# Add parent directory to path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
 
from app.db import Base
from app.models import Announcement, AnnouncementReadStatus
from app.config import settings
 
# this is the Alembic Config object, which provides the values of the
# [alembic] section of the .ini file, and can be accessed via the
# config.get_section() method. We set the sqlalchemy.url here so that
# we can use the DATABASE_URL from our settings.
 
config = context.config
 
# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
 
# set the sqlalchemy.url value from our settings
# Convert asyncpg to psycopg for Alembic (which needs synchronous driver)
database_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg")
config.set_main_option("sqlalchemy.url", database_url)
 
# Model's MetaData object for 'autogenerate' support
target_metadata = Base.metadata
 
 
def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.
 
    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.
 
    Calls to context.execute() here emit the given string to the
    script output.
 
    """
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
    """
    Run migrations in 'online' mode.
 
    In this scenario we need to create an Engine
    and associate a connection with the context.
 
    """
    configuration = config.get_section(config.config_ini_section)
    # Convert asyncpg to psycopg for Alembic (synchronous)
    database_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg")
    configuration["sqlalchemy.url"] = database_url
   
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
 
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
 
        with context.begin_transaction():
            context.run_migrations()
 
 
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()