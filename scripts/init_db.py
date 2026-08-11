"""建库脚本：CREATE EXTENSION vector + 跑 sql/schema.sql。幂等（全部 IF NOT EXISTS）。

用法：
    python scripts/init_db.py
"""
import asyncio
from pathlib import Path

from app import db

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


async def main() -> None:
    sql_text = SCHEMA_FILE.read_text(encoding="utf-8")
    pool = await db.get_pool()
    async with pool.connection() as conn:
        await conn.execute(sql_text)
    print(f"schema applied from {SCHEMA_FILE}")

    print("\n-- 表清单 --")
    tables = await db.fetch_all(
        "SELECT table_name FROM information_schema.tables"
        " WHERE table_schema = 'public' ORDER BY table_name"
    )
    for t in tables:
        print(f"  {t['table_name']}")

    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
