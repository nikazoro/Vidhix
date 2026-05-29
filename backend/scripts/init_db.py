"""
Initialize the LexAra PostgreSQL database.

Usage:
    python -m scripts.init_db
"""
import asyncio
import sys
from pathlib import Path

# Ensure backend/ is on the path when run as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog
from sqlalchemy import inspect, text

from database import async_engine, init_db

log = structlog.get_logger(__name__)


async def main() -> None:
    print("LexAra — Initializing database...")
    print(f"Engine URL: {async_engine.url!r}\n")

    try:
        await init_db()
    except Exception as exc:
        print(f"❌ Database initialization failed: {exc}")
        sys.exit(1)

    # Report tables created
    async with async_engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )

    print(f"✅ Database initialized. Tables ({len(tables)}):")
    for t in sorted(tables):
        print(f"   • {t}")

    await async_engine.dispose()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())