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

# Columns present in the models but missing on prod's existing tables.
# All nullable / defaulted, so adding them is safe for existing rows.
ADD_COLUMNS = [
    f"ALTER TABLE {SCHEMA}.announcements   ADD COLUMN IF NOT EXISTS is_ai boolean DEFAULT false",
    f"ALTER TABLE {SCHEMA}.announcements   ADD COLUMN IF NOT EXISTS source varchar(50)",
    f"ALTER TABLE {SCHEMA}.announcements   ADD COLUMN IF NOT EXISTS published_at timestamptz",
    f"ALTER TABLE {SCHEMA}.incoming_emails ADD COLUMN IF NOT EXISTS original_html_key varchar(500)",
]


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
        for stmt in ADD_COLUMNS:
            await conn.execute(text(stmt))
    await engine.dispose()


def _stamp_head(url: str) -> None:
    from alembic.config import Config
    from alembic import command

    os.environ["DATABASE_URL"] = url  # env.py reads this (converts to psycopg2 itself)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.stamp(cfg, "head", purge=True)


def main() -> None:
    url = _resolve_url()
    target = url.split("@")[-1]  # host:port/db, without credentials
    print(f"Target DB: {target}")
    print("1/2  Creating missing tables/columns (existing data untouched)...")
    asyncio.run(_apply_schema(url))
    print("2/2  Stamping alembic to head...")
    _stamp_head(url)
    print("Done. DB is reconciled and on this repo's migration history.")


if __name__ == "__main__":
    main()
