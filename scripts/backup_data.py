"""
Local data backup — extra safety before running reconcile_prod.py.

Dumps every table in the cchub_announcements schema to a local CSV file
(one file per table, with a header row). This is a human-readable, eyeball-able
copy in addition to `pg_dump`. It does NOT modify the database in any way —
read-only.

Usage (from repo root):
    python scripts/backup_data.py "postgresql://user:pass@host:5432/dbname"
  (or set DATABASE_URL and run without an argument)

Output: ./db_backup_<YYYYmmdd_HHMMSS>/<table>.csv  +  _summary.txt with row counts.
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

SCHEMA = "cchub_announcements"


def _resolve_dsn() -> str:
    """asyncpg wants a plain postgres DSN (no +asyncpg / +psycopg2 driver tag)."""
    raw = (sys.argv[1] if len(sys.argv) > 1 else None) or os.environ.get("DATABASE_URL")
    if not raw:
        sys.exit(
            "ERROR: target DB URL required.\n"
            '  python scripts/backup_data.py "postgresql://user:pass@host:5432/db"\n'
            "  (or set DATABASE_URL)"
        )
    return (
        raw.replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgres+asyncpg://", "postgresql://")
    )


async def main() -> None:
    import asyncpg

    dsn = _resolve_dsn()
    out_dir = Path.cwd() / f"db_backup_{datetime.now():%Y%m%d_%H%M%S}"
    out_dir.mkdir()

    conn = await asyncpg.connect(dsn)
    try:
        tables = [r["table_name"] for r in await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = $1 ORDER BY table_name", SCHEMA,
        )]
        if not tables:
            sys.exit(f"No tables found in schema {SCHEMA}. Wrong DB?")

        summary = []
        for t in tables:
            n = await conn.fetchval(f'SELECT count(*) FROM {SCHEMA}."{t}"')
            csv_path = out_dir / f"{t}.csv"
            # COPY ... TO with CSV+header: faithful, restorable, readable.
            await conn.copy_from_query(
                f'SELECT * FROM {SCHEMA}."{t}"',
                output=str(csv_path),
                format="csv",
                header=True,
            )
            print(f"  {t}: {n} rows -> {csv_path.name}")
            summary.append(f"{t}\t{n}")

        (out_dir / "_summary.txt").write_text(
            f"DB backup {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"schema: {SCHEMA}\n\ntable\trows\n" + "\n".join(summary) + "\n",
            encoding="utf-8",
        )
    finally:
        await conn.close()

    print(f"\nDone. Local backup saved to: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
