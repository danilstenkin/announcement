"""
One-shot prod reconcile + stamp.

Background: prod was migrated from a different alembic history (its
alembic_version points to a revision absent from this repo), so plain
`alembic upgrade head` cannot run there — it would also collide with the tables
that already exist. Prod's existing tables already match the current models;
only some objects are missing.

This script brings the prod schema up to the current models WITHOUT touching
existing data, then re-points alembic to this repo's history:

  1. Base.metadata.create_all() — creates the MISSING tables + enum types only
     (checkfirst skips everything that already exists).
  2. ADD COLUMN IF NOT EXISTS for the few columns missing on existing tables.
  3. `alembic stamp head --purge` — overwrites the foreign alembic_version with
     this repo's head, so future deploys run `alembic upgrade head` as a no-op.

Usage (run from the repo root). Pass the TARGET database URL explicitly so you
never hit the wrong DB by accident:

    python scripts/reconcile_prod.py "postgresql+asyncpg://user:pass@host:5432/dbname"

  (a plain postgresql://... URL is accepted too; the driver is normalized.)
  Alternatively set the DATABASE_URL env var and run without an argument.
"""
import asyncio
import os
import sys
from pathlib import Path

# Make the app's rootless packages (models, ...) importable, same as env.py.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "app"))

SCHEMA = "cchub_announcements"

# Some prod enums were created in `public` by the old migration history, while
# the models expect them in cchub_announcements. Relocate them (columns using
# them follow automatically) BEFORE create_all, so create_all skips them instead
# of creating confusing duplicates. Idempotent: only moves when still in public
# and not yet present in the target schema.
RELOCATE_ENUMS = [
    "announcementcategoryenum",
    "emailstatusenum",
]

# Pre-existing enums came from the old migration history and may be missing
# values the current code uses (prod's emailstatusenum lacked DONE). Bring them
# up to the full set the models define. ADD VALUE IF NOT EXISTS is idempotent.
SYNC_ENUM_VALUES = {
    "emailstatusenum": ["PROCESSING", "GREEN", "RED", "YELLOW", "GRAY", "DONE"],
    "announcementcategoryenum": ["TECH_QUESTION", "CHANGE", "NEW", "REVOKED"],
}

def _resolve_url() -> str:
    raw = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("DATABASE_URL")
    if not raw:
        sys.exit(
            "ERROR: target DB URL required.\n"
            '  python scripts/reconcile_prod.py "postgresql+asyncpg://user:pass@host:5432/db"\n'
            "  (or set DATABASE_URL)"
        )
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql://", "postgres://"):
        if raw.startswith(prefix):
            return "postgresql+asyncpg://" + raw[len(prefix):]
    sys.exit(f"ERROR: unrecognized DB URL scheme: {raw.split('://')[0]}://")


async def _apply_schema(url: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from models.base import Base
    import models  # noqa: F401 — register all mappers

    engine = create_async_engine(url, future=True)
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        for enum_name in RELOCATE_ENUMS:
            await conn.execute(text(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                               WHERE n.nspname = 'public' AND t.typname = '{enum_name}')
                       AND NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                                       WHERE n.nspname = '{SCHEMA}' AND t.typname = '{enum_name}')
                    THEN
                        ALTER TYPE public.{enum_name} SET SCHEMA {SCHEMA};
                    END IF;
                END$$;
                """
            ))
        await conn.run_sync(Base.metadata.create_all)  # missing tables/enums only
    await engine.dispose()


async def _sync_columns(url: str) -> None:
    """Add any column the models define but an EXISTING table lacks. create_all
    skips tables that already exist, so a table left over from an older version
    of the app misses newer columns (e.g. email_attachments.is_inline,
    review_tickets transfer fields). Only adds nullable / server-defaulted
    columns; a NOT NULL column without a server default can't be backfilled, so
    it's reported and skipped."""
    import asyncpg
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateColumn
    from models.base import Base
    import models  # noqa: F401 — register all mappers

    dialect = postgresql.dialect()
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        for table in Base.metadata.sorted_tables:
            exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=$1 AND table_name=$2",
                SCHEMA, table.name,
            )
            if not exists:
                continue  # brand-new tables are created complete by create_all
            present = {r["column_name"] for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name=$2",
                SCHEMA, table.name,
            )}
            for col in table.columns:
                if col.name in present:
                    continue
                if not col.nullable and col.server_default is None:
                    print(f"     SKIP {table.name}.{col.name} (NOT NULL без server default)")
                    continue
                col_ddl = str(CreateColumn(col).compile(dialect=dialect)).strip()
                await conn.execute(
                    f'ALTER TABLE {SCHEMA}."{table.name}" ADD COLUMN IF NOT EXISTS {col_ddl}'
                )
                print(f"     + {table.name}.{col.name}")
    finally:
        await conn.close()


async def _sync_enum_values(url: str) -> None:
    """Add enum values the models define but prod's (foreign-history) enums lack.
    Run via plain asyncpg in autocommit — ALTER TYPE ADD VALUE cannot run inside a
    transaction block on older PostgreSQL."""
    import asyncpg

    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        for enum_name, values in SYNC_ENUM_VALUES.items():
            for v in values:
                await conn.execute(
                    f"ALTER TYPE {SCHEMA}.{enum_name} ADD VALUE IF NOT EXISTS '{v}'"
                )
    finally:
        await conn.close()


def _head_revision() -> str:
    """Read this repo's head revision from the migration files (no DB needed)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()  # no alembic.ini → no configparser interpolation on the URL
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


async def _stamp_head(url: str) -> None:
    """Equivalent of `alembic stamp head --purge`, written directly so we don't
    push the DB URL through alembic's configparser (which treats % specially)."""
    import asyncpg

    head = _head_revision()
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"CREATE TABLE IF NOT EXISTS {SCHEMA}.alembic_version "
            f"(version_num varchar(32) NOT NULL)"
        )
        await conn.execute(f"DELETE FROM {SCHEMA}.alembic_version")
        await conn.execute(
            f"INSERT INTO {SCHEMA}.alembic_version (version_num) VALUES ($1)", head
        )
    finally:
        await conn.close()
    print(f"     alembic_version set to {head}")


def main() -> None:
    url = _resolve_url()
    target = url.split("@")[-1]  # host:port/db, without credentials
    print(f"Target DB: {target}")
    print("1/2  Creating missing tables, syncing columns + enum values (existing data untouched)...")
    asyncio.run(_apply_schema(url))
    asyncio.run(_sync_columns(url))
    asyncio.run(_sync_enum_values(url))
    print("2/2  Stamping alembic to head...")
    asyncio.run(_stamp_head(url))
    print("Done. DB is reconciled and on this repo's migration history.")


if __name__ == "__main__":
    main()
